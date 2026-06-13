---
title: "Compaction in coding harnesses"
description: "Summarizing chat history doesn't help when the context is full of file bodies. Where the tokens actually are, drawn to scale, plus what Open SWE and Deep Agents do about it and a Genkit middleware that does the same."
pubDate: "Jun 12 2026"
---

Summarizing old conversation history works fine for chat agents and badly for coding agents. The difference is where the tokens are. In a coding loop most of the context is file contents, and they show up in more than one place: tool results (tracebacks, command output), synthetic user messages (when a harness delivers `read_file` bodies that way), and tool-call arguments that stay in history after the edit lands. A `write_file` from turn 3 is still carrying its full payload at turn 40, and no summary of the conversation touches it.

I read through [Open SWE](https://github.com/langchain-ai/open-swe) and the [Deep Agents](https://github.com/langchain-ai/deepagents) library it sits on to see what they actually do about this. The short version: summarization exists, but it's the last resort. Most of the savings come from never letting file bodies into the message history at all, and from clipping the ones that got in before each model call. I then implemented the same layers as middleware for Genkit's Python SDK, and used that implementation to measure exactly where the bloat sits in a typical transcript. The code and the measurement script are linked at the end.

## What the message history actually looks like

The mechanics matter here, so it's worth being concrete. An agent loop keeps one growing message list. Every iteration, the entire list goes back to the model: the user's task, every tool call the model made, every tool result that came back. Nothing falls out on its own.

Here is that list for a small, completely ordinary task: one failing test, two file reads, a pytest run, a patch, a rewrite, a green pytest run, then a follow-up request from the user. Twenty-one messages. The transcript uses Genkit's `Filesystem` middleware shape: `read_file` returns a one-line tool ack, and the file body arrives on the next turn as a user message wrapped in `<read_file>…</read_file>`. Each bar is one message, drawn to scale by character count. The file bodies are real files from the Genkit repo; every number comes from running the transcript through the actual middleware code.

![Per-message sizes of a 21-message coding agent transcript. File deliveries and tool arguments dwarf all the prose.](/figures/compaction-anatomy.svg)

A few things jump out. The prose is invisible: the task, the model's commentary, the `read_file` acks are all under 100 characters each, slivers at this scale. The green bars are bulk entering the history: two `<read_file>` user deliveries (about 21k characters between them) plus a pytest traceback in a tool result. The rust-colored bars are the part summarization can never reach on its own: an `edit_file` request carrying a 2.6k patch in its arguments, and a `write_file` request carrying the entire updated file (about 15k characters) as `content`. The file is already on disk at that point. Those bytes ride along on every subsequent model call anyway.

Total: about 50.5k characters, roughly 12k tokens, for a task a human would describe in one sentence. By message 21 the model needs almost none of it. That ratio is the whole problem, and it gets worse linearly with every tool call.

## What Open SWE does at the tool boundary

Open SWE's main agent is a `create_deep_agent()` call in `agent/server.py` with a custom middleware stack on top. The piece relevant here is `ToolArtifactMiddleware`, in `agent/middleware/tool_artifact.py`. Its module docstring explains the whole design:

```text
edit_file / write_file return only a one-line summary, but the dashboard
renders a full-file diff per edit. This middleware reads the file's *before*
content from the sandbox once, computes the *after* content locally, and
stamps the result's artifact with a {"diff": {...}} payload.
```

So when the agent edits a file, the model sees a one-line acknowledgment. The full diff goes into `ToolMessage.artifact`, a serialized field that the dashboard reads directly, both live and on reload. The diff exists for the human reviewing the work, not for the model, so it never enters the context window. If you're deciding what your own file-edit tools should return, this separation between what the model needs and what the UI needs is the decision that matters most.

There's a related detail I appreciated: the middleware caps its own before-read at 20,000 lines (`_MAX_DIFF_LINES`), because rendering a diff isn't worth pulling a huge file into memory. Even the bookkeeping around compaction is budgeted.

## What Deep Agents does underneath it

Deep Agents wires summarization into every agent by default. `create_deep_agent` adds `create_summarization_middleware(model, backend)` to the stack, and Open SWE rides on it without modification. The middleware does three things, in increasing order of severity.

First, it clips large tool-call arguments in older messages. This is configured by `TruncateArgsSettings`, and the docstring is worth quoting because it names the exact failure mode:

```text
This is a lightweight, pre-summarization optimization that fires at a lower
token threshold than full conversation compaction. When triggered, only the
args values on AIMessage.tool_calls in messages *before* the keep window
are shortened. Recent messages are left intact. Typical large arguments
include write_file content, edit_file patches, and verbose execute outputs.
```

This is the part chat-style summarization misses, and it's aimed squarely at bars 8 and 10 in the figure above. The arguments to a `write_file` call from twenty turns ago are dead weight: the edit already landed, the file is on disk, and the model can re-read it if it ever needs it again. Clipping them is nearly free and loses nothing.

Second, large tool results get offloaded to the backend under `/large_tool_results/`, with a pointer left in the message.

Third, when token usage crosses a threshold, the middleware summarizes the evicted span with an LLM call and appends the full original messages to `/conversation_history/{thread_id}.md` on the backend, so nothing is unrecoverable. With a known model profile, the defaults trigger at 85% of the context window and keep the most recent 10%.

The ordering tells you the philosophy. The layers fire cheapest first:

1. Keep bulk out of the message history at the tool boundary (artifacts, side channels)
2. Clip bulky arguments in old tool calls before each model call
3. Offload large tool results, leaving a pointer
4. Summarize with an LLM only near the context ceiling

Summarization is the only layer that costs a model call and the only one that loses information you can't point back to. Both libraries treat it accordingly.

## What Genkit's Filesystem middleware does today

Genkit's official `Filesystem` middleware queues file bodies as user messages. When the model calls `read_file`, the tool returns a short ack and the full file lands on the next turn as a **user** message wrapped in `<read_file>…</read_file>`. That keeps tool responses small and string-shaped, but it does **not** shrink the context window by itself — you still pay for those bytes on every subsequent call unless something compacts them away.

Open SWE and Deep Agents take a different shape at the tool boundary: paginated `read_file` with `offset` / `limit`, file body in the tool result, re-read dedup when the file is unchanged. That's the direction I'd rather see in harnesses. The compaction recipe below works with either delivery style; it clips bulky tool arguments, truncates oversized inline text (including stale read deliveries), offloads huge tool outputs to artifacts, and only then runs LLM summarization.

## A Genkit implementation

Genkit's official middleware plugin already has `Filesystem` and `Artifacts`. History compaction is a **recipe** — copy [`compaction.py`](https://github.com/jeffdh5/python-middleware-recipes/blob/main/recipes/compaction/compaction.py) from [jeffdh5/python-middleware-recipes](https://github.com/jeffdh5/python-middleware-recipes) into your project.

```python
from compaction import Compaction
from genkit.plugins.middleware import Artifacts, Filesystem

await ai.generate(
    prompt='Fix the failing test in auth.py',
    use=[
        Filesystem(root_dir='./workspace'),
        Artifacts(),
        Compaction(
            max_context_tokens=200_000,
            trigger_fraction=0.85,
            keep_fraction=0.10,
            summary_model='googleai/gemini-flash-latest',
        ),
    ],
)
```

It hooks the agent loop in two places.

**`wrap_tool`** — tool results above `offload_tool_threshold_chars` (~80k characters) go to a session artifact with a head/tail sample inline. `read_file` / `write_file` / `edit_file` / `list_files` are excluded.

```python
artifact_name = f'tool-output/{tool_name}/{ref}.txt'
await ctx.session.add_artifacts(Artifact(name=artifact_name, parts=[Part(text=text)]))
return MultipartToolResponse(
    output=(
        f'[output offloaded] full text in artifact `{artifact_name}`.\n'
        f'--- sample ---\n{preview}\n--- end sample ---'
    )
)
```

**`wrap_generate`** — before each model call, messages outside the keep window get structurally compacted: bulky tool-call arguments clipped, oversized tool responses and user-message text truncated to a preview. When estimated usage crosses `trigger_fraction` of `max_context_tokens`, the evicted prefix is appended to a conversation-log artifact and replaced with an LLM-written handoff note the model can recover from via `read_artifact`.

Here is the transcript from the first figure again, run through the structural pass at its default settings (`keep_recent_messages=6`), drawn at the same scale:

![The same transcript at two stages: 50.5k characters raw, then structurally compacted with old read deliveries and tool arguments clipped.](/figures/compaction-stages.svg)

The drop is almost entirely structural. Old `<read_file>` user deliveries shrink to short previews, the `write_file` argument clip removes the 15k rust bar, and the pytest traceback outside the keep window gets truncated. What remains in the tail is prose, recent reads, and small acks. On a transcript this size summarization does not fire yet; that layer kicks in near the context ceiling.

Tests live in the recipe repo:

```bash
git clone https://github.com/jeffdh5/python-middleware-recipes
cd python-middleware-recipes && uv sync && uv run pytest -q
```

## What's still open

The official Genkit `Filesystem` middleware still queues reads instead of paginating inline. Re-read dedup exists in the Go plugin but not Python yet. Those are harness fixes, not compaction fixes — but they change where the bytes land before compaction ever runs.

Sources: Open SWE's [`agent/middleware/tool_artifact.py`](https://github.com/langchain-ai/open-swe) and `agent/server.py`; Deep Agents' [`middleware/summarization.py`](https://github.com/langchain-ai/deepagents) and `graph.py`. The compaction recipe is [jeffdh5/python-middleware-recipes](https://github.com/jeffdh5/python-middleware-recipes) (not part of official [genkit](https://github.com/firebase/genkit)). The figures were generated by [a script](https://github.com/jeffdh5/agent-eng-blog/tree/main/scripts) that builds the transcript above and runs it through the recipe's compaction functions.
