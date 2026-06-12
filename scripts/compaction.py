# Copyright 2026 Jeff Huang
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

"""Compaction middleware recipe for Genkit coding-agent harnesses.

Not part of the official Genkit middleware plugin. Copy this file into your
project and pass ``Compaction()`` in ``use=[...]`` alongside ``Filesystem``
and ``Artifacts`` from ``genkit.plugins.middleware``.

Blog writeup: https://agentinternals.dev/blog/compaction-in-coding-harnesses/
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field

from genkit._core._model import Message
from genkit._core._typing import (
    Artifact,
    MediaPart,
    Part,
    Role,
    TextPart,
    ToolRequestPart,
    ToolResponsePart,
)
from genkit.middleware import (
    BaseMiddleware,
    GenerateHookParams,
    GenerateMiddlewareContext,
    MultipartToolResponse,
    ToolHookParams,
)

_ARTIFACT_SOURCE = 'compaction-middleware'
_DEFAULT_BULK_INPUT_KEYS = frozenset(
    {'content', 'old_string', 'new_string', 'command', 'code', 'patch', 'diff'}
)
_READ_FILE_PATH_RE = re.compile(r'<read_file[^>]*\bpath=["\']([^"\']+)["\']')
_FILESYSTEM_TOOL_META = 'filesystemMiddlewareTool'


class CompactionConfig(BaseModel):
    """Knobs for coding-harness context compaction."""

    max_tool_output_chars: int = Field(
        default=1500,
        description='Inline cap for a single tool result. Larger outputs are offloaded or clipped.',
    )
    max_tool_input_chars: int = Field(
        default=400,
        description='Per-field cap when trimming tool arguments in older messages.',
    )
    keep_recent_messages: int = Field(
        default=6,
        description='Trailing messages left untouched by history compaction.',
    )
    preview_chars: int = Field(
        default=120,
        description='How many characters to keep at the start of a truncated field.',
    )
    offload_large_outputs: bool = Field(
        default=True,
        description='When a session is available, store full tool output in an artifact.',
    )
    strip_filesystem_reads: bool = Field(
        default=True,
        description='Replace old <read_file> user deliveries with path stubs.',
    )
    bulk_input_keys: list[str] = Field(
        default_factory=lambda: sorted(_DEFAULT_BULK_INPUT_KEYS),
        description='Tool input dict keys likely to carry file bodies or patches.',
    )
    truncation_suffix: str = Field(
        default='…[truncated]',
        description='Appended after clipped tool arguments and outputs.',
    )
    filesystem_strip_suffix: str = Field(
        default='…[stripped]',
        description='Marker left when an old read_file delivery is removed.',
    )


def _as_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _truncate_text(text: str, max_chars: int, preview_chars: int, suffix: str) -> str:
    if len(text) <= max_chars:
        return text
    head = text[:preview_chars].rstrip()
    return f'{head}{suffix} ({len(text):,} chars total)'


def _extract_read_file_path(text: str) -> str | None:
    match = _READ_FILE_PATH_RE.search(text)
    return match.group(1) if match else None


def _is_filesystem_read_part(part: Part) -> bool:
    root = part.root
    if isinstance(root, TextPart) and root.text:
        meta = root.metadata if isinstance(root.metadata, dict) else {}
        if meta.get(_FILESYSTEM_TOOL_META):
            return True
        return root.text.lstrip().startswith('<read_file')
    return False


def _is_filesystem_media_part(part: Part) -> bool:
    root = part.root
    if not isinstance(root, MediaPart):
        return False
    meta = root.metadata if isinstance(root.metadata, dict) else {}
    return bool(meta.get(_FILESYSTEM_TOOL_META))


def _strip_filesystem_read_text(text: str, suffix: str) -> str:
    path = _extract_read_file_path(text) or 'unknown'
    return (
        f'<read_file path="{path}">{suffix} '
        f'({len(text):,} chars; file is on disk, call read_file to retrieve)</read_file>'
    )


def _strip_filesystem_media_part(part: Part, suffix: str) -> Part:
    return Part(
        root=TextPart(
            text=f'<read_file path="unknown">{suffix} (image; call read_file to retrieve)</read_file>',
            metadata={_FILESYSTEM_TOOL_META: True},
        )
    )


def _truncate_tool_input(
    tool_input: Any,
    *,
    max_chars: int,
    preview_chars: int,
    suffix: str,
    bulk_keys: frozenset[str],
) -> Any:
    if not isinstance(tool_input, dict):
        return tool_input
    out = deepcopy(tool_input)
    for key in bulk_keys:
        if key not in out:
            continue
        raw = out[key]
        if not isinstance(raw, str):
            continue
        out[key] = _truncate_text(raw, max_chars, preview_chars, suffix)
    return out


def _compact_messages(
    messages: list[Message],
    *,
    keep_recent: int,
    max_output_chars: int,
    max_input_chars: int,
    preview_chars: int,
    suffix: str,
    bulk_keys: frozenset[str],
    strip_filesystem_reads: bool,
    filesystem_strip_suffix: str,
) -> list[Message]:
    if len(messages) <= keep_recent:
        return messages

    cutoff = len(messages) - keep_recent
    compacted: list[Message] = []
    for idx, msg in enumerate(messages):
        if idx >= cutoff:
            compacted.append(msg)
            continue

        new_parts: list[Part] = []
        for part in msg.content:
            root = part.root
            if strip_filesystem_reads and msg.role == Role.USER:
                if _is_filesystem_read_part(part):
                    text = root.text if isinstance(root, TextPart) else ''
                    new_parts.append(
                        Part(
                            root=TextPart(
                                text=_strip_filesystem_read_text(text, filesystem_strip_suffix),
                                metadata={_FILESYSTEM_TOOL_META: True},
                            )
                        )
                    )
                    continue
                if _is_filesystem_media_part(part):
                    new_parts.append(_strip_filesystem_media_part(part, filesystem_strip_suffix))
                    continue

            if isinstance(root, ToolRequestPart):
                tr = root.tool_request
                new_input = _truncate_tool_input(
                    tr.input,
                    max_chars=max_input_chars,
                    preview_chars=preview_chars,
                    suffix=suffix,
                    bulk_keys=bulk_keys,
                )
                new_parts.append(
                    Part(
                        root=ToolRequestPart(
                            tool_request=tr.model_copy(update={'input': new_input}),
                            metadata=root.metadata,
                        )
                    )
                )
                continue

            if isinstance(root, ToolResponsePart):
                tr = root.tool_response
                output_text = _as_text(tr.output)
                if len(output_text) > max_output_chars:
                    output_text = _truncate_text(output_text, max_output_chars, preview_chars, suffix)
                    new_parts.append(
                        Part(
                            root=ToolResponsePart(
                                tool_response=tr.model_copy(update={'output': output_text}),
                                metadata=root.metadata,
                            )
                        )
                    )
                    continue

            if isinstance(root, TextPart) and isinstance(root.text, str):
                if len(root.text) > max_output_chars:
                    new_parts.append(
                        Part(
                            root=TextPart(
                                text=_truncate_text(root.text, max_output_chars, preview_chars, suffix),
                                metadata=root.metadata,
                            )
                        )
                    )
                    continue

            new_parts.append(part)

        compacted.append(Message(role=msg.role, content=new_parts, metadata=msg.metadata))

    return compacted


class Compaction(BaseMiddleware[CompactionConfig]):
    """Layered compaction for agent tool loops: offload, strip reads, trim history."""

    async def wrap_generate(
        self,
        params: GenerateHookParams,
        ctx: GenerateMiddlewareContext,
        next_fn: Callable[[GenerateHookParams, GenerateMiddlewareContext], Awaitable[Any]],
    ) -> Any:
        cfg = self.config
        bulk = frozenset(cfg.bulk_input_keys)
        compact_kwargs = {
            'keep_recent': cfg.keep_recent_messages,
            'max_output_chars': cfg.max_tool_output_chars,
            'max_input_chars': cfg.max_tool_input_chars,
            'preview_chars': cfg.preview_chars,
            'suffix': cfg.truncation_suffix,
            'bulk_keys': bulk,
            'strip_filesystem_reads': cfg.strip_filesystem_reads,
            'filesystem_strip_suffix': cfg.filesystem_strip_suffix,
        }

        compacted_options = _compact_messages(list(params.options.messages), **compact_kwargs)
        compacted_request = _compact_messages(list(params.request.messages), **compact_kwargs)

        new_options = params.options.model_copy(update={'messages': compacted_options})
        new_request = params.request.model_copy(update={'messages': compacted_request})
        new_params = params.model_copy(update={'options': new_options, 'request': new_request})
        return await next_fn(new_params, ctx)

    async def wrap_tool(
        self,
        params: ToolHookParams,
        ctx: GenerateMiddlewareContext,
        next_fn: Callable[[ToolHookParams, GenerateMiddlewareContext], Awaitable[MultipartToolResponse]],
    ) -> MultipartToolResponse:
        result = await next_fn(params, ctx)
        cfg = self.config
        text = _as_text(result.output)
        if len(text) <= cfg.max_tool_output_chars:
            return result

        tool_name = params.tool.name
        ref = params.tool_request_part.tool_request.ref or uuid.uuid4().hex[:8]
        artifact_name = f'tool-output/{tool_name}/{ref}.txt'

        if cfg.offload_large_outputs and ctx.session is not None:
            await ctx.session.add_artifacts(
                Artifact(
                    name=artifact_name,
                    parts=[Part(text=text)],
                    metadata={'source': _ARTIFACT_SOURCE, 'tool': tool_name, 'ref': ref},
                )
            )
            preview = text[: cfg.preview_chars].rstrip()
            compact = (
                f'{preview}{cfg.truncation_suffix} '
                f'({len(text):,} chars saved to artifact "{artifact_name}". '
                f'Use read_artifact to retrieve.)'
            )
            return MultipartToolResponse(
                output=compact,
                content=result.content,
                metadata=result.metadata,
            )

        compact = _truncate_text(
            text,
            cfg.max_tool_output_chars,
            cfg.preview_chars,
            cfg.truncation_suffix,
        )
        return MultipartToolResponse(
            output=compact,
            content=result.content,
            metadata=result.metadata,
        )
