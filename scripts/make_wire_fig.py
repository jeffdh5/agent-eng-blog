"""Generate compaction-wire.svg: transcript cards with wire format, hot zones, compaction."""

from __future__ import annotations

import json
from pathlib import Path

data = json.loads(Path('/tmp/compaction_demo.json').read_text())
rows = data['rows']
cutoff_msg = data['cutoff_message']
totals = data['totals']

OUT = Path('/Users/jeffhuang/Desktop/agent-eng-blog/public/figures')
OUT.mkdir(parents=True, exist_ok=True)

TEXT = '#141414'
MUTED = '#5a5a5a'
FAINT = '#8c8c8c'
BORDER = '#d4d1c9'
BG = '#f6f5f2'
GREEN = '#2a4a3a'
GREEN_BG = '#e8efe9'
RUST = '#9a4a2a'
RUST_BG = '#f5ebe4'
GRAY = '#c9c6bd'
KEEP_BG = '#faf9f6'
CLIP = '#4a6a8a'
FONT = "font-family='Inter, system-ui, sans-serif'"
MONO = "font-family='IBM Plex Mono, ui-monospace, monospace'"

ROLE_STYLE = {
    'user': ('#3d4f6a', '#e8ecf2', 'user'),
    'model': ('#5a4a3a', '#f0ebe6', 'model'),
    'tool': ('#2a4a3a', '#e8efe9', 'tool'),
}

KIND_ACCENT = {'prose': GRAY, 'req': GRAY, 'payload': GREEN, 'dup': RUST}
HOT_IDXS = {4, 7, 9, 10, 12, 20}


def esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def fmt(n: int) -> str:
    return f'{n / 1000:.1f}k' if n >= 1000 else str(n)


def card_height(lines: int) -> int:
    return 28 + lines * 13


W = 920
parts: list[str] = [
    f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} 1280' role='img' "
    f"aria-label='Coding agent transcript wire format with hot zones and compaction before and after.'>",
    f"<rect width='{W}' height='1280' fill='{BG}'/>",
    f"<text x='16' y='28' font-size='15' fill='{TEXT}' {FONT}>The wire format, hot zones, and what compaction does</text>",
    f"<text x='16' y='48' font-size='11' fill='{FAINT}' {FONT}>Real 21-message run: failing pytest, two reads, patch, rewrite, green run, follow-up. Numbers from the recipe code.</text>",
]

lx = 16
for color, label in [
    (GRAY, 'prose'),
    (GREEN, 'file / log bulk'),
    (RUST, 'dead weight in tool args'),
    (CLIP, 'compacted'),
]:
    parts.append(f"<rect x='{lx}' y='58' width='10' height='10' rx='2' fill='{color}'/>")
    parts.append(f"<text x='{lx + 14}' y='67' font-size='10' fill='{MUTED}' {FONT}>{label}</text>")
    lx += 14 + len(label) * 6.2 + 18

y = 82

parts.append(f"<text x='16' y='{y + 14}' font-size='12' fill='{TEXT}' {FONT}>How read_file lands on the wire</text>")
y += 24
for r in [x for x in rows if x['idx'] in (2, 3, 4)]:
    role = r['role']
    fg, bg, label = ROLE_STYLE.get(role, ROLE_STYLE['user'])
    accent = KIND_ACCENT[r['kind']]
    lines = r['wire_before']
    h = card_height(len(lines))
    parts.append(f"<rect x='16' y='{y}' width='888' height='{h}' rx='6' fill='white' stroke='{BORDER}'/>")
    parts.append(f"<rect x='16' y='{y}' width='5' height='{h}' rx='2' fill='{accent}'/>")
    parts.append(f"<rect x='28' y='{y + 6}' width='44' height='16' rx='4' fill='{bg}'/>")
    parts.append(f"<text x='50' y='{y + 17}' font-size='9' fill='{fg}' text-anchor='middle' {MONO}>{label}</text>")
    parts.append(f"<text x='82' y='{y + 17}' font-size='9' fill='{FAINT}' {MONO}>#{r['idx']} · {fmt(r['before'])} chars</text>")
    ly = y + 30
    for line in lines:
        parts.append(f"<text x='28' y='{ly}' font-size='9.5' fill='{MUTED}' {MONO}>{esc(line)}</text>")
        ly += 13
    y += h + 8

parts.append(f"<text x='28' y='{y + 10}' font-size='10' fill='{FAINT}' {FONT}>The tool ack is tiny. The file body queues as a user message and stays in history unless compaction clips it.</text>")
y += 28

