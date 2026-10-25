import os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "pipeline"))

from helpers import build_dataset  # noqa: E402

DEMO_V1 = {
    "demo/__init__.py": (
        "from .frame import Frame, read\n"
        "from . import util\n"
        "def old_helper(x): pass\n"
        "def connect(host, port=80, timeout=None): pass\n"
    ),
    "demo/frame.py": (
        "class Frame:\n"
        "    def __init__(self, data=None, copy=False): pass\n"
        "    def append(self, other, ignore_index=False): pass\n"
        "    def to_csv(self, path, line_terminator='\\n'): pass\n"
        "def read(path, error_bad_lines=True, sep=','): pass\n"
    ),
    "demo/util.py": "def tool(a, b=1): pass\n",
}
DEMO_V2 = {
    "demo/__init__.py": (
        "import warnings\n"
        "from .frame import Frame, read\n"
        "from . import util\n"
        "def connect(host, port=80, *, timeout=None): pass\n"
        "def legacy():\n    warnings.warn('legacy is deprecated', DeprecationWarning)\n"
        "def brand_new(): pass\n"
    ),
    "demo/frame.py": (
        "class Frame:\n"
        "    def __init__(self, data=None, copy=False): pass\n"
        "    def to_csv(self, path, lineterminator='\\n'): pass\n"
        "    def concat(self, other): pass\n"
        "def read(path, on_bad_lines='error', sep=','): pass\n"
    ),
    "demo/util.py": "def tool(a, b=1): pass\n",
}


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    v15 = dict(DEMO_V1)
    v15["demo/__init__.py"] = DEMO_V1["demo/__init__.py"] + (
        "import warnings\ndef legacy():\n    warnings.warn('legacy is deprecated', DeprecationWarning)\n")
    return build_dataset(str(d), {"demo-lib": [("1.0.0", DEMO_V1), ("1.5.0", v15), ("2.0.0", DEMO_V2)]})


@pytest.fixture(scope="session")
def store(data_dir):
    from apidrift.check import Store
    return Store(data_dir)
