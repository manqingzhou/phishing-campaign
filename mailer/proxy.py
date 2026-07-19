#!/usr/bin/env python3
"""TCP tunneling through a local proxy for the mailer's SMTP connection.

Standard library only — no PySocks / third-party deps. Supports two proxy
styles, selected by the URL scheme in ``SMTP_PROXY``:

    socks5://host:port      SOCKS5, hostname resolved by THIS machine
    socks5h://host:port     SOCKS5, hostname resolved by the PROXY (default)
    http://host:port        HTTP CONNECT tunnel (a.k.a. "Web Proxy")
    https://host:port       HTTP CONNECT over a TLS link to the proxy

Optional auth is taken from the URL userinfo, e.g.
``socks5://user:pass@127.0.0.1:15236``. A bare ``host:port`` with no scheme is
treated as ``socks5h://`` since that is the most robust for tunnelling raw TCP.

The public entrypoint is :func:`Proxy.open`, which returns a connected socket
whose far end is the destination SMTP server — smtplib then speaks over it as
if it had dialled the server directly.
"""

from __future__ import annotations

import socket
import ssl
import struct
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass
class Proxy:
    scheme: str  # "socks5", "socks5h", "http", or "https"
    host: str
    port: int
    username: str = ""
    password: str = ""

    @classmethod
    def from_url(cls, url: str) -> "Proxy | None":
        """Parse a proxy URL. Returns None for an empty/blank value."""
        if not url or not url.strip():
            return None
        raw = url.strip()
        # Allow a bare host:port (no scheme) -> default to socks5h.
        if "://" not in raw:
            raw = "socks5h://" + raw
        parts = urlsplit(raw)
        scheme = (parts.scheme or "socks5h").lower()
        if scheme not in {"socks5", "socks5h", "http", "https"}:
            raise ValueError(
                f"Unsupported SMTP_PROXY scheme {scheme!r} "
                "(use socks5, socks5h, http, or https)"
            )
        if not parts.hostname:
            raise ValueError(f"SMTP_PROXY {url!r} is missing a host")
        default_port = 1080 if scheme.startswith("socks5") else 8080
        return cls(
            scheme=scheme,
            host=parts.hostname,
            port=parts.port or default_port,
            username=parts.username or "",
            password=parts.password or "",
        )

    def describe(self) -> str:
        auth = f"{self.username}@" if self.username else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def open(self, dest_host: str, dest_port: int, timeout: float) -> socket.socket:
        """Open a tunnel to ``dest_host:dest_port`` through this proxy."""
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        try:
            if self.scheme in {"socks5", "socks5h"}:
                remote_dns = self.scheme == "socks5h"
                self._socks5_handshake(sock, dest_host, dest_port, remote_dns)
            else:
                if self.scheme == "https":
                    context = ssl.create_default_context()
                    sock = context.wrap_socket(sock, server_hostname=self.host)
                self._http_connect(sock, dest_host, dest_port)
        except Exception:
            sock.close()
            raise
        return sock

    # -- SOCKS5 (RFC 1928 / 1929) -------------------------------------------

    def _socks5_handshake(
        self, sock: socket.socket, dest_host: str, dest_port: int, remote_dns: bool
    ) -> None:
        # Greeting: offer no-auth (0x00) and, if we have creds, user/pass (0x02).
        methods = b"\x00"
        if self.username:
            methods += b"\x02"
        sock.sendall(b"\x05" + bytes([len(methods)]) + methods)
        ver, method = _recv_exact(sock, 2)
        if ver != 0x05:
            raise OSError(f"SOCKS5: bad version byte {ver:#x} from proxy")
        if method == 0xFF:
            raise OSError("SOCKS5: proxy rejected all offered auth methods")
        if method == 0x02:
            self._socks5_userpass_auth(sock)
        elif method != 0x00:
            raise OSError(f"SOCKS5: proxy chose unsupported auth method {method:#x}")

        # CONNECT request.
        if remote_dns:
            host_bytes = dest_host.encode("idna")
            addr = b"\x03" + bytes([len(host_bytes)]) + host_bytes
        else:
            packed = _pack_ip(dest_host)
            addr = packed if packed else (
                b"\x03" + bytes([len(dest_host.encode("idna"))])
                + dest_host.encode("idna")
            )
        request = b"\x05\x01\x00" + addr + struct.pack("!H", dest_port)
        sock.sendall(request)

        ver, rep, _rsv, atyp = _recv_exact(sock, 4)
        if ver != 0x05:
            raise OSError(f"SOCKS5: bad reply version {ver:#x}")
        if rep != 0x00:
            raise OSError(f"SOCKS5: CONNECT failed ({_SOCKS5_ERRORS.get(rep, rep)})")
        # Drain the bound address so the socket is left at the start of data.
        if atyp == 0x01:  # IPv4
            _recv_exact(sock, 4)
        elif atyp == 0x04:  # IPv6
            _recv_exact(sock, 16)
        elif atyp == 0x03:  # domain
            length = _recv_exact(sock, 1)[0]
            _recv_exact(sock, length)
        else:
            raise OSError(f"SOCKS5: unknown bound-address type {atyp:#x}")
        _recv_exact(sock, 2)  # bound port

    def _socks5_userpass_auth(self, sock: socket.socket) -> None:
        user = self.username.encode("utf-8")
        pw = self.password.encode("utf-8")
        if len(user) > 255 or len(pw) > 255:
            raise OSError("SOCKS5: username/password too long (max 255 bytes)")
        sock.sendall(
            b"\x01" + bytes([len(user)]) + user + bytes([len(pw)]) + pw
        )
        ver, status = _recv_exact(sock, 2)
        if ver != 0x01 or status != 0x00:
            raise OSError("SOCKS5: username/password authentication failed")

    # -- HTTP CONNECT --------------------------------------------------------

    def _http_connect(
        self, sock: socket.socket, dest_host: str, dest_port: int
    ) -> None:
        target = f"{dest_host}:{dest_port}"
        lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
        if self.username:
            import base64

            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            lines.append(f"Proxy-Authorization: Basic {token}")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
        sock.sendall(request)

        # Read the response headers ONE byte at a time so we never consume
        # bytes past "\r\n\r\n" — anything after belongs to the tunnelled SMTP
        # stream (e.g. the server's 220 banner) and must be left in the socket.
        response = b""
        while not response.endswith(b"\r\n\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                raise OSError("HTTP proxy closed the connection during CONNECT")
            response += chunk
            if len(response) > 65536:
                raise OSError("HTTP proxy sent an oversized CONNECT response")
        status_line = response.split(b"\r\n", 1)[0].decode("latin-1")
        fields = status_line.split(None, 2)
        if len(fields) < 2 or fields[1] != "200":
            raise OSError(f"HTTP proxy CONNECT failed: {status_line!r}")


_SOCKS5_ERRORS = {
    0x01: "general SOCKS server failure",
    0x02: "connection not allowed by ruleset",
    0x03: "network unreachable",
    0x04: "host unreachable",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
}


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes or raise if the peer closes early."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("proxy closed the connection unexpectedly")
        buf += chunk
    return buf


def _pack_ip(host: str) -> bytes | None:
    """Return the SOCKS5 ATYP+addr bytes if ``host`` is a literal IP, else None."""
    try:
        return b"\x01" + socket.inet_pton(socket.AF_INET, host)
    except OSError:
        pass
    try:
        return b"\x04" + socket.inet_pton(socket.AF_INET6, host)
    except OSError:
        return None
