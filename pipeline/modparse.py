"""Static, import-free extraction of one Python module's top-level API.

parse_module(source_bytes, is_stub) -> dict with
  all       : list[str] | None          static __all__ if it can be evaluated
  defs      : {name: info}              functions / classes / attributes defined at module level
  imports   : {alias: (module, level, attr)}   from-imports and imports (attr None for `import x`)
  stars     : [(module, level)]          `from x import *`
  error     : str | None
Function info: {"kind": "function", "sig": sig, "deprecated": bool, "async": bool}
Class info   : {"kind": "class", "bases": [...], "members": {name: info}, "deprecated": bool}
Signatures   : {"params": [[name, kind, has_default, default_src, annotation_src], ...], "returns": str|None}
  kind codes: PO positional-only, PK positional-or-keyword, VA *args, KO keyword-only, VK **kwargs
"""
import ast, warnings

DEPR_WARNINGS = {"DeprecationWarning", "FutureWarning", "PendingDeprecationWarning"}
MAX_SRC = 80


def _src(node, limit=MAX_SRC):
    if node is None:
        return None
    try:
        s = ast.unparse(node)
    except Exception:
        return "?"
    return s if len(s) <= limit else s[: limit - 1] + "…"


def signature(fn, drop_first):
    a = fn.args
    pos = [(p, "PO") for p in a.posonlyargs] + [(p, "PK") for p in a.args]
    defaults = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    params = []
    for (p, k), d in zip(pos, defaults):
        params.append([p.arg, k, d is not None, _src(d), _src(p.annotation)])
    if a.vararg:
        params.append([a.vararg.arg, "VA", False, None, _src(a.vararg.annotation)])
    for p, d in zip(a.kwonlyargs, a.kw_defaults):
        params.append([p.arg, "KO", d is not None, _src(d), _src(p.annotation)])
    if a.kwarg:
        params.append([a.kwarg.arg, "VK", False, None, _src(a.kwarg.annotation)])
    if drop_first and params and params[0][1] in ("PO", "PK"):
        params = params[1:]
    # PEP 484 convention: leading positional params named __x are positional-only; normalize to PEP 570 form
    for i, p in enumerate(params):
        if p[1] in ("PO", "PK") and p[0].startswith("__") and not p[0].endswith("__"):
            params[i] = [p[0][2:], "PO"] + p[2:]
        elif p[1] in ("PO", "PK"):
            break
    return {"params": params, "returns": _src(fn.returns)}


def merge_overloads(sigs):
    """Signature accepted by any of several @overload variants (stub-only functions)."""
    if len(sigs) == 1:
        return sigs[0]
    order, by = [], {}
    for sg in sigs:
        for p in sg["params"]:
            if p[0] not in by:
                order.append(p[0]); by[p[0]] = list(p)
            else:
                q = by[p[0]]
                if p[2] and not q[2]:
                    q[2], q[3] = True, p[3]
    for n in order:  # optional if some overload omits it
        if any(n not in {p[0] for p in sg["params"]} for sg in sigs) and by[n][1] not in ("VA", "VK"):
            by[n][2] = True
            by[n][3] = by[n][3] or "..."
        by[n][4] = None  # annotations differ per overload; don't compare them
    rets = {sg["returns"] for sg in sigs}
    return {"params": [by[n] for n in order], "returns": rets.pop() if len(rets) == 1 else None, "stub": True}


def _dec_names(node):
    out = []
    for d in node.decorator_list:
        t = d.func if isinstance(d, ast.Call) else d
        if isinstance(t, ast.Attribute):
            out.append(t.attr)
        elif isinstance(t, ast.Name):
            out.append(t.id)
    return out


def _is_warn_call(c):
    """warnings.warn(..., DeprecationWarning) or a helper whose name says deprecat*."""
    f = c.func
    fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
    if "deprecat" in fname.lower():
        return True
    if fname == "warn":
        for arg in list(c.args[1:2]) + [k.value for k in c.keywords if k.arg == "category"]:
            an = arg.attr if isinstance(arg, ast.Attribute) else (arg.id if isinstance(arg, ast.Name) else "")
            if an in DEPR_WARNINGS or "deprecat" in an.lower():
                return True
    return False


