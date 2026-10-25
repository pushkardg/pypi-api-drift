"""Public-API snapshots of a wheel and diffs between consecutive releases."""
import hashlib, json, re, sys

STDLIB = set(sys.stdlib_module_names) | {"__future__", "typing_extensions"}

SKIP_SEGMENTS = {"tests", "test", "conftest"}
EXT_RE = re.compile(r"\.(cpython-[^.]*|abi3|pypy[^.]*|cp\d+[^.]*)?\.?(so|pyd)$")
BREAKING = {"removed", "param_removed", "param_renamed", "required_param_added", "default_removed",
            "became_keyword_only", "became_positional_only", "positional_reordered", "vararg_removed",
            "varkw_removed", "kind_changed"}


def module_of(path):
    """Map a wheel member to (dotted module, kind) or None. kind: py | pyi | ext."""
    parts = path.split("/")
    if not parts or parts[0].endswith((".dist-info", ".data")) or ".dist-info" in path:
        return None
    last = parts[-1]
    if last.endswith(".py"):
        kind, stem = "py", last[:-3]
    elif last.endswith(".pyi"):
        kind, stem = "pyi", last[:-4]
    else:
        m = EXT_RE.search(last)
        if not m:
            return None
        kind, stem = "ext", last[: m.start()]
    segs = parts[:-1] + ([] if stem == "__init__" else [stem])
    if not segs or not all(s.isidentifier() for s in segs) or any(s in SKIP_SEGMENTS for s in segs):
        return None
    return ".".join(segs), kind, stem == "__init__"


def sig_hash(sig):
    if sig is None:
        return None
    return hashlib.sha1(json.dumps(sig, separators=(",", ":"), sort_keys=True).encode()).hexdigest()[:16]


