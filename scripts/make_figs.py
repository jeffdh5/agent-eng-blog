"""Generate the two compaction figures as SVGs from measured demo data."""

import json
from pathlib import Path

data = json.loads(Path('/tmp/compaction_demo.json').read_text())
rows = data['rows']
totals = data['totals']

OUT = Path('/Users/jeffhuang/Desktop/agent-eng-blog/public/figures')
OUT.mkdir(parents=True, exist_ok=True)

# palette (matches site)
TEXT = '#141414'
MUTED = '#5a5a5a'
FAINT = '#8c8c8c'
BORDER = '#d4d1c9'
GREEN = '#2a4a3a'    # bulk bytes entering via tool results
RUST = '#9a4a2a'     # same bytes re-entering via tool args
GRAY = '#c9c6bd'     # prose / small requests
FONT = "font-family='Inter, system-ui, sans-serif'"
MONO = "font-family='IBM Plex Mono, ui-monospace, monospace'"

KIND_COLOR = {'prose': GRAY, 'req': GRAY, 'payload': GREEN, 'dup': RUST}


def fmt(n: int) -> str:
    return f'{n / 1000:.1f}k' if n >= 1000 else str(n)


# ---------------- figure 1: anatomy of the raw history ----------------
SHORTEN = {
    'model · write_file request (whole file in args)': 'model · write_file request (whole file)',
    'user · read_file delivery (same file again)': 'user · read_file delivery (again)',
    'user · read_file delivery (artifacts_test.py)': 'user · read_file delivery (test file)',
    'user · read_file delivery (_filesystem.py)': 'user · read_file delivery (source)',
    'model · edit_file request (patch in args)': 'model · edit_file request (patch)',
}
ROW_H = 21
LABEL_X = 296
BAR_X = 304
W = 720
MAX_BAR = W - BAR_X - 52
max_chars = max(r['before'] for r in rows)
scale = MAX_BAR / max_chars

legend_h = 34
H = legend_h + len(rows) * ROW_H + 14

parts = [
    f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {H}' role='img' "
    f"aria-label='Per-message sizes of a {len(rows)}-message coding agent transcript. "
    f"File deliveries and tool arguments dwarf all prose.'>",
]

# legend
lx = 12
for color, label, est in [
    (GRAY, 'prose &amp; tool requests', 'prose + tool requests'),
    (GREEN, 'file/log bulk (reads &amp; tool output)', 'file/log bulk'),
    (RUST, 'same bytes again, in tool args', 'same bytes again, in tool args'),
]:
    parts.append(f"<rect x='{lx}' y='8' width='10' height='10' rx='2' fill='{color}'/>")
    parts.append(f"<text x='{lx + 15}' y='17' font-size='11' fill='{MUTED}' {FONT}>{label}</text>")
    lx += 15 + 6.2 * len(est) + 26

y = legend_h
for r in rows:
    w = max(2, r['before'] * scale)
    color = KIND_COLOR[r['kind']]
    label = f"{r['idx']:>2}  {SHORTEN.get(r['label'], r['label'])}"
    parts.append(
        f"<text x='{LABEL_X}' y='{y + 13}' font-size='10.5' fill='{MUTED}' text-anchor='end' {MONO}>{label}</text>"
    )
    parts.append(f"<rect x='{BAR_X}' y='{y + 4}' width='{w:.1f}' height='12' rx='2' fill='{color}'/>")
    parts.append(
        f"<text x='{BAR_X + w + 6:.1f}' y='{y + 14}' font-size='10.5' fill='{FAINT}' {MONO}>{fmt(r['before'])}</text>"
    )
    y += ROW_H

parts.append('</svg>')
(OUT / 'compaction-anatomy.svg').write_text('\n'.join(parts))

# ---------------- figure 2: two stages ----------------
W2 = 720
BAR_X2 = 8
MAX_BAR2 = W2 - BAR_X2 - 16
scale2 = MAX_BAR2 / totals['before']
STAGE_GAP = 64
BAR_H = 22
H2 = 2 * STAGE_GAP + 6

stages = [
    ('before', 'raw message history', 'every read result, log, and write argument rides along'),
    ('clip', 'after structural compaction', 'old read deliveries and tool args clipped; recent 6 messages untouched'),
]

parts = [
    f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W2} {H2}' role='img' "
    f"aria-label='The same transcript at two compaction stages, drawn to scale: "
    f"{fmt(totals['before'])} chars raw, {fmt(totals['clip'])} after structural compaction.'>",
]

y = 4
for key, title, note in stages:
    total = totals[key]
    parts.append(
        f"<text x='{BAR_X2}' y='{y + 12}' font-size='12' fill='{TEXT}' {FONT}>{title}"
        f"<tspan fill='{FAINT}'>  ·  {note}</tspan></text>"
    )
    parts.append(
        f"<text x='{W2 - 8}' y='{y + 12}' font-size='12' fill='{TEXT}' text-anchor='end' {MONO}>{fmt(total)} chars</text>"
    )
    x = BAR_X2
    by = y + 20
    for r in rows:
        w = r[key] * scale2
        if w <= 0:
            continue
        color = KIND_COLOR[r['kind']]
        parts.append(
            f"<rect x='{x:.2f}' y='{by}' width='{max(w, 0.8):.2f}' height='{BAR_H}' "
            f"fill='{color}' stroke='#f6f5f2' stroke-width='0.5'/>"
        )
        x += max(w, 0.8)
    y += STAGE_GAP

parts.append('</svg>')
(OUT / 'compaction-stages.svg').write_text('\n'.join(parts))
print('wrote', list(p.name for p in OUT.iterdir()))
