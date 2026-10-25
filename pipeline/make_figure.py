"""Figure 1 of the paper, computed from the released tables with the same definitions as stats.py.
Left: deduplicated high-confidence breaking changes (breaking, representative, not parent_removed, confidence high)
by year of the breaking release's upload. Right: share of release transitions with at least one breaking change, by
version increment, calendar-versioned releases excluded (stats.json *_with_breaking_share).
Usage: python make_figure.py DATA_DIR STATS_JSON OUT_PDF"""
import json, sys
import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

D, ST, OUT = sys.argv[1:4]
S = json.load(open(ST))
rows = duckdb.sql(f"""
select year(r.upload_time::timestamp) y, count(*) n
from '{D}/changes.parquet' c join '{D}/releases.parquet' r on c.project = r.project and c.to_version = r.version
where c.breaking and not c.parent_removed and c.representative and c.confidence = 'high'
group by 1 order by 1""").fetchall()
first = 2021  # selection starts at 2021-01-01; a release whose wheel was uploaded earlier is counted with 2021
agg = {}
for y, n in rows:
    agg[max(int(y), first)] = agg.get(max(int(y), first), 0) + int(n)
years = sorted(agg); counts = [agg[y] for y in years]
assert sum(counts) == S["breaking_representative_high"], (sum(counts), S["breaking_representative_high"])

plt.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42, "font.family": "serif",
                     "font.serif": ["Nimbus Roman", "Times New Roman", "Times", "DejaVu Serif"], "font.size": 9})
grey, blue = "#9aa0a6", "#3b6ea8"
fig, (a, b) = plt.subplots(1, 2, figsize=(7.0, 2.3), gridspec_kw={"width_ratios": [1.25, 1]})
last = max(years)
bars = a.bar([("≤" if y == first else "") + str(y) for y in years], counts, color=[blue if y >= 2025 else grey for y in years], width=0.6)
for r, n in zip(bars, counts):
    a.text(r.get_x() + r.get_width() / 2, n, f"{n:,}", ha="center", va="bottom", fontsize=7.5)
a.set_title(f"High-confidence breaking changes by release year ({last}: Jan–{S.get('snapshot_month_short', 'Oct')})",
            fontsize=8.5)
a.set_yticks([]); [a.spines[k].set_visible(False) for k in ("top", "right", "left")]
a.set_ylim(0, max(counts) * 1.18)
labels = ["patch", "minor", "major"]
shares = [S["patch_with_breaking_share"], S["minor_with_breaking_share"], S["major_with_breaking_share"]]
hb = b.barh(labels, shares, color=[blue, grey, grey], height=0.5)
for r, v in zip(hb, shares):
    b.text(v + 0.01, r.get_y() + r.get_height() / 2, f"{100 * v:.1f}%", va="center", fontsize=7.5)
b.set_title("Releases with at least one breaking change", fontsize=8.5)
b.set_xlim(0, 1); b.set_xticks([]); [b.spines[k].set_visible(False) for k in ("top", "right", "bottom")]
fig.tight_layout()
fig.savefig(OUT, bbox_inches="tight"); fig.savefig(OUT.replace(".pdf", ".png"), dpi=200, bbox_inches="tight")
print(dict(zip(years, counts)), "total", sum(counts), "2025+", sum(n for y, n in zip(years, counts) if y >= 2025),
      "shares", shares)
