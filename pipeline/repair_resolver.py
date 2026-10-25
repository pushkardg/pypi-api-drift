"""Runs INSIDE an environment where only the target library release is installed.
Reads JSON lines {"key":..., "code":..., "tops": [...]} on stdin; prints JSON lines with a verdict for each:
  valid    every name from the library resolves (imports, attribute chains, methods on objects built from library
           classes) and every call binds its keyword and positional arguments (inspect.signature().bind_partial);
           the snippet is never executed
  invalid  some name does not resolve or some call does not bind (reason given)
  no_library_use  the code never references the library
Calls whose callables have no introspectable signature are not judged (counted in "unbound").
With "strict": true in an item, calls must bind completely (inspect.signature().bind): every required parameter
must be supplied as well. This is the post-hoc sensitivity check; the preregistered metric uses bind_partial."""
import ast, importlib, inspect, json, sys


def check(code, tops, strict=False):
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"verdict": "invalid", "reason": f"syntax error: {e.msg}"}
    env, types = {}, {}   # local name -> object ; local var -> class object
    used, unbound = 0, 0

    def is_lib(modname):
        return modname.split(".")[0] in tops

    def resolve(expr):
        parts = []
        while isinstance(expr, ast.Attribute):
            parts.append(expr.attr); expr = expr.value
        if not isinstance(expr, ast.Name):
            return None, None
        parts.append(expr.id); parts.reverse()
        if parts[0] in env:
            obj, base = env[parts[0]], parts[0]
        elif parts[0] in types:
            obj, base = types[parts[0]], parts[0]
        else:
            return None, None
        for p in parts[1:]:
            try:
                obj = getattr(obj, p)
            except Exception:
                # a submodule not imported yet
                mod = getattr(obj, "__name__", None)
                if inspect.ismodule(obj) and mod:
                    try:
                        obj = importlib.import_module(f"{mod}.{p}"); continue
                    except Exception:
                        pass
                raise AttributeError(f"{'.'.join(parts)}: no attribute {p!r}")
        return obj, ".".join(parts)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if is_lib(a.name):
                    try:
                        m = importlib.import_module(a.name)
                    except Exception as e:
                        return {"verdict": "invalid", "reason": f"import {a.name}: {type(e).__name__}: {e}"[:300]}
                    used += 1
                    if a.asname:
                        env[a.asname] = m
                    else:
                        env[a.name.split(".")[0]] = importlib.import_module(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0 and is_lib(node.module):
            try:
                m = importlib.import_module(node.module)
            except Exception as e:
                return {"verdict": "invalid", "reason": f"from {node.module}: {type(e).__name__}: {e}"[:300]}
            for a in node.names:
                if a.name == "*":
                    continue
                if hasattr(m, a.name):
                    env[a.asname or a.name] = getattr(m, a.name)
                else:
                    try:
                        env[a.asname or a.name] = importlib.import_module(f"{node.module}.{a.name}")
                    except Exception:
                        return {"verdict": "invalid", "reason": f"cannot import {a.name} from {node.module}"}
                used += 1
    # assignments of library-class instances, then attribute loads and calls (in source order)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1 and \
                isinstance(node.targets[0], ast.Name):
            try:
                obj, _ = resolve(node.value.func)
            except AttributeError:
                obj = None
            if inspect.isclass(obj):
                types[node.targets[0].id] = obj
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            try:
                obj, name = resolve(node)
            except AttributeError as e:
                return {"verdict": "invalid", "reason": str(e)[:300]}
            if name:
                used += 1
        if isinstance(node, ast.Call):
            try:
                obj, name = resolve(node.func)
            except AttributeError as e:
                return {"verdict": "invalid", "reason": str(e)[:300]}
            if name is None or not callable(obj):
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
                continue
            try:
                sig = inspect.signature(obj)
            except (TypeError, ValueError):
                unbound += 1
                continue
            base = node.func
            n_pos = len(node.args)
            # method looked up on a class through an instance variable: drop self
            if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id in types \
                    and inspect.isfunction(obj):
                n_pos += 1
            try:
                (sig.bind if strict else sig.bind_partial)(*([None] * n_pos), **{k.arg: None for k in node.keywords})
            except TypeError as e:
                return {"verdict": "invalid", "reason": f"{name}(...): {e}"[:300]}
    if not used:
        return {"verdict": "no_library_use", "reason": ""}
    return {"verdict": "valid", "reason": "", "unbound_calls": unbound}


for line in sys.stdin:
    item = json.loads(line)
    try:
        r = check(item["code"], set(item["tops"]), item.get("strict", False))
    except Exception as e:
        r = {"verdict": "error", "reason": f"{type(e).__name__}: {e}"[:300]}
    r["key"] = item["key"]
    print(json.dumps(r), flush=True)