def _is_deprecated(node):
    """True only for whole-symbol deprecation: a deprecat* decorator, a `.. deprecated::` docstring
    directive, or an *unconditional* deprecation warning at the top of the body. Warnings issued only
    on some code paths (usually parameter deprecations) don't count."""
    for n in _dec_names(node):
        low = n.lower()
        # argument-level helpers (pandas deprecate_kwarg / deprecate_nonkeyword_arguments, ...) don't count
        if "deprecat" in low and not any(w in low for w in ("arg", "kwarg", "param", "positional", "keyword")):
            return True
    doc = ast.get_docstring(node, clean=False) if not isinstance(node, ast.Lambda) else None
    if doc and ".. deprecated::" in doc:
        return True
    if isinstance(node, ast.ClassDef):
        return False
    for s in node.body[:6]:
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call) and _is_warn_call(s.value):
            return True
    return False


def _function(node, in_class):
    decs = _dec_names(node)
    kind = "function"
    drop = in_class
    if in_class:
        kind = "method"
        if "staticmethod" in decs:
            kind, drop = "staticmethod", False
        elif "classmethod" in decs:
            kind = "classmethod"
        elif "property" in decs or "cached_property" in decs:
            kind = "property"
    sig = signature(node, drop)
    if "overload" in decs:
        sig["stub"] = True  # typing-only overload; replaced if a real implementation follows
    return {"kind": kind, "sig": sig, "deprecated": _is_deprecated(node),
            "async": isinstance(node, ast.AsyncFunctionDef),
            "overload": "overload" in decs}


def _never_at_runtime(test):
    """`if TYPE_CHECKING:` and Python-2-only branches (`if sys.version_info < (3,)`, `== (2, 6)`, `if PY2:`)."""
    if isinstance(test, ast.Name) and test.id in ("TYPE_CHECKING", "PY2"):
        return True
    if isinstance(test, ast.Attribute) and test.attr in ("TYPE_CHECKING", "PY2"):
        return True
    if isinstance(test, ast.Compare) and len(test.ops) == 1 and ("version_info" in (_src(test.left) or "") or "ver_info" in (_src(test.left) or "")):
        c = test.comparators[0]
        if isinstance(c, ast.Tuple) and c.elts and isinstance(c.elts[0], ast.Constant) and c.elts[0].value == 2:
            return isinstance(test.ops[0], (ast.Eq, ast.Lt, ast.LtE))
        if isinstance(c, ast.Tuple) and c.elts and isinstance(c.elts[0], ast.Constant) and c.elts[0].value == 3 \
                and len(c.elts) == 1:
            return isinstance(test.ops[0], ast.Lt)
    if isinstance(test, ast.Subscript) and "version_info" in (_src(test) or ""):
        return False
    return False


RUNTIME_ONLY = [True]  # set False while parsing .pyi stubs, where TYPE_CHECKING blocks are ordinary declarations


def _walk_body(body, fallback=False, flagged=False):
    """Yield top-level statements, descending into if/try/with blocks (conditional definitions).
    With flagged=True yields (stmt, is_fallback): statements in `except` handlers and `else` branches of
    `if` are fallbacks that must not override a binding made by the primary branch. Branches that never run
    (`if TYPE_CHECKING:`, Python-2-only) are skipped and their `else` becomes the primary branch."""
    for s in body:
        if isinstance(s, ast.If) and RUNTIME_ONLY[0] and _never_at_runtime(s.test):
            yield from _walk_body(s.orelse, fallback, flagged)
        elif isinstance(s, ast.If):
            yield from _walk_body(s.body, fallback, flagged); yield from _walk_body(s.orelse, True, flagged)
        elif isinstance(s, ast.Try) or (hasattr(ast, "TryStar") and isinstance(s, ast.TryStar)):
            yield from _walk_body(s.body, fallback, flagged)
            for h in s.handlers:
                yield from _walk_body(h.body, True, flagged)
            yield from _walk_body(s.orelse, fallback, flagged); yield from _walk_body(s.finalbody, fallback, flagged)
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            yield from _walk_body(s.body, fallback, flagged)
        else:
            yield (s, fallback) if flagged else s


