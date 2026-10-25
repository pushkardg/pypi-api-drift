import re, sys
from PIL import Image, ImageDraw, ImageFont
D = "/tmp/demo"
F = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 15)
FB = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", 15)
CW = F.getbbox("M")[2]; LH = 22; PAD = 18; TOP = 38
BG, FG, DIM, GRN, RED, YEL, CYA, PROMPT = "#0d1117", "#e6edf3", "#7d8590", "#3fb950", "#ff7b72", "#d29922", "#79c0ff", "#3fb950"
check = open(f"{D}/check.txt").read().splitlines()
W = PAD * 2 + CW * max(len(l) for l in check) + 10
ROWS = 21; H = TOP + PAD + LH * ROWS
lines = []  # list of list of (text, color, bold)
frames = []

def draw():
    im = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 28], fill="#161b22")
    for i, c in enumerate(("#ff5f56", "#ffbd2e", "#27c93f")):
        d.ellipse([14 + i * 20, 9, 26 + i * 20, 21], fill=c)
    d.text((W // 2, 14), "pip install pypi-api-drift", fill=DIM, font=F, anchor="mm")
    for r, segs in enumerate(lines[-ROWS:]):
        x = PAD
        for t, c, b in segs:
            d.text((x, TOP + r * LH), t, fill=c, font=FB if b else F); x += CW * len(t)
    return im

def hold(ms):
    frames.append((draw(), ms))

def type_cmd(cmd, comment=False):
    lines.append([("$ ", PROMPT, True)])
    for i in range(1, len(cmd) + 1, 2):
        lines[-1] = [("$ ", PROMPT, True), (cmd[:i], DIM if comment else FG, False)]
        hold(45)
    lines[-1] = [("$ ", PROMPT, True), (cmd, DIM if comment else FG, False)]
    hold(500)

def out(segs_list, ms=60):
    for s in segs_list:
        lines.append(s); hold(ms)

def colour_check(l):
    m = re.match(r"(\S+?:\d+:\d+:) (\[\w+\]) (\S+)( .*)", l)
    loc, kind, sym, rest = m.groups()
    return [(loc + " ", DIM, False), (kind, RED if kind == "[missing]" else YEL, True), (" ", FG, False),
            (sym, CYA, True), (rest, FG, False)]

hold(600)
type_cmd("# AI-written code, checked against the versions you actually pin", comment=True)
type_cmd("cat requirements.txt")
out([[(l, FG, False)] for l in open(f"{D}/requirements.txt").read().splitlines()])
hold(900)
type_cmd("cat app.py")
out([[(l, FG, False)] for l in open(f"{D}/app.py").read().splitlines()], ms=90)
hold(1800)
type_cmd("apidrift-check app.py -r requirements.txt")
hold(500)
out([colour_check(l) for l in check], ms=350)
hold(1200); type_cmd("# 4 version-drift errors, found without running the code", comment=True); hold(5000)

ims = [f[0] for f in frames]; durs = [f[1] for f in frames]
pal = ims[-1].quantize(colors=64, method=Image.Quantize.MEDIANCUT)
q = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in ims]
q[0].save(sys.argv[1], save_all=True, append_images=q[1:], duration=durs, loop=0, optimize=True, disposal=1)
ims[-1].save(sys.argv[1].replace(".gif", "_last.png"))
print(len(frames), "frames", sum(durs) / 1000, "s", W, H)
