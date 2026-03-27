"""Pytest conftest — ensure analysis/ is on sys.path for bare imports."""
import sys
from pathlib import Path

import pytest

# Add analysis/ directory so bare imports (from features import ...) work
_analysis_dir = str(Path(__file__).parent)
if _analysis_dir not in sys.path:
    sys.path.insert(0, _analysis_dir)


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
