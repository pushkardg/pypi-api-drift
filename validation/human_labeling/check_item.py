"""Helper for labellers: resolve a dotted path in the CURRENT environment and print what it is.
    python check_item.py airflow.configuration.mkdir_p          # does it exist?
    python check_item.py pandas.read_csv --signature            # and its signature
Imports the longest importable module prefix, then walks attributes. Exit code 0 = found, 1 = not found."""
import importlib, inspect, sys

path, want_sig = sys.argv[1], "--signature" in sys.argv
parts = path.split(".")
obj, i = None, len(parts)
while i > 0:
    try:
        obj = importlib.import_module(".".join(parts[:i])); break
    except Exception:
        i -= 1
if obj is None:
    print(f"NOT FOUND: cannot import {parts[0]}"); sys.exit(1)
for k, p in enumerate(parts[i:], start=i):
    try:
        obj = getattr(obj, p)
    except AttributeError as e:
        print(f"NOT FOUND: {'.'.join(parts[:k])} has no attribute {p!r} ({e})"); sys.exit(1)
print("FOUND:", type(obj).__name__, getattr(obj, "__module__", ""))
if want_sig:
    try:
        print("signature:", inspect.signature(obj))
    except (TypeError, ValueError) as e:
        print("signature: not introspectable:", e)
