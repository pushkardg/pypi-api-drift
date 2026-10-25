"""Check downloaded data files against DATA_MANIFEST.json before using them.
Usage: python verify_data.py [DATA_DIR]   (default: data/)"""
import hashlib, json, os, sys
d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
m = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "DATA_MANIFEST.json")))
bad = 0
for name, info in m["files"].items():
    p = os.path.join(d, name)
    if not os.path.exists(p):
        print(f"MISSING  {name}"); bad += 1; continue
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    ok = h.hexdigest() == info["sha256"] and os.path.getsize(p) == info["bytes"]
    bad += not ok
    print(f"{'OK      ' if ok else 'MISMATCH'} {name}")
print(f"{m['data_version']}: {len(m['files']) - bad}/{len(m['files'])} files verified")
sys.exit(1 if bad else 0)