def _targets(s):
    if isinstance(s, ast.Assign):
        ts = s.targets
    elif isinstance(s, (ast.AnnAssign, ast.AugAssign)):
        ts = [s.target]
    else:
        return []
    out = []
    for t in ts:
        if isinstance(t, ast.Name):
            out.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            out.extend(e.id for e in t.elts if isinstance(e, ast.Name))
    return out


def _instance_attrs(fn):
    """Names assigned as self.<name> = ... anywhere in a method body."""
    out = []
    if not fn.args.args:
        return out
    me = fn.args.args[0].arg
    for n in ast.walk(fn):
        ts = n.targets if isinstance(n, ast.Assign) else ([n.target] if isinstance(n, (ast.AnnAssign, ast.AugAssign)) else [])
        for t in ts:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == me:
                out.append(t.attr)
    return out


def _class(node):
    members = {}
    inst = []
    for s in _walk_body(node.body):
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and "staticmethod" not in _dec_names(s):
            inst.extend(_instance_attrs(s))
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            info = _function(s, True)
            prev = members.get(s.name)
            # implementation beats overloads; overload-only (stubs) -> merged; property setters don't replace getters
            if prev is None:
                info["_ov"] = [info["sig"]] if info["overload"] else None
                members[s.name] = info
            elif prev.get("overload") and info["overload"]:
                prev["_ov"].append(info["sig"]); prev["sig"] = merge_overloads(prev["_ov"])
            elif prev.get("overload") and not info["overload"]:
                info["_ov"] = None
                members[s.name] = info
        elif isinstance(s, ast.ClassDef):
            members[s.name] = {"kind": "class", "bases": [_src(b, 120) for b in s.bases],
                               "members": {}, "deprecated": _is_deprecated(s)}
        else:
            for t in _targets(s):
                members.setdefault(t, {"kind": "attribute"})
    for a in inst:
        members.setdefault(a, {"kind": "attribute"})
    out = {"kind": "class", "bases": [_src(b, 120) for b in node.bases], "members": members,
           "deprecated": _is_deprecated(node) or bool(members.get("__init__", {}).get("deprecated"))}
    # a class decorator that may generate or replace the constructor (dataclass, attrs, factories such as
    # matplotlib's make_norm_from_scale): without an __init__ in the body, the inherited one is not reliable
    if "__init__" not in members and any(d not in SAFE_CLASS_DECORATORS for d in _dec_names(node)):
        out["init_generated"] = True
    return out


SAFE_CLASS_DECORATORS = {"total_ordering", "final", "runtime_checkable", "unique", "deprecated", "override",
                         "add_metaclass", "register", "export", "public", "dataclass_transform", "type_check_only",
                         "final_class", "six_add_metaclass", "python_2_unicode_compatible", "implementer"}


def _getattr_names(fn):
    """String literals a module-level __getattr__ compares the requested name against."""
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.Compare) and any(isinstance(o, (ast.Eq, ast.In)) for o in n.ops):
            for c in n.comparators:
                vals = c.elts if isinstance(c, (ast.Tuple, ast.List, ast.Set)) else \
                       (c.keys if isinstance(c, ast.Dict) else [c])
                for v in vals:
                    if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value.isidentifier():
                        out.append(v.value)
    return list(dict.fromkeys(out))


def _literal_test(t):
    if isinstance(t, ast.BoolOp):
        return all(_literal_test(v) for v in t.values)
    if isinstance(t, ast.Compare) and all(isinstance(o, (ast.Eq, ast.In)) for o in t.ops):
        for c in t.comparators:
            vals = c.elts if isinstance(c, (ast.Tuple, ast.List, ast.Set)) else [c]
            if not all(isinstance(v, ast.Constant) for v in vals):
                return False
        return True
    return False