parts.append(f"<text x='16' y='{y + 14}' font-size='12' fill='{TEXT}' {FONT}>Full message list at turn 21</text>")
y += 22
parts.append(f"<rect x='16' y='{y}' width='888' height='{21 * 18 + 8}' rx='6' fill='white' stroke='{BORDER}'/>")
ty = y + 14
for r in rows:
    accent = KIND_ACCENT[r['kind']]
    hot = r['idx'] in HOT_IDXS
    bar_w = max(4, min(220, r['before'] / 70))
    fill = accent if hot else GRAY
    if r['in_keep_window']:
        parts.append(f"<rect x='18' y='{ty - 10}' width='884' height='16' fill='{KEEP_BG}' opacity='0.9'/>")
    parts.append(f"<text x='28' y='{ty}' font-size='9' fill='{FAINT}' {MONO}>#{r['idx']:>2}</text>")
    parts.append(f"<rect x='52' y='{ty - 8}' width='{bar_w:.0f}' height='10' rx='2' fill='{fill}'/>")
    parts.append(f"<text x='{56 + bar_w:.0f}' y='{ty}' font-size='9' fill='{MUTED}' {MONO}>{fmt(r['before'])}</text>")
    short = r['wire_before'][0]
    if len(short) > 62:
        short = short[:59] + '...'
    parts.append(f"<text x='160' y='{ty}' font-size='9' fill='{MUTED}' {MONO}>{esc(short)}</text>")
    ty += 18

y += 21 * 18 + 20
parts.append(f"<line x1='16' y1='{y}' x2='904' y2='{y}' stroke='{CLIP}' stroke-width='1.5' stroke-dasharray='6 4'/>")
parts.append(f"<text x='16' y='{y + 16}' font-size='10' fill='{CLIP}' {FONT}>keep window starts at message #{cutoff_msg} (last 6 messages verbatim)</text>")
y += 32

parts.append(f"<text x='16' y='{y + 14}' font-size='12' fill='{TEXT}' {FONT}>Hot zones: before and after wrap_generate</text>")
y += 26
parts.append(f"<text x='16' y='{y + 10}' font-size='10' fill='{MUTED}' {FONT}>Raw (left)</text>")
parts.append(f"<text x='468' y='{y + 10}' font-size='10' fill='{MUTED}' {FONT}>After compaction (right)</text>")
y += 18

for r in [x for x in rows if x['idx'] in (4, 9, 10, 12)]:
    accent = KIND_ACCENT[r['kind']]
    bg_left = GREEN_BG if r['kind'] == 'payload' else RUST_BG
    lines_b = r['wire_before']
    lines_a = r['wire_after']
    h = max(card_height(len(lines_b)), card_height(len(lines_a))) + 8
    parts.append(f"<rect x='16' y='{y}' width='430' height='{h}' rx='6' fill='{bg_left}' stroke='{accent}' stroke-width='1.2'/>")
    parts.append(f"<text x='28' y='{y + 16}' font-size='9' fill='{accent}' {MONO}>#{r['idx']} · {r['role']} · {fmt(r['before'])} chars</text>")
    ly = y + 30
    for line in lines_b:
        parts.append(f"<text x='28' y='{ly}' font-size='9' fill='{MUTED}' {MONO}>{esc(line)}</text>")
        ly += 13
    parts.append(f"<rect x='468' y='{y}' width='436' height='{h}' rx='6' fill='white' stroke='{CLIP}' stroke-width='1.2'/>")
    parts.append(f"<text x='480' y='{y + 16}' font-size='9' fill='{CLIP}' {MONO}>#{r['idx']} · {r['compact_action']} · {fmt(r['clip'])} chars</text>")
    ly = y + 30
    for line in lines_a:
        parts.append(f"<text x='480' y='{ly}' font-size='9' fill='{MUTED}' {MONO}>{esc(line)}</text>")
        ly += 13
    parts.append(f"<text x='452' y='{y + h / 2 + 4}' font-size='14' fill='{CLIP}' text-anchor='middle' {FONT}>→</text>")
    y += h + 12

y += 8
parts.append(f"<rect x='16' y='{y}' width='888' height='44' rx='6' fill='white' stroke='{BORDER}'/>")
parts.append(
    f"<text x='28' y='{y + 18}' font-size='11' fill='{TEXT}' {FONT}>Structural pass only: "
    f"{fmt(totals['before'])} chars → {fmt(totals['clip'])} chars. "
    f"Summarization layer not triggered at this size.</text>"
)
parts.append(
    f"<text x='28' y='{y + 34}' font-size='10' fill='{FAINT}' {FONT}>wrap_generate clips prefix; keep window untouched. "
    f"wrap_tool offloads fresh 80k+ tool results (not shown here).</text>"
)

parts.append('</svg>')
(OUT / 'compaction-wire.svg').write_text('\n'.join(parts))
print('wrote', OUT / 'compaction-wire.svg')
