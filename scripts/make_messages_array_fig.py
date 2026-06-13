"""Generate compaction-messages-array.svg: the messages[] array with per-index heat."""

from __future__ import annotations

import json
from pathlib import Path

data = json.loads(Path('/tmp/compaction_demo.json').read_text())
rows = data['rows']
cutoff_idx = data['cutoff_index']  # 0-based start of keep window
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
GREEN_LITE = '#6a9a7a'
RUST = '#9a4a2a'
RUST_LITE = '#c47a5a'
GRAY = '#c9c6bd'
GRAY_DARK = '#8a8880'
KEEP_TINT = '#f3f1ec'
CLIP = '#4a6a8a'
MODEL_ARROW = '#7a6a5a'
FONT = "font-family='Inter, system-ui, sans-serif'"
MONO = "font-family='IBM Plex Mono, ui-monospace, monospace'"

KIND_FILL = {'prose': GRAY, 'req': GRAY_DARK, 'payload': GREEN_LITE, 'dup': RUST_LITE}
KIND_STROKE = {'prose': GRAY, 'req': GRAY_DARK, 'payload': GREEN, 'dup': RUST}

SHORT = {
    'user · task': 'user · task',
    'model · read_file request': 'model · tool_call read_file',
    'tool · read_file ack': 'tool · ack (queued)',
    'user · read_file delivery (_filesystem.py)': 'user · <read_file> body',
    'user · read_file delivery (artifacts_test.py)': 'user · <read_file> body',
    'user · read_file delivery (same file again)': 'user · <read_file> body',
    'model · execute request (pytest)': 'model · tool_call execute',
    'tool · execute result (traceback)': 'tool · pytest traceback',
    'model · edit_file request (patch in args)': 'model · tool_call edit_file',
    'tool · edit_file result': 'tool · ack',
    'model · write_file request (whole file in args)': 'model · tool_call write_file',
    'tool · write_file result': 'tool · ack',
    'tool · execute result (pass)': 'tool · pytest pass',
    'model · summary': 'model · summary',
    'user · follow-up task': 'user · follow-up',
    'model · working': 'model · working',
}


def esc(s: str) -> str:
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def fmt(n: int) -> str:
    return f'{n / 1000:.1f}k' if n >= 1000 else str(n)


def slot_height(chars: int, *, max_chars: int) -> int:
    if chars <= 120:
        return 22
    return min(96, 22 + int((chars / max_chars) * 74))


def array_column(
    parts: list[str],
    *,
    x: int,
    y0: int,
    col_w: int,
    title: str,
    subtitle: str,
    total_chars: int,
    key: str,
    compacted: bool,
) -> int:
    max_chars = max(r[key] for r in rows)
    slot_gap = 3
    header_h = 52
    bracket_pad = 14

    parts.append(f"<text x='{x}' y='{y0 + 16}' font-size='13' fill='{TEXT}' {FONT}>{esc(title)}</text>")
    parts.append(f"<text x='{x}' y='{y0 + 32}' font-size='10' fill='{FAINT}' {FONT}>{esc(subtitle)}</text>")
    parts.append(f"<text x='{x + col_w - 8}' y='{y0 + 16}' font-size='11' fill='{TEXT}' text-anchor='end' {MONO}>{fmt(total_chars)} chars</text>")

    y = y0 + header_h
    inner_x = x + bracket_pad
    inner_w = col_w - bracket_pad * 2

    parts.append(f"<text x='{inner_x}' y='{y + 11}' font-size='10' fill='{MUTED}' {MONO}>messages = [</text>")
    y += 18

    slot_ys: list[tuple[int, int, dict]] = []
    for r in rows:
        i = r['idx'] - 1
        chars = r[key]
        h = slot_height(chars, max_chars=max_chars)
        in_keep = r['in_keep_window']
        kind = r['kind']
        fill = KIND_FILL[kind]
        stroke = KIND_STROKE[kind]
        hot = chars > 800

        if in_keep and not compacted:
            parts.append(f"<rect x='{inner_x - 2}' y='{y - 1}' width='{inner_w + 4}' height='{h + 2}' fill='{KEEP_TINT}' rx='3'/>")
        if compacted and i < cutoff_idx and chars < r['before']:
            parts.append(f"<rect x='{inner_x - 2}' y='{y - 1}' width='{inner_w + 4}' height='{h + 2}' fill='#eef3f7' rx='3'/>")

        parts.append(
            f"<rect x='{inner_x}' y='{y}' width='{inner_w}' height='{h}' rx='4' "
            f"fill='{fill}' stroke='{stroke}' stroke-width='{1.4 if hot else 0.8}'/>"
        )

        label = SHORT.get(r['label'], r['label'])
        parts.append(
            f"<text x='{inner_x + 8}' y='{y + 13}' font-size='9' fill='{TEXT}' {MONO}>"
            f"[{i}] {esc(label)}</text>"
        )
        parts.append(
            f"<text x='{inner_x + inner_w - 8}' y='{y + 13}' font-size='9' fill='{TEXT}' text-anchor='end' {MONO}>"
            f"{fmt(chars)}</text>"
        )
        if h > 36:
            parts.append(
                f"<text x='{inner_x + 8}' y='{y + 28}' font-size='8.5' fill='{MUTED}' {MONO}>"
                f"role={r['role']} · hot slot</text>"
            )

        slot_ys.append((y, h, r))
        y += h + slot_gap

    parts.append(f"<text x='{inner_x}' y='{y + 11}' font-size='10' fill='{MUTED}' {MONO}>]</text>")
    y += 18

    # bracket lines
    top = y0 + header_h + 10
    bot = y - 8
    bx = x + 6
    parts.append(f"<path d='M {bx} {top} L {bx - 6} {top} L {bx - 6} {bot} L {bx} {bot}' fill='none' stroke='{BORDER}' stroke-width='1.5'/>")
    parts.append(f"<path d='M {x + col_w - 6} {top} L {x + col_w} {top} L {x + col_w} {bot} L {x + col_w - 6} {bot}' fill='none' stroke='{BORDER}' stroke-width='1.5'/>")

    return y


