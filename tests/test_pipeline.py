"""Regression tests for the extraction pipeline: what counts as public API, and which changes are breaking.
Many cases reproduce false-positive patterns found by the three precision audits (see validation/)."""
import pytest
from helpers import symbols, breaking, changes

P = "pkg/__init__.py"


# ---------------------------------------------------------------- public names
def test_all_restricts_public_names():
    s = symbols({P: "__all__ = ['a']\ndef a(): pass\ndef b(): pass\n"})
    assert "pkg.a" in s and "pkg.b" not in s


def test_underscore_names_are_private():
    s = symbols({P: "def _x(): pass\ndef y(): pass\n"})
    assert "pkg._x" not in s and "pkg.y" in s


def test_private_modules_skipped():
    s = symbols({P: "", "pkg/_impl.py": "def f(): pass\n"})
    assert not any(p.startswith("pkg._impl") for p in s)


def test_star_import_reexports():
    s = symbols({P: "from .core import *\n", "pkg/core.py": "def f(): pass\n"})
    assert "pkg.f" in s


def test_star_import_respects_all_of_source():
    s = symbols({P: "from .core import *\n", "pkg/core.py": "__all__ = ['f']\ndef f(): pass\ndef g(): pass\n"})
    assert "pkg.f" in s and "pkg.g" not in s


def test_explicit_import_in_init_is_public():
    s = symbols({P: "from .core import f\n", "pkg/core.py": "def f(): pass\n"})
    assert "pkg.f" in s


def test_as_reexport_in_submodule_is_public():
    files = {P: "", "pkg/a.py": "from pkg.b import Y as Y\n", "pkg/b.py": "class Y: pass\n"}
    assert "pkg.a.Y" in symbols(files)


def test_type_checking_import_not_public():
    s = symbols({P: "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from .core import f\n",
                 "pkg/core.py": "def f(): pass\n"})
    assert "pkg.f" not in s


@pytest.mark.parametrize("test", ["sys.version_info < (3,)", "sys.version_info == (2, 7)", "PY2",
                                  "six.PY2", "TYPE_CHECKING"])
def test_python2_and_type_checking_branches_skipped(test):
    src = f"import sys, six\nif {test}:\n    def old(): pass\nelse:\n    def new(): pass\n"
    s = symbols({P: src})
    assert "pkg.new" in s and "pkg.old" not in s


@pytest.mark.xfail(reason="known gap: subscripted version_info tests are not recognised as Python-2-only", strict=True)
def test_python2_subscript_branch_skipped():
    src = "import sys\nif sys.version_info[0] == 2:\n    def old(): pass\nelse:\n    def new(): pass\n"
    assert "pkg.old" not in symbols({P: src})


def test_later_definition_wins():
    s = symbols({P: "def f(a): pass\ndef f(a, b): pass\n"})
    ev = changes({P: "def f(a, b): pass\n"}, {P: "def f(a): pass\ndef f(a, b): pass\n"})
    assert not [k for k, e in ev.items() if e["breaking"]]
    assert "pkg.f" in s


def test_try_except_fallback_binding():
    src = "try:\n    from fast import f\nexcept ImportError:\n    def f(x): pass\n"
    assert "pkg.f" in symbols({P: src})


def test_protocol_members_skipped():
    src = "from typing import Protocol\nclass P(Protocol):\n    def m(self): ...\n"
    s = symbols({P: src})
    assert "pkg.P" in s and "pkg.P.m" not in s


@pytest.mark.parametrize("call", ["TypedDict('T', {'a': int})", "NamedTuple('T', [('a', int)])",
                                  "namedtuple('T', 'a b')"])
def test_synthetic_classes(call):
    imp = "from collections import namedtuple\n" if call.startswith("namedtuple") else "from typing import *\n"
    s = symbols({P: f"{imp}T = {call}\n"})
    assert s["pkg.T"][0] in ("class", "class_open")


def test_inherited_method_within_package():
    s = symbols({P: "class A:\n    def m(self): pass\nclass B(A):\n    pass\n"})
    assert "pkg.B.m" in s


