"""Build a realistic coding-agent transcript, run it through the real
Compaction middleware functions, and emit per-message sizes per stage.

Output: /tmp/compaction_demo.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from genkit._core._model import Message
from genkit._core._typing import (
    Part,
    Role,
    TextPart,
    ToolRequest,
    ToolRequestPart,
    ToolResponse,
    ToolResponsePart,
)
from compaction import CompactionConfig, _compact_prefix, _determine_cutoff_index

REPO = Path('/Users/jeffhuang/Desktop/genkit-middleware')
FILE_A = (REPO / 'py/plugins/middleware/src/genkit/plugins/middleware/_filesystem.py').read_text()
FILE_B = (REPO / 'py/plugins/middleware/tests/artifacts_test.py').read_text()

PYTEST_FAIL = (
    '============================= test session starts ==============================\n'
    'platform darwin -- Python 3.12.12, pytest-9.0.2\n'
    'collected 11 items\n\n'
    'plugins/middleware/tests/artifacts_test.py::test_read_artifact_offset FAILED\n\n'
    '=================================== FAILURES ===================================\n'
    '________________________ test_read_artifact_offset _____________________________\n\n'
) + ''.join(
    f'plugins/middleware/src/genkit/plugins/middleware/_filesystem.py:{200 + i}: in _read_file_impl\n'
    f'    slice_start = offset * _MAX_READ_SLICE_BYTES\n'
    for i in range(20)
) + (
    "E   TypeError: can't multiply sequence by non-int of type 'str'\n\n"
    '=========================== short test summary info ============================\n'
    'FAILED plugins/middleware/tests/artifacts_test.py::test_read_artifact_offset\n'
    '========================= 1 failed, 10 passed in 0.41s =========================\n'
)

PYTEST_PASS = (
    '============================= test session starts ==============================\n'
    'platform darwin -- Python 3.12.12, pytest-9.0.2\n'
    'collected 11 items\n\n'
    'plugins/middleware/tests/artifacts_test.py ...........                   [100%]\n\n'
    '============================== 11 passed in 0.38s ==============================\n'
)

EDIT_OLD = FILE_A[6000:7200]
EDIT_NEW = EDIT_OLD.replace('offset', 'int(offset)', 1)


def user(text: str) -> Message:
    return Message(role=Role.USER, content=[Part(root=TextPart(text=text))])


def model_text(text: str) -> Message:
    return Message(role=Role.MODEL, content=[Part(root=TextPart(text=text))])


def tool_req(name: str, inp: dict) -> Message:
    return Message(
        role=Role.MODEL,
        content=[Part(root=ToolRequestPart(tool_request=ToolRequest(name=name, input=inp)))],
    )


def tool_res(name: str, output: str) -> Message:
    return Message(
        role=Role.TOOL,
        content=[Part(root=ToolResponsePart(tool_response=ToolResponse(name=name, output=output)))],
    )


def read_file_delivery(path: str, content: str) -> Message:
    total = content.count('\n') + (0 if content.endswith('\n') else 1)
    wrapped = f'<read_file path="{path}" totalLines="{total}">\n{content}\n</read_file>'
    return user(wrapped)


# Genkit Filesystem middleware: stub tool ack + full file as a user message.
TRANSCRIPT: list[tuple[Message, str, str]] = [
    (user('pytest is failing in plugins/middleware after the offset change. find and fix it.'),
     'user · task', 'prose'),
    (tool_req('read_file', {'file_path': 'src/.../middleware/_filesystem.py'}),
     'model · read_file request', 'req'),
    (tool_res('read_file', 'File src/.../_filesystem.py read successfully. Content queued as user message.'),
     'tool · read_file ack', 'prose'),
    (read_file_delivery('src/.../middleware/_filesystem.py', FILE_A),
     'user · read_file delivery (_filesystem.py)', 'payload'),
    (tool_req('read_file', {'file_path': 'tests/artifacts_test.py'}),
     'model · read_file request', 'req'),
    (tool_res('read_file', 'File tests/artifacts_test.py read successfully. Content queued as user message.'),
     'tool · read_file ack', 'prose'),
    (read_file_delivery('tests/artifacts_test.py', FILE_B),
     'user · read_file delivery (artifacts_test.py)', 'payload'),
    (tool_req('execute', {'command': 'uv run pytest plugins/middleware/tests -q'}),
     'model · execute request (pytest)', 'req'),
    (tool_res('execute', PYTEST_FAIL),
     'tool · execute result (traceback)', 'payload'),
    (tool_req('edit_file', {'file_path': 'src/.../_filesystem.py', 'old_string': EDIT_OLD, 'new_string': EDIT_NEW}),
     'model · edit_file request (patch in args)', 'dup'),
    (tool_res('edit_file', 'File edited successfully.'),
     'tool · edit_file result', 'prose'),
    (tool_req('write_file', {'file_path': 'src/.../_filesystem.py', 'content': FILE_A.replace('offset', 'int(offset)', 1)}),
     'model · write_file request (whole file in args)', 'dup'),
    (tool_res('write_file', 'File src/.../_filesystem.py written successfully.'),
     'tool · write_file result', 'prose'),
    (tool_req('execute', {'command': 'uv run pytest plugins/middleware/tests -q'}),
     'model · execute request (pytest)', 'req'),
    (tool_res('execute', PYTEST_PASS),
     'tool · execute result (pass)', 'payload'),
    (model_text('Fixed. read_file was multiplying a string offset; I coerced it to int and all 11 tests pass.'),
     'model · summary', 'prose'),
    (user('great. now add a regression test for the string-offset case.'),
     'user · follow-up task', 'prose'),
    (tool_req('read_file', {'file_path': 'tests/artifacts_test.py'}),
     'model · read_file request', 'req'),
    (tool_res('read_file', 'File tests/artifacts_test.py read successfully. Content queued as user message.'),
     'tool · read_file ack', 'prose'),
    (read_file_delivery('tests/artifacts_test.py', FILE_B),
     'user · read_file delivery (same file again)', 'payload'),
    (model_text('Adding a test that passes offset as a string and asserts the coercion.'),
     'model · working', 'prose'),
]

CFG = CompactionConfig(max_context_tokens=None, keep_recent_messages=6)


def _esc(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _short_path(path: str) -> str:
    return path.replace('src/.../middleware/', '').replace('src/.../', '')


def wire_lines(msg: Message, *, chars: int) -> list[str]:
    """Human-readable wire-format lines for the transcript figure."""
    for part in msg.content:
        root = part.root
        if isinstance(root, TextPart) and root.text:
            text = root.text.strip()
            if text.startswith('<read_file'):
                m = re.search(r'path="([^"]+)" totalLines="(\d+)"', text)
                path = _short_path(m.group(1)) if m else 'file'
                lines = int(m.group(2)) if m else 0
                body_lines = text.splitlines()
                preview = body_lines[1] if len(body_lines) > 1 else ''
                if len(preview) > 58:
                    preview = preview[:55] + '...'
                return [
                    f'<read_file path="{path}" totalLines="{lines}">',
                    preview,
                    f'... {max(0, lines - 1)} lines, {chars:,} chars total ...',
                    '</read_file>',
                ]
            if len(text) > 72:
                return [text[:69] + '...']
            return [text]

        if isinstance(root, ToolRequestPart):
            tr = root.tool_request
            name = tr.name or 'tool'
            inp = tr.input or {}
            if name == 'read_file':
                fp = _short_path(str(inp.get('file_path', '')))
                return [f'tool_call: read_file(file_path="{fp}")']
            if name == 'execute':
                return [f'tool_call: execute(command="{inp.get("command", "")}")']
            if name == 'edit_file':
                fp = _short_path(str(inp.get('file_path', '')))
                old = str(inp.get('old_string', ''))
                new = str(inp.get('new_string', ''))
                return [
                    f'tool_call: edit_file(file_path="{fp}")',
                    f'  old_string: "{old[:40]}..." ({len(old):,} chars)',
                    f'  new_string: "{new[:40]}..." ({len(new):,} chars)',
                ]
            if name == 'write_file':
                content = str(inp.get('content', ''))
                fp = _short_path(str(inp.get('file_path', '')))
                return [
                    f'tool_call: write_file(file_path="{fp}")',
                    f'  content: "{content[:36]}..." ({len(content):,} chars)',
                ]
            return [f'tool_call: {name}({json.dumps(inp, ensure_ascii=False)[:60]}...)']

        if isinstance(root, ToolResponsePart):
            out = root.tool_response.output
            text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
            if len(text) > 72:
                lines = text.splitlines()
                return lines[:2] + [f'... {len(lines)} lines, {chars:,} chars total ...']
            return [text]
    return ['(empty)']


def compact_action(before: int, after: int, kind: str, in_keep: bool) -> str:
    if in_keep:
        return 'keep window: verbatim'
    if after >= before:
        return 'unchanged'
    if kind == 'dup':
        return 'clip tool_call args'
    if kind == 'payload':
        return 'truncate body'
    return 'truncate'


def msg_chars(m: Message) -> int:
    total = 0
    for part in m.content:
        root = part.root
        if isinstance(root, TextPart) and root.text:
            total += len(root.text)
        elif isinstance(root, ToolRequestPart):
            total += len(json.dumps(root.tool_request.input or {}, ensure_ascii=False)) + len(root.tool_request.name)
        elif isinstance(root, ToolResponsePart):
            out = root.tool_response.output
            total += len(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))
    return total


messages = [t[0] for t in TRANSCRIPT]
stage0 = [msg_chars(m) for m in messages]

cutoff = _determine_cutoff_index(messages, CFG)
clipped = _compact_prefix(messages, cutoff, CFG)
stage1 = [msg_chars(m) for m in clipped]

rows = []
for i, (msg, label, kind) in enumerate(TRANSCRIPT):
    in_keep = i >= cutoff
    b, a = stage0[i], stage1[i]
    rows.append({
        'idx': i + 1,
        'label': label,
        'kind': kind,
        'role': msg.role.value if hasattr(msg.role, 'value') else str(msg.role),
        'before': b,
        'clip': a,
        'in_keep_window': in_keep,
        'wire_before': wire_lines(msg, chars=b),
        'wire_after': wire_lines(clipped[i], chars=a),
        'compact_action': compact_action(b, a, kind, in_keep),
    })

result = {
    'rows': rows,
    'totals': {'before': sum(stage0), 'clip': sum(stage1)},
    'config': CFG.model_dump(),
    'cutoff_index': cutoff,
    'cutoff_message': cutoff + 1,
}
Path('/tmp/compaction_demo.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result['totals'], indent=2))
for r in rows:
    print(f"{r['idx']:>2} {r['kind']:<8} {r['before']:>7} {r['clip']:>7}  {r['label']}")