class Snapshot:
    """modules: {dotted: parsed dict}; is_pkg: {dotted: bool}; ext: set of dotted ext modules."""

    def __init__(self, modules, is_pkg, ext, opaque=()):
        self.m, self.is_pkg, self.ext = modules, is_pkg, ext
        self.opaque = set(opaque)  # modules present but unparseable: treat their contents as unknown
        self.tops = {k.split(".")[0] for k in list(modules) + list(ext)}
        self._pub = {}
        self._res = {}
        self._cm = {}

    def _abs(self, mod, cur, level):
        if level == 0:
            return mod
        base = cur if self.is_pkg.get(cur) else cur.rpartition(".")[0]
        for _ in range(level - 1):
            base = base.rpartition(".")[0]
        return f"{base}.{mod}" if mod else base

    def internal(self, dotted):
        return dotted.split(".")[0] in self.tops

    def public_names(self, M, seen=None):
        if M in self._pub:
            return self._pub[M]
        seen = seen or set()
        if M in seen or M not in self.m:
            return []
        seen.add(M)
        p = self.m[M]
        if p["all"] is not None:
            names = list(p["all"])
        else:
            names = [n for n in p["defs"] if not n.startswith("_")]
            # `from .x import Y as Y` marks an intentional re-export in any module
            names += [n for n in p.get("reexports", []) if not n.startswith("_")]
            if self.is_pkg.get(M):
                for n, (mod, lvl, attr) in p["imports"].items():
                    if n.startswith("_"):
                        continue
                    t = self._abs(mod, M, lvl)
                    # internal re-exports, plus `from thirdparty import name` (e.g. flask.escape from markupsafe)
                    if self.internal(t) or (attr is not None and lvl == 0 and t.split(".")[0] not in STDLIB):
                        names.append(n)
            for mod, lvl in p["stars"]:
                t = self._abs(mod, M, lvl)
                if t in self.m:
                    names.extend(self.public_names(t, seen))
            names = list(dict.fromkeys(names))
        names = list(dict.fromkeys(names + [n for n in p.get("getattr_names", []) if not n.startswith("_")]))
        self._pub[M] = names
        return names

    def resolve(self, M, name, depth=0):
        """-> (kind, payload, defined_in). kind in def | module | external | unresolved"""
        key = (M, name)
        r = self._res.get(key)
        if r is None:
            r = self._resolve(M, name, depth)
            if depth == 0:
                self._res[key] = r
        return r

    def _resolve(self, M, name, depth=0):
        if depth > 12 or M not in self.m:
            return ("unresolved", f"{M}.{name}", None)
        p = self.m[M]
        if name in p["defs"]:
            return ("def", p["defs"][name], M)
        if name in p["imports"]:
            mod, lvl, attr = p["imports"][name]
            a = self._abs(mod, M, lvl)
            if attr is None:
                return ("module", a, None) if a in self.m or a in self.ext else \
                       (("unresolved", a, None) if self.internal(a) else ("external", a, None))
            sub = f"{a}.{attr}"
            if sub in self.m or sub in self.ext:
                return ("module", sub, None)
            if a in self.m:
                return self.resolve(a, attr, depth + 1)
            return ("unresolved", sub, None) if self.internal(a) else ("external", sub, None)
        for mod, lvl in p["stars"]:
            t = self._abs(mod, M, lvl)
            if t in self.m and name in self.public_names(t):
                return self.resolve(t, name, depth + 1)
        sub = f"{M}.{name}"
        if sub in self.m or sub in self.ext:
            return ("module", sub, None)
        if name in p.get("getattr_names", []):
            return ("dynamic", p.get("getattr_deprecates", False) or name in p.get("getattr_deprecated_names", []), M)
        return ("unresolved", sub, None)

    def _base_class(self, M, expr, depth):
        """Resolve a base-class expression written in module M to (class_info, defining_module)."""
        if not expr or depth > 8:
            return None
        expr = expr.split("[")[0]
        head, _, rest = expr.partition(".")
        kind, payload, where = self.resolve(M, head)
        while rest and kind == "module":
            nxt, _, rest = rest.partition(".")
            kind, payload, where = self.resolve(payload, nxt)
        if kind == "def" and not rest and payload.get("kind") == "class":
            return payload, where
        return None

    CLOSED_BASES = {"object", "Generic", "Protocol", "ABC", "typing.Generic", "typing.Protocol", "abc.ABC",
                    "typing_extensions.Protocol"}

    def class_members(self, M, info, depth=0, seen=None):
        if depth == 0:
            k = (M, id(info))
            if k not in self._cm:
                self._cm[k] = self._class_members(M, info)
            return self._cm[k]
        return self._class_members(M, info, depth, seen)

    def _class_members(self, M, info, depth=0, seen=None):
        """-> (members incl. those inherited from classes in this package, members_complete).
        members_complete is False when a base class lives outside the package (or is a builtin container)
        or the class defines __getattr__, i.e. callers may legitimately use members we cannot see."""
        seen = seen or set()
        key = (M, id(info))
        if key in seen or depth > 8:
            return dict(info.get("members", {})), False
        seen.add(key)
        merged, complete = {}, True
        for b in reversed(info.get("bases", [])):
            if b is None or b.split("[")[0] in self.CLOSED_BASES or b.startswith("metaclass"):
                continue
            r = self._base_class(M, b, depth + 1)
            if r:
                mem, ok = self.class_members(r[1], r[0], depth + 1, seen)
                merged.update(mem); complete &= ok
            else:
                complete = False
        merged.update(info.get("members", {}))
        if "__getattr__" in merged or "__getattribute__" in merged or info.get("synthetic"):
            complete = False
        return merged, complete

    # ---- runtime accessibility (is `path` still reachable by attribute access / import?)
    def accessible(self, path):
        """True / False, or None when it cannot be decided statically (compiled code, external objects,
        module __getattr__, classes with members we can't see). self.last_reason explains a None."""
        self.last_reason = None
        parts = path.split(".")
        for k in range(len(parts), 0, -1):
            mod = ".".join(parts[:k])
            if mod in self.m or mod in self.ext or mod in self.opaque:
                break
        else:
            return False
        if mod not in self.m:
            return None
        return self._acc_module(mod, parts[k:], 0)

    def _acc_module(self, M, rest, depth):
        if not rest:
            return True
        if depth > 12:
            return None
        p = self.m[M]
        n = rest[0]
        if n in p["defs"]:
            return self._acc_def(M, p["defs"][n], rest[1:])
        if n in p["imports"]:
            mod, lvl, attr = p["imports"][n]
            a = self._abs(mod, M, lvl)
            if attr is None:
                return self._acc_target_module(a, rest[1:], depth)
            sub = f"{a}.{attr}"
            if sub in self.m or sub in self.ext or sub in self.opaque:
                return self._acc_target_module(sub, rest[1:], depth)
            if a in self.m:
                return self._acc_module(a, [attr] + rest[1:], depth + 1)
            return None  # bound to something external / unknown: still accessible as far as we can tell
        unknown = False
        for mod, lvl in p["stars"]:
            t = self._abs(mod, M, lvl)
            if t in self.m:
                tp = self.m[t]
                if n in tp["defs"] or n in tp["imports"] or n in self.public_names(t):
                    return self._acc_module(t, rest, depth + 1)
                # star-imported module whose namespace we can't fully see: compiled (stub only), filled
                # dynamically, or itself star-importing such modules
                if t in self.ext or tp.get("dynamic_globals") or tp.get("has_getattr") or \
                        (tp["all"] is None and tp["stars"] and self._star_opaque(t)):
                    unknown = True
            else:
                unknown = True
        sub = f"{M}.{n}"
        if sub in self.m or sub in self.ext or sub in self.opaque:
            return self._acc_target_module(sub, rest[1:], depth)
        if n in p.get("getattr_raises", []):
            return False  # explicitly rejected by the module's __getattr__ (expired/removed attribute)
        for cname in p.get("getattr_raise_containers", []):  # rejection list imported from another module
            if cname in p["imports"]:
                mod, lvl, attr = p["imports"][cname]
                src = self.m.get(self._abs(mod, M, lvl))
                if src and n in src.get("containers", {}).get(attr or cname, []):
                    return False
        if p.get("has_getattr") and not (p.get("getattr_closed") and n not in p.get("getattr_names", [])):
            return None
        if p.get("dynamic_globals"):  # a runtime-built __all__ changes star exports, not attribute access
            self.last_reason = "dynamic_globals"
            return None
        if unknown:
            return None
        return False

    def _star_opaque(self, M, seen=None):
        """True when some module in M's star-import closure has a namespace we cannot fully see."""
        if seen is None:  # memoized per snapshot (called for every hidden symbol of every release)
            memo = self.__dict__.setdefault("_so", {})
            if M not in memo:
                memo[M] = self._star_opaque(M, set())
            return memo[M]
        if M in seen:
            return False
        seen.add(M)
        p = self.m.get(M)
        if p is None or M in self.ext or M in self.opaque or p.get("dynamic_globals") or \
                (p.get("has_getattr") and not p.get("getattr_closed")):
            return True
        for mod, lvl in p["stars"]:
            t = self._abs(mod, M, lvl)
            if not self.internal(t) or self._star_opaque(t, seen):
                return True
        return False

    def _acc_target_module(self, mod, rest, depth):
        if mod in self.m:
            return self._acc_module(mod, rest, depth + 1)
        return None if (mod in self.ext or mod in self.opaque or not self.internal(mod)) else False

    def _acc_def(self, M, d, rest):
        if not rest:
            return True
        if d.get("kind") == "class":
            members, complete = self.class_members(M, d)
            if rest[0] in members:
                return True if len(rest) == 1 else None
            # a member the package itself used to define is gone; only a __getattr__ could still serve it
            if "__getattr__" in members or "__getattribute__" in members:
                self.last_reason = "class_getattr"
                return None
            return False
        return None

    def symbols(self, sigs):
        """-> {path: (kind, sig_hash, deprecated, defined_in)}; fills sigs {hash: sig}."""
        out = {}

        def put(path, kind, sig, dep, where):
            h = sig_hash(sig)
            if h:
                sigs[h] = sig
            out[path] = (kind, h, bool(dep), where)

        for M in sorted(self.m):
            if any(s.startswith("_") for s in M.split(".")):
                continue
            out[M] = ("module", None, False, M)
            for n in self.public_names(M):
                path = f"{M}.{n}"
                kind, payload, where = self.resolve(M, n)
                if kind == "def":
                    k = payload["kind"]
                    if k == "function":
                        put(path, "function", payload["sig"], payload["deprecated"], where)
                    elif k == "class":
                        members, complete = self.class_members(where, payload)
                        init = None if payload.get("init_generated") else members.get("__init__")
                        # a typing Protocol's __init__ is not something callers invoke
                        proto = any((b or "").split("[")[0].endswith("Protocol") for b in payload.get("bases", []))
                        put(path, "class" if complete else "class_open",
                            init["sig"] if init and "sig" in init and not proto else None, payload["deprecated"], where)
                        if proto:
                            continue  # typing Protocol: members describe an interface, not callable API
                        for mn, mi in members.items():
                            if mn.startswith("_"):
                                continue
                            put(f"{path}.{mn}", mi["kind"], mi.get("sig"), mi.get("deprecated"), where)
                    else:
                        put(path, "attribute", None, False, where)
                elif kind == "module":
                    out.setdefault(path, ("module_alias", None, False, payload))
                elif kind == "dynamic":
                    out[path] = ("dynamic_attribute", None, bool(payload), where)
                elif kind == "external":
                    out[path] = ("external_reexport", None, False, payload)
                else:
                    out[path] = ("unresolved", None, False, payload)
        for E in sorted(self.ext):
            if not any(s.startswith("_") for s in E.split(".")) and E not in out:
                out[E] = ("extension_module", None, False, E)
        return out


