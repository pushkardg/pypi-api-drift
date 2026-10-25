"""Fill README/paper templates from computed numbers. Usage: python fill.py NUMBERS_JSON TEMPLATE OUT"""
import json, re, sys

nums = json.load(open(sys.argv[1]))
src = open(sys.argv[2]).read()
missing = sorted(set(re.findall(r"\{\{(\w+)\}\}", src)) - set(nums))
if missing:
    sys.exit(f"missing numbers: {missing}")
tex = sys.argv[3].endswith(".tex")


def fmt(v):
    s = f"{v:,}" if isinstance(v, int) else str(v)
    return s.replace("%", r"\%").replace("\u2013", "--").replace("<", "$<$") if tex else s


out = re.sub(r"\{\{(\w+)\}\}", lambda m: fmt(nums[m.group(1)]), src)
open(sys.argv[3], "w").write(out)
print("wrote", sys.argv[3])