def test_external_base_makes_class_open():
    s = symbols({P: "import other\nclass B(other.Base):\n    def m(self): pass\n"})
    assert s["pkg.B"][0] == "class_open"


# ---------------------------------------------------------------- removals that are NOT breaking
def test_dropped_from_all_but_still_defined_is_not_breaking():
    old = {P: "__all__ = ['a', 'b']\ndef a(): pass\ndef b(): pass\n"}
    new = {P: "__all__ = ['a']\ndef a(): pass\ndef b(): pass\n"}
    assert not breaking(old, new)
    assert ("pkg.b", "left_public_api", "") in changes(old, new)


def test_moved_to_submodule_but_reexported_is_not_breaking():
    old = {P: "def f(): pass\n"}
    new = {P: "from .core import f\n", "pkg/core.py": "def f(): pass\n"}
    assert not breaking(old, new)


def test_method_moved_to_base_class_is_not_breaking():
    old = {P: "class A:\n    pass\nclass B(A):\n    def m(self): pass\n"}
    new = {P: "class A:\n    def m(self): pass\nclass B(A):\n    pass\n"}
    assert not breaking(old, new)


def test_lazy_module_getattr_serves_name():
    old = {P: "def f(): pass\n"}
    new = {P: "def __getattr__(name):\n    if name == 'f':\n        from ._lazy import f\n        return f\n"
              "    raise AttributeError(name)\n", "pkg/_lazy.py": "def f(): pass\n"}
    assert ("pkg.f", "removed", "") not in breaking(old, new)


def test_open_getattr_keeps_removed_name_undecided():
    old = {P: "def f(): pass\n"}
    new = {P: "def __getattr__(name):\n    return load(name)\n"}
    assert ("pkg.f", "removed", "") not in breaking(old, new)


def test_dynamic_globals_keep_names():
    old = {P: "def f(): pass\n"}
    new = {P: "for n in ['f']:\n    globals()[n] = make(n)\n"}
    assert ("pkg.f", "removed", "") not in breaking(old, new)


def test_name_moved_to_compiled_module_is_not_breaking():
    old = {P: "def f(): pass\n"}
    new = {P: "from ._speedups import f\n", "pkg/_speedups.cpython-312-x86_64-linux-gnu.so": b""}
    assert ("pkg.f", "removed", "") not in breaking(old, new)


def test_unparseable_module_is_opaque():
    old = {P: "from .m import *\n", "pkg/m.py": "def f(): pass\n"}
    new = {P: "from .m import *\n", "pkg/m.py": "print 'python 2'\n"}
    assert not breaking(old, new)


# ---------------------------------------------------------------- removals that ARE breaking
def test_function_removed():
    assert ("pkg.f", "removed", "") in breaking({P: "def f(): pass\ndef g(): pass\n"}, {P: "def g(): pass\n"},
                                               "high")


def test_closed_getattr_rejection_list_counts_as_removed():
    old = {P: "float_ = float\n"}
    new = {P: "_expired = {'float_': 'use float64'}\n"
              "def __getattr__(name):\n    if name in _expired:\n        raise AttributeError(_expired[name])\n"
              "    raise AttributeError(name)\n"}
    assert ("pkg.float_", "removed", "") in breaking(old, new)


NUMPY_LIKE_SHIM = (
    "import warnings, builtins as _b\n"
    "__deprecated_attrs__ = {}\n"
    "_type_info = [('float', 'use float64'), ('int', 'use int64')]\n"
    "__deprecated_attrs__.update({n: (getattr(_b, n), m) for n, m in _type_info})\n"
    "__deprecated_attrs__['typeDict'] = (dict, 'use sctypeDict')\n"
    "def __getattr__(attr):\n"
    "    try:\n        val, msg = __deprecated_attrs__[attr]\n    except KeyError:\n        pass\n"
    "    else:\n        warnings.warn(msg, DeprecationWarning, stacklevel=2)\n        return val\n"
    "    if attr == 'testing':\n        return 1\n"
    "    raise AttributeError(attr)\n")