def param_events(old, new):
    """Compare two signatures -> list of (change_type, param, detail)."""
    ev = []
    if old is None or new is None:
        return ev
    op = {p[0]: p for p in old["params"]}
    np_ = {p[0]: p for p in new["params"]}
    new_has_vk = any(p[1] == "VK" for p in new["params"])
    old_pos = [p[0] for p in old["params"] if p[1] in ("PO", "PK")]
    new_pos = [p[0] for p in new["params"] if p[1] in ("PO", "PK")]
    removed = [n for n, p in op.items() if n not in np_ and p[1] not in ("VA", "VK")]
    added = [n for n, p in np_.items() if n not in op and p[1] not in ("VA", "VK")]
    renamed = set()
    for n in removed:
        if op[n][1] in ("PO", "PK") and n in old_pos:
            i = old_pos.index(n)
            if i < len(new_pos) and new_pos[i] in added and np_[new_pos[i]][1] == op[n][1]:
                if op[n][1] != "PO":  # a positional-only parameter's name is invisible to callers
                    ev.append(("param_renamed", n, f"{n} -> {new_pos[i]}"))
                renamed.update({n, new_pos[i]})
    for n in removed:
        if n in renamed:
            continue
        ev.append(("param_removed", n, "absorbed by **kwargs" if new_has_vk else ""))
    for n in added:
        if n in renamed:
            continue
        p = np_[n]
        ev.append(("required_param_added" if not p[2] else "optional_param_added", n, p[1]))
    for n in set(op) & set(np_):
        a, b = op[n], np_[n]
        if a[1] in ("VA", "VK"):
            continue
        if a[2] and not b[2] and b[1] not in ("VA", "VK"):
            ev.append(("default_removed", n, f"was {a[3]}"))
        elif a[2] and b[2] and a[3] != b[3]:
            ev.append(("default_changed", n, f"{a[3]} -> {b[3]}"))
        if a[1] in ("PK", "PO") and b[1] == "KO":
            ev.append(("became_keyword_only", n, ""))
        elif a[1] == "PK" and b[1] == "PO":
            ev.append(("became_positional_only", n, ""))
        if a[4] != b[4] and a[4] is not None and b[4] is not None:
            ev.append(("annotation_changed", n, f"{a[4]} -> {b[4]}"))
    common = [n for n in old_pos if n in new_pos]
    if common != [n for n in new_pos if n in common]:
        ev.append(("positional_reordered", "", f"{common} -> {[n for n in new_pos if n in common]}"))
    for k, t in (("VA", "vararg_removed"), ("VK", "varkw_removed")):
        if any(p[1] == k for p in old["params"]) and not any(p[1] == k for p in new["params"]):
            ev.append((t, next(p[0] for p in old["params"] if p[1] == k), ""))
    if old.get("returns") != new.get("returns") and old.get("returns") and new.get("returns"):
        ev.append(("return_annotation_changed", "", f"{old['returns']} -> {new['returns']}"))
    return ev