def _getattr_closed(fn):
    """True when the module __getattr__ only returns objects for literally named attributes (and raises
    for anything else), so a name not among those literals is definitely not served."""
    def ok(stmts, guarded):
        for s in stmts:
            if isinstance(s, ast.Return) and not guarded:
                return False
            if isinstance(s, ast.If):
                if not ok(s.body, guarded or _literal_test(s.test)) or not ok(s.orelse, guarded):
                    return False
            elif isinstance(s, (ast.Try, ast.With)) or (hasattr(ast, "TryStar") and isinstance(s, ast.TryStar)):
                blocks = [s.body] + [h.body for h in getattr(s, "handlers", [])] + \
                         [getattr(s, "orelse", []), getattr(s, "finalbody", [])]
                if not all(ok(b, guarded) for b in blocks):
                    return False
            elif isinstance(s, (ast.For, ast.While)):
                if not ok(s.body, guarded) or not ok(s.orelse, guarded):
                    return False
            elif isinstance(s, ast.Match):
                return False
        return True
    return ok(fn.body, False)


def _flag_stub(d):
    d = dict(d)
    if "sig" in d and d["sig"] is not None:
        d["sig"] = dict(d["sig"], stub=True)
    if d.get("kind") == "class":
        d["members"] = {k: _flag_stub(v) for k, v in d.get("members", {}).items()}
    d["stub_only"] = True
    return d


def merge_stub(py, pyi):
    """Combine a module's .py and .pyi parses. The runtime source wins wherever it defines a name; the stub
    only fills in names the source doesn't bind (typically populated dynamically or by compiled code), and
    those are flagged as stub-derived. Stub-only class members are dropped (they often don't exist at runtime)."""
    if py["error"]:
        return pyi
    if pyi["error"]:
        return py
    out = dict(py)
    out["defs"] = dict(py["defs"])
    out["imports"] = dict(py["imports"])
    out["stars"] = list(dict.fromkeys(py["stars"] + [st for st in pyi["stars"]]))
    out["all"] = py["all"]
    out["has_getattr"] = py.get("has_getattr", False)
    for n, d in pyi["defs"].items():
        rd = py["defs"].get(n)
        if rd is None:
            if n in py["imports"] or d.get("typing") or n.startswith("global___"):
                continue  # runtime binding is the import / a typing-only name the runtime never defines
            out["defs"][n] = _flag_stub(d)
        elif rd.get("kind") == "attribute" and d.get("kind") in ("function", "class"):
            out["defs"][n] = _flag_stub(d)  # runtime object built dynamically (f = make_checker(...))
    for n, imp in pyi["imports"].items():
        if n not in out["defs"] and n not in out["imports"]:
            out["imports"][n] = imp
    return out


SYNTHETIC_CLASS_CALLS = {"TypedDict", "NamedTuple", "namedtuple", "NewType", "make_dataclass", "Enum", "IntEnum",
                         "StrEnum", "Flag", "IntFlag", "type"}


TYPING_ONLY_CALLS = {"TypeVar", "ParamSpec", "TypeVarTuple"}


def _assigned_kind(value):
    """kind of a module-level `name = value` binding."""
    if isinstance(value, ast.Call):
        f = value.func
        fn = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if fn in TYPING_ONLY_CALLS:
            return {"kind": "attribute", "typing": True}
        if fn in SYNTHETIC_CLASS_CALLS:
            return {"kind": "class", "bases": [], "members": {}, "deprecated": False, "synthetic": True}
    return {"kind": "attribute"}


