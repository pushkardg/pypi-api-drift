"""Download one release artifact from PyPI and unpack it into a fresh directory.

Preference: pure wheel (py3-none-any) > cp312 manylinux x86_64 wheel > any wheel > sdist.
Returns (unpack_dir, artifact_filename, top_level_names).
"""
import json, os, re, shutil, sys, tarfile, urllib.request, zipfile
from pathlib import Path


def _score(f):
    fn = f["filename"]
    if f["packagetype"] == "bdist_wheel":
        if fn.endswith("-none-any.whl"):
            return 0
        if "cp312" in fn and "manylinux" in fn and "x86_64" in fn:
            return 1
        if "abi3" in fn and "manylinux" in fn and "x86_64" in fn:
            return 2
        return 3
    if f["packagetype"] == "sdist":
        return 4
    return 9


def choose(project, version):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{project}/{version}/json", timeout=60) as r:
        data = json.load(r)
    files = [f for f in data["urls"] if not f.get("yanked_files")]
    files.sort(key=_score)
    if not files:
        raise RuntimeError("no files")
    return files[0]


def fetch(project, version, dest):
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    f = choose(project, version)
    arc = dest.parent / (dest.name + "__" + f["filename"])
    urllib.request.urlretrieve(f["url"], arc)
    if f["filename"].endswith((".whl", ".zip")):
        with zipfile.ZipFile(arc) as z:
            z.extractall(dest)
        root = dest
    else:
        with tarfile.open(arc) as t:
            t.extractall(dest, filter="data")
        subs = [p for p in dest.iterdir() if p.is_dir()]
        root = subs[0] if len(subs) == 1 else dest
        # src layout
        if (root / "src").is_dir():
            root = root / "src"
    arc.unlink()
    return root, f["filename"], top_level(root, sdist=f["packagetype"] == "sdist")


def top_level(root, sdist=False):
    root = Path(root)
    names = set()
    for p in root.iterdir():
        if p.name.endswith((".dist-info", ".data", ".egg-info")) or p.name.startswith("."):
            continue
        if p.is_dir() and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p.name):
            if any(p.rglob("*.py")) or any(p.rglob("*.pyi")):
                names.add(p.name)
        elif p.suffix in (".py", ".pyi") and p.stem not in ("setup", "conftest", "noxfile", "tasks"):
            names.add(p.stem)
    # drop obvious non-package dirs from sdists
    if sdist:
        names -= {"tests", "test", "docs", "doc", "examples", "benchmarks", "scripts", "tools", "build"}
    else:
        names -= {"tests", "test"}
    return sorted(names)


if __name__ == "__main__":
    print(fetch(sys.argv[1], sys.argv[2], sys.argv[3]))