def _k(kind):
    return "class" if kind == "class_open" else kind


def diff(A, B, sigs):
    """A, B: symbol dicts. Returns list of event dicts."""
    ev = []
    dyn = [p for p, b in B.items() if b[0] == "dynamic_attribute" and p in A and A[p][0] != "dynamic_attribute"]
    dyn_prefix = tuple(p + "." for p in dyn)
    for path, a in A.items():
        b = B.get(path)
        if b is None and dyn_prefix and path.startswith(dyn_prefix):
            continue  # member of an object that is now served lazily by __getattr__
        if b is not None and b[0] == "dynamic_attribute" and a[0] != "dynamic_attribute":
            ev.append({"path": path, "symbol_kind": b[0], "change_type": "became_dynamic", "param": "",
                       "detail": f"{a[0]} -> served by module __getattr__", "old_sig": a[1], "new_sig": None})
            continue
        if b is None:
            ev.append({"path": path, "symbol_kind": a[0], "change_type": "removed", "param": "",
                       "detail": "", "old_sig": a[1], "new_sig": None})
            continue
        if _k(a[0]) != _k(b[0]) and not {a[0], b[0]} <= {"method", "classmethod", "staticmethod", "property", "attribute"} \
                and not {a[0], b[0]} <= {"function", "attribute", "external_reexport", "unresolved"}:
            ev.append({"path": path, "symbol_kind": b[0], "change_type": "kind_changed", "param": "",
                       "detail": f"{a[0]} -> {b[0]}", "old_sig": a[1], "new_sig": b[1]})
            continue
        if a[1] != b[1] and a[1] and b[1]:
            stub = bool(sigs[a[1]].get("stub") or sigs[b[1]].get("stub"))
            for t, prm, det in param_events(sigs[a[1]], sigs[b[1]]):
                ev.append({"path": path, "symbol_kind": b[0], "change_type": t, "param": prm,
                           "detail": (det + " " if det else "") + "[signature from type stub only]" if stub else det,
                           "old_sig": a[1], "new_sig": b[1], "_stub": stub})
        if b[2] and not a[2]:
            ev.append({"path": path, "symbol_kind": b[0], "change_type": "deprecation_marker_added",
                       "param": "", "detail": "", "old_sig": a[1], "new_sig": b[1]})
    for path, b in B.items():
        if path not in A:
            ev.append({"path": path, "symbol_kind": b[0], "change_type": "added", "param": "", "detail": "",
                       "old_sig": None, "new_sig": b[1]})
    for e in ev:
        parent = A.get(e["path"].rpartition(".")[0])
        old = sigs.get(e["old_sig"]) if e.get("old_sig") else None
        med = (e["change_type"] in ("varkw_removed",) or
               (e["change_type"] == "removed" and parent is not None and parent[0] == "class_open") or
               (old is not None and old.get("stub")))
        e["confidence"] = "medium" if med else "high"
    removed = {e["path"] for e in ev if e["change_type"] == "removed"}
    if removed:
        leaves = {p.rpartition(".")[2] for p in removed}
        idx = {}
        for p, b in B.items():
            lf = p.rpartition(".")[2]
            if lf in leaves and p not in A:
                idx.setdefault(lf, []).append(p)
            elif lf in leaves:
                idx.setdefault(lf, []).append(p)
        for e in ev:
            if e["change_type"] == "removed":
                cands = [p for p in idx.get(e["path"].rpartition(".")[2], []) if _k(B[p][0]) == _k(e["symbol_kind"])]
                if cands:
                    e["detail"] = "still available as " + sorted(cands, key=len)[0]
    for e in ev:
        parts = e["path"].split(".")
        e["parent_removed"] = e["change_type"] == "removed" and any(
            ".".join(parts[:k]) in removed for k in range(1, len(parts)))
        if e["change_type"] == "kind_changed":
            old_k = e["detail"].split(" -> ")[0]
            e.pop("_stub", None)
            e["breaking"] = old_k in ("class", "class_open", "module", "module_alias") and e["symbol_kind"] not in (
                "dynamic_attribute", "class", "class_open", "module", "module_alias", "extension_module")
            continue
        e["breaking"] = e["change_type"] in BREAKING and not (
            e["change_type"] == "param_removed" and e["detail"].startswith("absorbed")) and not e.pop("_stub", False)
    return ev
