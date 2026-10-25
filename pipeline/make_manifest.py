"""Write DATA_MANIFEST.json (SHA-256 and size of every data table). Usage: python make_manifest.py RELEASE_DIR VERSION"""
import hashlib, json, os, sys
R, V = sys.argv[1:3]
files = {}
for f in sorted(os.listdir(f"{R}/data")):
    if f.endswith(".parquet"):
        h = hashlib.sha256()
        with open(f"{R}/data/{f}", "rb") as fh:
            for c in iter(lambda: fh.read(1 << 20), b""):
                h.update(c)
        files[f] = {"sha256": h.hexdigest(), "bytes": os.path.getsize(f"{R}/data/{f}")}
json.dump({"dataset": "pypi-api-drift", "data_version": V, "files": files}, open(f"{R}/DATA_MANIFEST.json", "w"), indent=1)
print(json.dumps(files, indent=1))
