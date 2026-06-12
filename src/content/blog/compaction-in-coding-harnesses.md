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

## Why `read_file` shows up as a user message

Genkit's `Filesystem` middleware does something that looks wrong until you see the full stack. When the model calls `read_file`, the tool returns a short string ("read successfully, see below"). The actual file body is queued and injected before the next model turn as a **user** message:

```text
tool:  File auth.py read successfully. Content queued as user message.
user:  <read_file path="auth.py" totalLines="412">
       ...entire file...
       </read_file>
model: I see the bug on line 42...
```

Moving the payload from tool to user does **not** shrink the context window by itself. If both messages stay in history forever, you pay for the bytes either way. The trick is what it unlocks elsewhere:

**Tool results stay small and string-shaped.** Provider APIs expect tool output to be a compact acknowledgment. Images need `media` parts, which fit the user channel more cleanly than a tool result blob.

**Parallel reads batch into one delivery.** Multiple `read_file` calls in the same tool round append to the same queued user message instead of producing three separate fat tool responses.

**Re-reads can dedup.** When the file has not changed, the tool can return a stub ("unchanged since last read, refer to the earlier delivery") without queueing another copy. The canonical body lives in the earlier `<read_file>` block.

**Compaction can strip old deliveries.** That last point is the pairing that makes the pattern worth it. `Compaction` recognizes `<read_file>` user messages outside the keep window and replaces them with path stubs:

```text
<read_file path="auth.py">…[stripped] (14,521 chars; file is on disk, call read_file to retrieve)</read_file>
```

The file is still on disk. The model can call `read_file` again if it genuinely needs the body back. You are not deleting information, you are refusing to re-send it on every subsequent turn.

Without that strip pass, queuing file bodies as user messages would just move the bloat to a different role. With it, the delivery channel and the compaction layer are designed together.

## A Genkit implementation

Genkit's official middleware plugin already has two pieces of this stack: `Filesystem` (the read delivery trick above) and `Artifacts` (`read_artifact` / `write_artifact` for side-channel storage). History compaction across both tool messages and those synthetic user deliveries is a **recipe**, not part of the official plugin: copy [`compaction.py`](https://github.com/jeffdh5/genkit-compaction-recipe/blob/main/compaction.py) from [jeffdh5/genkit-compaction-recipe](https://github.com/jeffdh5/genkit-compaction-recipe) into your project.

```python
from compaction import Compaction
from genkit.plugins.middleware import Artifacts, Filesystem

await ai.generate(
    prompt='Fix the failing test in auth.py',
    use=[
        Filesystem(root_dir='./workspace'),
        Artifacts(),
        Compaction(
            max_tool_output_chars=1500,
            max_tool_input_chars=400,
            keep_recent_messages=6,
        ),
    ],
)
```

It hooks the agent loop in two places. `wrap_tool` runs on every tool call: when a result exceeds `max_tool_output_chars`, the full text is written to a session artifact and the model gets a short pointer instead.

```python
artifact_name = f'tool-output/{tool_name}/{ref}.txt'
await ctx.session.add_artifacts(Artifact(name=artifact_name, parts=[Part(text=text)]))
return MultipartToolResponse(
    output=f'{preview}... ({len(text):,} chars saved to artifact '
           f'"{artifact_name}". Use read_artifact to retrieve.)'
)
```

Pairing this with `Artifacts()` matters: the model can call `read_artifact` if it genuinely needs the full output again, which is the same recoverability property Deep Agents gets from its backend files.

`wrap_generate` runs before each model call and compacts messages outside the `keep_recent_messages` window. That pass does three things: strip old `<read_file>` user deliveries to path stubs, clip bulky tool request inputs (`content`, `old_string`, `patch`, and so on), and truncate oversized tool response outputs to a preview plus a character count.

Here is the transcript from the first figure again, run through this implementation at its default settings, drawn at the same scale:

![The same transcript at three stages: 50.5k characters raw, 47.3k after offloading one large tool output, 9.2k after stripping read deliveries and clipping old tool arguments.](/figures/compaction-stages.svg)

The middle stage is a useful sanity check. With `Filesystem` in the stack, offloading tool outputs barely moves the total (50.5k down to 47.3k) because the file bodies were never in tool results to begin with. Only the pytest traceback gets offloaded. The real drop happens in the third stage: old `<read_file>` deliveries shrink to about 130 characters each, and the `write_file` argument clip removes the 15k rust bar. What remains in the keep window is prose, recent reads, and small acks.

A note on what stripping costs: nothing visible in my testing so far, though that is unit tests plus this measurement, not long production runs. The stripped deliveries belong to files already on disk. If the model wants the current state, `read_file` is both cheaper and more correct than trusting a stale copy in history.

Tests live in the recipe repo. They run with:

```bash
git clone https://github.com/jeffdh5/genkit-compaction-recipe
cd genkit-compaction-recipe && uv sync && uv run pytest -q
```

## What's missing

`Compaction` implements the structural layers and stops there. Deep Agents also has token-fraction triggers (clip at 85% of the window rather than a fixed message count), LLM-written summaries of evicted history, and the conversation log file that makes summarization reversible. Those belong in a separate summarization middleware, and the structural layers are the right place to start anyway: they're cheap, they're lossless in practice, and they delay the day you need the expensive layer at all.

Sources: Open SWE's [`agent/middleware/tool_artifact.py`](https://github.com/langchain-ai/open-swe) and `agent/server.py`; Deep Agents' [`middleware/summarization.py`](https://github.com/langchain-ai/deepagents) and `graph.py`. The compaction recipe is [jeffdh5/genkit-compaction-recipe](https://github.com/jeffdh5/genkit-compaction-recipe) (not part of official [genkit](https://github.com/firebase/genkit)). The figures were generated by [a script](https://github.com/jeffdh5/agent-eng-blog/tree/main/scripts) that builds the transcript above and runs it through the recipe's compaction functions.
