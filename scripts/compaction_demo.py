"""Build a realistic coding-agent transcript, run it through the real
Compaction middleware functions, and emit per-message sizes per stage.

Output: /tmp/compaction_demo.json
"""

from __future__ import annotations

import json
from pathlib import Path

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
from genkit.plugins.middleware._compaction import CompactionConfig, _compact_messages, _truncate_text

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


# kind: prose | req | payload (bulk bytes) | dup (bulk bytes repeating earlier bulk)
TRANSCRIPT: list[tuple[Message, str, str]] = [
    (user('pytest is failing in plugins/middleware after the offset change. find and fix it.'),
     'user · task', 'prose'),
    (tool_req('read_file', {'file_path': 'src/.../middleware/_filesystem.py'}),
     'model · read_file request', 'req'),
    (tool_res('read_file', FILE_A),
     'tool · read_file result (_filesystem.py)', 'payload'),
    (tool_req('read_file', {'file_path': 'tests/artifacts_test.py'}),
     'model · read_file request', 'req'),
    (tool_res('read_file', FILE_B),
     'tool · read_file result (artifacts_test.py)', 'payload'),
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
    (tool_res('read_file', FILE_B),
     'tool · read_file result (artifacts_test.py again)', 'payload'),
    (model_text('Adding a test that passes offset as a string and asserts the coercion.'),
     'model · working', 'prose'),
]

CFG = CompactionConfig()  # library defaults: 1500 out / 400 in / keep 6 / preview 120


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


def offload_pointer(text: str, tool: str) -> str:
    # mirrors Compaction.wrap_tool output format exactly
    preview = text[: CFG.preview_chars].rstrip()
    name = f'tool-output/{tool}/ref.txt'
    return (
        f'{preview}{CFG.truncation_suffix} '
        f'({len(text):,} chars saved to artifact "{name}". Use read_artifact to retrieve.)'
    )


def apply_offload(messages: list[Message]) -> list[Message]:
    out = []
    for m in messages:
        new_parts = []
        changed = False
        for part in m.content:
            root = part.root
            if isinstance(root, ToolResponsePart):
                o = root.tool_response.output
                if isinstance(o, str) and len(o) > CFG.max_tool_output_chars:
                    new_parts.append(Part(root=ToolResponsePart(
                        tool_response=root.tool_response.model_copy(
                            update={'output': offload_pointer(o, root.tool_response.name or 'tool')}
                        ))))
                    changed = True
                    continue
            new_parts.append(part)
        out.append(Message(role=m.role, content=new_parts) if changed else m)
    return out


messages = [t[0] for t in TRANSCRIPT]
stage0 = [msg_chars(m) for m in messages]

offloaded = apply_offload(messages)
stage1 = [msg_chars(m) for m in offloaded]

clipped = _compact_messages(
    offloaded,
    keep_recent=CFG.keep_recent_messages,
    max_output_chars=CFG.max_tool_output_chars,
    max_input_chars=CFG.max_tool_input_chars,
    preview_chars=CFG.preview_chars,
    suffix=CFG.truncation_suffix,
    bulk_keys=frozenset(CFG.bulk_input_keys),
)
stage2 = [msg_chars(m) for m in clipped]

rows = []
for i, (msg, label, kind) in enumerate(TRANSCRIPT):
    rows.append({
        'idx': i + 1,
        'label': label,
        'kind': kind,
        'before': stage0[i],
        'offload': stage1[i],
        'clip': stage2[i],
        'in_keep_window': i >= len(TRANSCRIPT) - CFG.keep_recent_messages,
    })

result = {
    'rows': rows,
    'totals': {'before': sum(stage0), 'offload': sum(stage1), 'clip': sum(stage2)},
    'config': CFG.model_dump(),
}
Path('/tmp/compaction_demo.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result['totals'], indent=2))
for r in rows:
    print(f"{r['idx']:>2} {r['kind']:<8} {r['before']:>7} {r['offload']:>7} {r['clip']:>7}  {r['label']}")