def _literal_names(value):
    if isinstance(value, ast.Dict):
        vals = value.keys
    elif isinstance(value, (ast.Set, ast.List, ast.Tuple)):
        vals = value.elts
    else:
        return None
    out = [v.value for v in vals if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return out if out else None


def _module_level(tree):
    """Module-level statements, including those nested in if/try/with/else blocks (not function or class bodies)."""
    out, stack = [], list(tree.body)
    while stack:
        st = stack.pop(0)
        out.append(st)
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            stack.extend(getattr(st, field, []) or [])
        for h in getattr(st, "handlers", []) or []:
            stack.extend(h.body)
    return out


def _comp_keys(comp, seqs):
    """Keys of a dict/set comprehension `{n: ... for n[, x] in SEQ}` over a literal sequence (or a module-level name
    bound to one): the string at the position of the key variable in each element."""
    if not isinstance(comp, (ast.DictComp, ast.SetComp)) or len(comp.generators) != 1:
        return None
    g = comp.generators[0]
    key = comp.key if isinstance(comp, ast.DictComp) else comp.elt
    if g.ifs or not isinstance(key, ast.Name):
        return None
    if isinstance(g.target, ast.Name) and g.target.id == key.id:
        pos = None
    elif isinstance(g.target, ast.Tuple) and any(isinstance(e, ast.Name) and e.id == key.id for e in g.target.elts):
        pos = [i for i, e in enumerate(g.target.elts) if isinstance(e, ast.Name) and e.id == key.id][0]
    else:
        return None
    it = g.iter
    elts = seqs.get(it.id) if isinstance(it, ast.Name) else (it.elts if isinstance(it, (ast.List, ast.Tuple, ast.Set))
                                                                 else None)
    if elts is None:
        return None
    out = []
    for e in elts:
        v = e if pos is None else (e.elts[pos] if isinstance(e, (ast.Tuple, ast.List)) and len(e.elts) > pos else None)
        if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
            return None
        out.append(v.value)
    return out


def _served_containers(tree):
    """{name: [str keys]} for module-level dicts/sets/lists whose keys are statically known, following the ways
    deprecation shims fill them: a literal, X[\"k\"] = ..., X.update({...}), X.update({n: ... for n in LITERAL}),
    X |= {...}, X.add("k"). A container whose filling cannot be followed is dropped (unknown, not partial)."""
    seqs, keys, bad = {}, {}, set()
    stmts = _module_level(tree)
    for st in stmts:
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            n, v = st.targets[0].id, st.value
            if isinstance(v, (ast.List, ast.Tuple)):
                seqs[n] = v.elts
            lit = _literal_names(v) if isinstance(v, (ast.Dict, ast.Set, ast.List, ast.Tuple)) else None
            if isinstance(v, (ast.Dict, ast.Set, ast.List, ast.Tuple)) and (lit is not None or not getattr(v, "keys", getattr(v, "elts", None))):
                keys[n] = list(lit or [])
            else:
                ck = _comp_keys(v, seqs)
                if ck is not None:
                    keys[n] = ck
                elif n in keys:
                    bad.add(n)
        elif isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Subscript):
            t = st.targets[0]
            if isinstance(t.value, ast.Name) and t.value.id in keys:
                k = t.slice
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys[t.value.id].append(k.value)
                else:
                    bad.add(t.value.id)
        elif isinstance(st, ast.AugAssign) and isinstance(st.target, ast.Name) and st.target.id in keys:
            lit = _literal_names(st.value)
            if lit is None:
                bad.add(st.target.id)
            else:
                keys[st.target.id].extend(lit)
        elif isinstance(st, ast.Expr) and isinstance(st.value, ast.Call) and isinstance(st.value.func, ast.Attribute) \
                and isinstance(st.value.func.value, ast.Name) and st.value.func.value.id in keys:
            n, meth, args = st.value.func.value.id, st.value.func.attr, st.value.args
            if meth in ("update", "extend") and len(args) == 1:
                got = _literal_names(args[0]) if isinstance(args[0], (ast.Dict, ast.Set, ast.List, ast.Tuple)) \
                    else _comp_keys(args[0], seqs)
                if got is None:
                    bad.add(n)
                else:
                    keys[n].extend(got)
            elif meth in ("add", "append") and len(args) == 1 and isinstance(args[0], ast.Constant) and \
                    isinstance(args[0].value, str):
                keys[n].append(args[0].value)
            elif meth in ("pop", "remove", "discard", "clear"):
                bad.add(n)
    return {k: list(dict.fromkeys(v)) for k, v in keys.items() if k not in bad and v}