W = 1040
COL_W = 480
LEFT_X = 24
RIGHT_X = 536
y_start = 72

parts: list[str] = [
    f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} 920' role='img' "
    f"aria-label='The messages array at turn 21, with per-index token heat, before and after compaction.'>",
    f"<rect width='{W}' height='920' fill='{BG}'/>",
    f"<text x='24' y='30' font-size='15' fill='{TEXT}' {FONT}>The messages array is where the context lives</text>",
    f"<text x='24' y='50' font-size='11' fill='{FAINT}' {FONT}>Every model call sends the entire list. Slot height is drawn to token weight (~chars/4). Real 21-message run.</text>",
]

lx = 24
for color, label, stroke in [(GRAY, 'cool', GRAY), (GREEN_LITE, 'file/log bulk', GREEN), (RUST_LITE, 'tool args bloat', RUST)]:
    parts.append(f"<rect x='{lx}' y='58' width='10' height='10' rx='2' fill='{color}' stroke='{stroke}' stroke-width='0.6'/>")
    parts.append(f"<text x='{lx + 14}' y='67' font-size='10' fill='{MUTED}' {FONT}>{label}</text>")
    lx += 14 + len(label) * 6 + 22

y_left_end = array_column(
    parts,
    x=LEFT_X,
    y0=y_start,
    col_w=COL_W,
    title='messages at turn 21',
    subtitle='passed verbatim to the model',
    total_chars=totals['before'],
    key='before',
    compacted=False,
)

y_right_end = array_column(
    parts,
    x=RIGHT_X,
    y0=y_start,
    col_w=COL_W,
    title='messages after wrap_generate',
    subtitle=f'prefix compacted; [{cutoff_idx}:] keep window verbatim',
    total_chars=totals['clip'],
    key='clip',
    compacted=True,
)

col_h = max(y_left_end, y_right_end) - y_start
mid_y = y_start + 80 + col_h / 2

# arrow between columns
parts.append(f"<text x='{W / 2}' y='{mid_y - 8}' font-size='18' fill='{CLIP}' text-anchor='middle' {FONT}>→</text>")
parts.append(f"<text x='{W / 2}' y='{mid_y + 10}' font-size='9' fill='{CLIP}' text-anchor='middle' {MONO}>wrap_generate</text>")

# model call annotation under left column
arrow_y = max(y_left_end, y_right_end) + 24
parts.append(f"<rect x='{LEFT_X}' y='{arrow_y}' width='{COL_W}' height='36' rx='6' fill='white' stroke='{BORDER}'/>")
parts.append(
    f"<text x='{LEFT_X + 16}' y='{arrow_y + 14}' font-size='10' fill='{MUTED}' {MONO}>"
    f"await ai.generate(messages=messages, ...)</text>"
)
parts.append(
    f"<text x='{LEFT_X + 16}' y='{arrow_y + 28}' font-size='9.5' fill='{FAINT}' {FONT}>"
    f"all {len(rows)} slots ride along on every subsequent call until compaction shrinks the prefix</text>"
)

# footer
foot_y = arrow_y + 52
parts.append(f"<rect x='24' y='{foot_y}' width='992' height='40' rx='6' fill='white' stroke='{BORDER}'/>")
parts.append(
    f"<text x='36' y='{foot_y + 16}' font-size='10.5' fill='{TEXT}' {FONT}>"
    f"Hot zones: messages[3], [6], [8], [9], [11], [19] hold most of the ~12k tokens. "
    f"Compaction targets the prefix; the tail stays verbatim.</text>"
)
parts.append(
    f"<text x='36' y='{foot_y + 30}' font-size='9.5' fill='{FAINT}' {FONT}>"
    f"Summarization (not shown) replaces a large prefix with one handoff message when usage crosses 85% of budget.</text>"
)

parts.append('</svg>')
(OUT / 'compaction-messages-array.svg').write_text('\n'.join(parts))
print('wrote', OUT / 'compaction-messages-array.svg')