def test_getattr_shim_container_names_are_public_and_deprecated():
    s = symbols({P: NUMPY_LIKE_SHIM})
    for n in ("float", "int", "typeDict"):
        assert s[f"pkg.{n}"][0] == "dynamic_attribute" and s[f"pkg.{n}"][2] is True
    assert s["pkg.testing"][0] == "dynamic_attribute" and s["pkg.testing"][2] is False


def test_getattr_shim_name_expiring_is_a_removal():
    new = {P: "_expired = {'float': 'removed in 2.0', 'int': 'removed', 'typeDict': 'removed'}\n"
              "def __getattr__(attr):\n    if attr in _expired:\n        raise AttributeError(_expired[attr])\n"
              "    raise AttributeError(attr)\n"}
    got = breaking({P: NUMPY_LIKE_SHIM}, new)
    assert {("pkg.float", "removed", ""), ("pkg.int", "removed", ""), ("pkg.typeDict", "removed", "")} <= got


def test_getattr_warn_only_branch_does_not_serve_and_comprehension_rejects():
    src = ("import warnings\n_info = [('float', 'x'), ('int', 'y')]\n"
           "__former_attrs__ = {n: m for n, m in _info}\n__future_scalars__ = {'bool'}\n"
           "def __getattr__(attr):\n"
           "    if attr in __future_scalars__:\n        warnings.warn('soon', FutureWarning)\n"
           "    if attr in __former_attrs__:\n        raise AttributeError(__former_attrs__[attr])\n"
           "    raise AttributeError(attr)\n")
    s = symbols({P: src})
    assert "pkg.float" not in s and "pkg.bool" not in s
    got = breaking({P: "float = 1.0\nint = 1\n"}, {P: src})
    assert ("pkg.float", "removed", "") in got and ("pkg.int", "removed", "") in got


def test_getattr_container_with_unfollowable_filling_is_not_used():
    src = ("_lazy = {}\n_lazy.update(compute())\n"
           "def __getattr__(name):\n    if name in _lazy:\n        return _lazy[name]\n    raise AttributeError(name)\n")
    assert not any(k.startswith("pkg.") and k != "pkg" for k in symbols({P: src}))


def test_class_method_removed():
    old = {P: "class C:\n    def a(self): pass\n    def b(self): pass\n"}
    new = {P: "class C:\n    def a(self): pass\n"}
    assert ("pkg.C.b", "removed", "") in breaking(old, new, "high")


def test_member_removed_from_open_class_is_medium():
    old = {P: "import x\nclass C(x.Base):\n    def a(self): pass\n    def b(self): pass\n"}
    new = {P: "import x\nclass C(x.Base):\n    def a(self): pass\n"}
    ev = changes(old, new)
    assert ev[("pkg.C.b", "removed", "")]["confidence"] == "medium"


def test_children_of_removed_class_marked_parent_removed():
    old = {P: "class C:\n    def a(self): pass\n"}
    ev = changes(old, {P: ""})
    assert ev[("pkg.C.a", "removed", "")]["parent_removed"]
    assert not ev[("pkg.C", "removed", "")]["parent_removed"]


def test_removed_but_available_elsewhere_hint():
    old = {P: "from .a import f\n", "pkg/a.py": "def f(): pass\n", "pkg/b.py": ""}
    new = {P: "", "pkg/a.py": "", "pkg/b.py": "def f(): pass\n"}
    ev = changes(old, new)
    assert "still available as pkg.b.f" in ev[("pkg.f", "removed", "")]["detail"]


# ---------------------------------------------------------------- signature changes
SIG_CASES = [
    ("def f(a, b): pass", "def f(a): pass", ("pkg.f", "param_removed", "b"), True),
    ("def f(a, b): pass", "def f(a, c): pass", ("pkg.f", "param_renamed", "b"), True),
    ("def f(a): pass", "def f(a, b): pass", ("pkg.f", "required_param_added", "b"), True),
    ("def f(a): pass", "def f(a, b=1): pass", ("pkg.f", "optional_param_added", "b"), False),
    ("def f(a, b=1): pass", "def f(a, b): pass", ("pkg.f", "default_removed", "b"), True),
    ("def f(a, b=1): pass", "def f(a, *, b=1): pass", ("pkg.f", "became_keyword_only", "b"), True),
    ("def f(a, b): pass", "def f(a, b, /): pass", ("pkg.f", "became_positional_only", "b"), True),
    ("def f(a, b): pass", "def f(b, a): pass", ("pkg.f", "positional_reordered", ""), True),
    ("def f(a, *args): pass", "def f(a): pass", ("pkg.f", "vararg_removed", "args"), True),
    ("def f(a, b=1): pass", "def f(a, b=2): pass", ("pkg.f", "default_changed", "b"), False),
]