def _getattr_container_lookups(fn, mode="served"):
    """Module-level containers a __getattr__ looks the requested name up in. mode="served": only lookups whose branch
    returns an object (`if name in X: return ...`, `try: v = X[name] ... else: return v`, `return X[name]`,
    `return X.get(name)`); a branch that only warns or raises does not serve. mode="any": every lookup."""
    if not fn.args.args:
        return []
    arg = fn.args.args[0].arg

    def refs(node):
        out = []
        for n in ast.walk(node):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) and isinstance(n.slice, ast.Name) and \
                    n.slice.id == arg:
                out.append(n.value.id)
            elif isinstance(n, ast.Compare) and isinstance(n.left, ast.Name) and n.left.id == arg and \
                    len(n.ops) == 1 and isinstance(n.ops[0], ast.In) and isinstance(n.comparators[0], ast.Name):
                out.append(n.comparators[0].id)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and \
                    isinstance(n.func.value, ast.Name) and n.args and isinstance(n.args[0], ast.Name) and \
                    n.args[0].id == arg:
                out.append(n.func.value.id)
        return out

    if mode == "any":
        return list(dict.fromkeys(refs(fn)))
    returns = lambda stmts: any(isinstance(x, ast.Return) and x.value is not None
                                for st in stmts for x in ast.walk(st))
    out = []
    for n in ast.walk(fn):
        if isinstance(n, ast.If) and returns(n.body):
            out += refs(n.test)
        elif isinstance(n, ast.Try) and (returns(n.body) or returns(n.orelse)):
            out += [r for st in n.body for r in refs(st)]
        elif isinstance(n, ast.Return) and n.value is not None:
            out += refs(n.value)
    return list(dict.fromkeys(out))


def _getattr_raises(fn, containers, foreign=None):
    """Names a module __getattr__ explicitly rejects (e.g. numpy's __expired_attributes__). Container names that
    are not literals of this module (typically imported from another module) are collected in `foreign`."""
    if not fn.args.args:
        return []
    arg = fn.args.args[0].arg
    out = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.If):
            continue
        t = n.test
        if not (isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == arg and len(t.ops) == 1):
            continue
        raises = any(isinstance(x, ast.Raise) for b in n.body for x in ast.walk(b))
        returns = any(isinstance(x, ast.Return) for b in n.body for x in ast.walk(b))
        if not raises or returns:
            continue
        c = t.comparators[0]
        if isinstance(t.ops[0], ast.Eq) and isinstance(c, ast.Constant) and isinstance(c.value, str):
            out.append(c.value)
        elif isinstance(t.ops[0], ast.In):
            if isinstance(c, ast.Name) and c.id in containers:
                out.extend(containers[c.id])
            elif isinstance(c, ast.Name) and foreign is not None:
                foreign.append(c.id)
            else:
                lit = _literal_names(c)
                out.extend(lit or [])
    return out


def _is_globals_call(n):
    return isinstance(n, ast.Call) and not n.args and (
        (isinstance(n.func, ast.Name) and n.func.id in ("globals", "vars")) or
        (isinstance(n.func, ast.Attribute) and n.func.attr in ("globals", "vars")))


