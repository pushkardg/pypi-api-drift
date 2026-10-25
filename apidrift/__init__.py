"""apidrift: check Python code against the public API of pinned library versions (PyPI API Drift dataset)."""
from .check import Store, check_source, parse_pins

__all__ = ["Store", "check_source", "parse_pins"]
try:
    from importlib.metadata import version as _v
    __version__ = _v("pypi-api-drift")
except Exception:  # running from a source tree that is not installed
    __version__ = "1.3.2"