@pytest.mark.parametrize("old,new,key,is_breaking", SIG_CASES, ids=[c[2][1] for c in SIG_CASES])
def test_signature_changes(old, new, key, is_breaking):
    ev = changes({P: old + "\n"}, {P: new + "\n"})
    assert key in ev, sorted(ev)
    assert ev[key]["breaking"] is is_breaking


def test_param_absorbed_by_kwargs_is_not_breaking():
    ev = changes({P: "def f(a, b=1): pass\n"}, {P: "def f(a, **kw): pass\n"})
    e = ev[("pkg.f", "param_removed", "b")]
    assert not e["breaking"] and e["detail"].startswith("absorbed")


def test_varkw_removed_is_medium():
    ev = changes({P: "def f(a, **kw): pass\n"}, {P: "def f(a): pass\n"})
    assert ev[("pkg.f", "varkw_removed", "kw")]["confidence"] == "medium"


def test_pep484_dunder_params_are_positional_only():
    # renaming a "__x" parameter is invisible to callers, who can only pass it positionally
    ev = changes({P: "def f(__a): pass\n"}, {P: "def f(__b): pass\n"})
    assert not [k for k, e in ev.items() if e["breaking"]]


def test_overloads_merged_into_one_signature():
    src = ("from typing import overload\n@overload\ndef f(a: int) -> int: ...\n@overload\n"
           "def f(a: str, b: int) -> str: ...\ndef f(a, b=0): return a\n")
    ev = changes({P: src}, {P: src})
    assert not [k for k, e in ev.items() if e["breaking"]]
    assert "pkg.f" in symbols({P: src})


def test_constructor_signature_tracked():
    ev = changes({P: "class C:\n    def __init__(self, a, b=1): pass\n"},
                 {P: "class C:\n    def __init__(self, a): pass\n"})
    assert ev[("pkg.C", "param_removed", "b")]["breaking"]


def test_decorated_function_keeps_signature():
    src_old = "import functools\n@functools.lru_cache\ndef f(a, b): pass\n"
    src_new = "import functools\n@functools.lru_cache\ndef f(a): pass\n"
    assert ("pkg.f", "param_removed", "b") in changes({P: src_old}, {P: src_new})


# ---------------------------------------------------------------- stubs
def test_runtime_source_preferred_over_stub():
    files = {P: "def f(a, b): pass\n", "pkg/__init__.pyi": "def f(a: int) -> None: ...\n"}
    s = symbols(files)
    sigs = {}
    s = symbols(files, sigs)
    params = [p[0] for p in sigs[s["pkg.f"][1]]["params"]]
    assert params == ["a", "b"]


def test_stub_fills_names_runtime_does_not_bind():
    files = {P: "from ._c import *\n", "pkg/__init__.pyi": "def g(a: int) -> None: ...\n",
             "pkg/_c.cpython-312-x86_64-linux-gnu.so": b""}
    assert "pkg.g" in symbols(files)


def test_signature_change_seen_only_in_stub_is_not_breaking():
    old = {P: "from ._c import *\n", "pkg/__init__.pyi": "def g(a: int, b: int) -> None: ...\n",
           "pkg/_c.cpython-312-x86_64-linux-gnu.so": b""}
    new = {P: "from ._c import *\n", "pkg/__init__.pyi": "def g(a: int) -> None: ...\n",
           "pkg/_c.cpython-312-x86_64-linux-gnu.so": b""}
    assert not breaking(old, new)


def test_stub_only_package_is_analysed():
    s = symbols({"pkg/__init__.pyi": "def f(a: int) -> int: ...\n"})
    assert "pkg.f" in s