def _literal_loop_names(tree):
    """Names written by `for x in ["a", "b"]: globals()[x] = ...` (a literal list): known, not dynamic."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.For) and isinstance(n.iter, (ast.List, ast.Tuple)) and isinstance(n.target, ast.Name) \
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.iter.elts):
            for m in ast.walk(n):
                if isinstance(m, ast.Subscript) and _is_globals_call(m.value) and isinstance(m.ctx, ast.Store) \
                        and isinstance(m.slice, ast.Name) and m.slice.id == n.target.id:
                    out.update(e.value for e in n.iter.elts)
    return out


def _dynamic_globals(tree):
    """Module WRITES its namespace programmatically: globals()/vars() item assignment or update/setdefault,
    exec, setattr(sys.modules[...]), protobuf builders. Reads such as `globals().keys()` in __dir__ don't count,
    and writes driven by a literal list of names are resolved instead (see _literal_loop_names)."""
    literal_loops = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.For) and isinstance(n.iter, (ast.List, ast.Tuple)) and \
                all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.iter.elts):
            literal_loops.update(id(m) for m in ast.walk(n))
    for n in ast.walk(tree):
        if id(n) in literal_loops:
            continue
        if isinstance(n, ast.Subscript) and _is_globals_call(n.value) and isinstance(n.ctx, (ast.Store, ast.Del)):
            return True
        if isinstance(n, ast.Call):
            f = n.func
            fn = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if fn == "exec":
                return True
            if isinstance(f, ast.Attribute) and f.attr in ("update", "setdefault", "__setitem__") and \
                    _is_globals_call(f.value):
                return True
            if fn == "setattr" and n.args and isinstance(n.args[0], ast.Subscript):
                return True  # setattr(sys.modules[__name__], ...)
            if fn in ("BuildTopDescriptorsAndMessages", "BuildMessageAndEnumDescriptors"):
                return True  # protobuf generated modules
            if any(_is_globals_call(a) for a in list(n.args) + [k.value for k in n.keywords]) and \
                    fn not in ("dict", "list", "set", "sorted", "len", "print", "repr"):
                return True  # namespace handed to a function that may fill it (protobuf builder, injectors)
    return False


def _static_all(value):
    if isinstance(value, (ast.List, ast.Tuple)):
        out = []
        for e in value.elts:
            if isinstance(e, ast.Constant) and isinstance(e.value, str):
                out.append(e.value)
            else:
                return None
        return out
    return None


def parse_module(src, is_stub=False):
    RUNTIME_ONLY[0] = not is_stub
    try:
        return _parse_module(src, is_stub)
    finally:
        RUNTIME_ONLY[0] = True


def _parse_module(src, is_stub=False):
    res = {"all": None, "defs": {}, "imports": {}, "stars": [], "error": None,
           "getattr_names": [], "getattr_deprecates": False, "has_getattr": False, "getattr_closed": False,
           "getattr_raises": [], "getattr_deprecated_names": [], "dynamic_globals": False}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(src)
    except Exception as e:  # Python 2 sources, generated junk, etc.
        res["error"] = type(e).__name__
        return res
    all_names, all_dynamic = None, False
    bound = lambda n: n in res["defs"] or n in res["imports"]
    containers = {}
    getattr_node = None
    for s, fb in _walk_body(tree.body, flagged=True):
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)) and s.name == "__getattr__":
            res["getattr_names"] = _getattr_names(s)
            res["getattr_deprecates"] = _is_deprecated(s)
            res["has_getattr"] = True
            res["getattr_closed"] = _getattr_closed(s)
            getattr_node = s
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if fb and bound(s.name):
                continue
            info = _function(s, False)
            prev = res["defs"].get(s.name)
            if prev is None or prev.get("kind") != "function" or \
                    (not fb and not info["overload"] and not prev.get("overload")):
                # runtime semantics: a later definition in the primary branch replaces an earlier one
                info["_ov"] = [info["sig"]] if info["overload"] else None
                res["defs"][s.name] = info
                res["imports"].pop(s.name, None)
            elif prev.get("overload") and info["overload"]:
                prev["_ov"].append(info["sig"]); prev["sig"] = merge_overloads(prev["_ov"])
            elif prev.get("overload") and not info["overload"]:
                info["_ov"] = None
                res["defs"][s.name] = info
        elif isinstance(s, ast.ClassDef):
            if fb and bound(s.name):
                continue
            res["defs"][s.name] = _class(s)
            res["imports"].pop(s.name, None)
        elif isinstance(s, ast.ImportFrom):
            for al in s.names:
                if al.name == "*":
                    res["stars"].append((s.module or "", s.level))
                else:
                    n = al.asname or al.name
                    if fb and bound(n):
                        continue
                    res["imports"][n] = (s.module or "", s.level, al.name)
                    if al.asname and al.asname == al.name:
                        res.setdefault("reexports", []).append(n)  # PEP 484 explicit re-export
                    res["defs"].pop(n, None)
        elif isinstance(s, ast.Import):
            for al in s.names:
                if al.asname:
                    if fb and bound(al.asname):
                        continue
                    res["imports"][al.asname] = (al.name, 0, None)
                else:
                    top = al.name.split(".")[0]
                    res["imports"].setdefault(top, (top, 0, None))
        elif isinstance(s, ast.Delete) and not fb:
            # `del get_versions` after `__version__ = get_versions()[...]` (versioneer): the name is gone at runtime
            for t in s.targets:
                if isinstance(t, ast.Name):
                    res["defs"].pop(t.id, None); res["imports"].pop(t.id, None)
        else:
            names = _targets(s)
            if "__all__" in names:
                if isinstance(s, ast.AugAssign):
                    extra = _static_all(s.value)
                    if extra is None or all_names is None:
                        all_dynamic = True
                    else:
                        all_names = all_names + extra
                else:
                    v = _static_all(s.value)
                    if v is None:
                        all_dynamic = True
                    else:
                        all_names = v
            val = getattr(s, "value", None)
            if len(names) == 1 and val is not None:
                lit = _literal_names(val)
                if lit:
                    containers[names[0]] = lit
            for t in names:
                if t == "__all__" or (fb and bound(t)):
                    continue  # fallback branches never override the primary binding
                if isinstance(val, ast.Name) and val.id == t:
                    continue  # `X = X`
                if isinstance(s, ast.AugAssign):
                    res["defs"].setdefault(t, {"kind": "attribute"}); continue
                res["defs"][t] = _assigned_kind(val) if len(names) == 1 else {"kind": "attribute"}
                res["imports"].pop(t, None)
        # __all__.extend([...]) / __all__.append("x")
        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call):
            f = s.value.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "__all__":
                if f.attr == "extend" and s.value.args and all_names is not None:
                    v = _static_all(s.value.args[0])
                    if v is None:
                        all_dynamic = True
                    else:
                        all_names = all_names + v
                elif f.attr == "append" and s.value.args and all_names is not None and \
                        isinstance(s.value.args[0], ast.Constant):
                    all_names = all_names + [s.value.args[0].value]
                else:
                    all_dynamic = True
    if all_names is not None and not all_dynamic:
        res["all"] = list(dict.fromkeys(all_names))
    res["all_dynamic"] = bool(all_dynamic)
    if getattr_node is not None:
        foreign = []
        res["getattr_raises"] = list(dict.fromkeys(_getattr_raises(getattr_node, containers, foreign)))
        # names served from module-level containers (deprecation shims such as numpy's __deprecated_attrs__);
        # names the same __getattr__ explicitly rejects are not served
        served = _served_containers(tree)
        # rejection lists may also be built by comprehensions (numpy's __former_attrs__)
        res["getattr_raises"] = list(dict.fromkeys(res["getattr_raises"] + _getattr_raises(
            getattr_node, {**served, **containers})))
        rejected = set(res["getattr_raises"])
        extra = [k for c in _getattr_container_lookups(getattr_node) for k in served.get(c, [])
                 if k.isidentifier() and k not in rejected]
        res["getattr_names"] = list(dict.fromkeys(res["getattr_names"] + extra))
        # a container looked up in a branch that issues a deprecation warning serves deprecated names
        warned = set()
        for n in ast.walk(getattr_node):
            if isinstance(n, (ast.If, ast.Try)) and any(isinstance(x, ast.Call) and _is_warn_call(x)
                                                       for x in ast.walk(n)):
                head = ast.Module(body=n.body if isinstance(n, ast.Try) else [ast.Expr(n.test)], type_ignores=[])
                fake = ast.FunctionDef(name="f", args=getattr_node.args, body=head.body, decorator_list=[])
                warned.update(_getattr_container_lookups(fake, mode="any"))
        res["getattr_deprecated_names"] = [k for c in warned for k in served.get(c, []) if k in extra]
        res["getattr_raise_containers"] = list(dict.fromkeys(foreign))
    res["containers"] = {k: v for k, v in containers.items() if k.startswith("__") or k.isupper()}
    res["dynamic_globals"] = _dynamic_globals(tree)
    for n in _literal_loop_names(tree):
        if n not in res["defs"] and n not in res["imports"]:
            res["defs"][n] = {"kind": "attribute"}
    for v in res["defs"].values():
        v.pop("overload", None); v.pop("_ov", None)
        for m in v.get("members", {}).values():
            m.pop("overload", None); m.pop("_ov", None)
    return res
