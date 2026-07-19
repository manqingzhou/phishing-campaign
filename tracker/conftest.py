"""Pytest configuration.

Placing this conftest at the tracker/ root ensures the application modules
(`database`, `models`, `main`) are importable from the test files without
requiring an installed package.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