# ---------------------------------------------------------------- deprecation markers
@pytest.mark.parametrize("src", [
    "import warnings\ndef f():\n    warnings.warn('x', DeprecationWarning)\n",
    "from typing_extensions import deprecated\n@deprecated('use g')\ndef f(): pass\n",
    "def f():\n    '''Old.\n\n    .. deprecated:: 2.0\n    '''\n",
])
def test_deprecation_marker_detected(src):
    assert symbols({P: src})["pkg.f"][2] is True


def test_conditional_warning_is_not_whole_symbol_deprecation():
    src = "import warnings\ndef f(a=None):\n    if a is not None:\n        warnings.warn('a', DeprecationWarning)\n"
    assert symbols({P: src})["pkg.f"][2] is False


def test_argument_deprecation_decorator_does_not_count():
    src = "from x import deprecate_kwarg\n@deprecate_kwarg('old', 'new')\ndef f(new=None): pass\n"
    assert symbols({P: src})["pkg.f"][2] is False


def test_unchanged_release_has_no_events():
    files = {P: "class C:\n    def m(self, a): pass\ndef f(x, *, y=1): pass\n"}
    assert not [k for k, e in changes(files, files).items()]


@pytest.mark.parametrize("old,new", [("def f(a, /): pass", "def f(b, /): pass"),
                                     ("def f(__a): pass", "def f(__b): pass")])
def test_renaming_positional_only_param_is_not_a_change(old, new):
    ev = changes({P: old + "\n"}, {P: new + "\n"})
    assert not [k for k, e in ev.items() if e["breaking"]], sorted(ev)


def test_positional_only_param_removed_is_breaking():
    ev = changes({P: "def f(a, b, /): pass\n"}, {P: "def f(a, /): pass\n"})
    assert ev[("pkg.f", "param_removed", "b")]["breaking"]


@pytest.mark.parametrize("dec", ["dataclass", "attr.s", "make_norm_from_scale(Scale, init=lambda a, b=1: None)"])
def test_decorator_generated_constructor_not_taken_from_base(dec):
    src = f"import attr\nfrom dataclasses import dataclass\nclass Base:\n    def __init__(self, x, y=1): pass\n@{dec}\nclass C(Base):\n    pass\n"
    sigs = {}
    s = symbols({P: src}, sigs)
    assert s["pkg.C"][1] is None  # constructor unknown, not Base.__init__


def test_safe_class_decorator_keeps_inherited_constructor():
    src = "import functools\nclass Base:\n    def __init__(self, x): pass\n@functools.total_ordering\nclass C(Base):\n    pass\n"
    assert symbols({P: src})["pkg.C"][1] is not None


def test_module_level_del_removes_name():
    src = "from ._version import get_versions\n__version__ = get_versions()['version']\ndel get_versions\n"
    s = symbols({P: src, "pkg/_version.py": "def get_versions(): return {}\n"})
    assert "pkg.get_versions" not in s and "pkg.__version__" not in s


@pytest.mark.parametrize("src,dynamic", [
    ("def __dir__():\n    return list(globals().keys())\n", False),
    ("x = globals().get('y')\n", False),
    ("globals()['a'] = 1\n", True),
    ("globals().update(make())\n", True),
    ("exec('a = 1')\n", True),
    ("_builder.BuildTopDescriptorsAndMessages(DESCRIPTOR, 'x_pb2', globals())\n", True),
    ("for _n in ['f96', 'f128']:\n    globals()[_n] = getattr(core, _n)\n", False),
])
def test_dynamic_globals_only_for_writes(src, dynamic):
    from modparse import parse_module
    assert parse_module(src)["dynamic_globals"] is dynamic


def test_literal_loop_globals_names_are_known():
    s = symbols({P: "import core\nfor _n in ['f96', 'f128']:\n    globals()[_n] = getattr(core, _n)\n"})
    assert "pkg.f96" in s and "pkg.f128" in s


def test_removed_name_found_despite_read_only_globals():
    old = {P: "def bool8(): pass\ndef __dir__():\n    return list(globals())\n"}
    new = {P: "def __dir__():\n    return list(globals())\n"}
    assert ("pkg.bool8", "removed", "") in breaking(old, new)
