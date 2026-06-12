---
title: "Compaction in coding harnesses"
description: "Summarizing chat history doesn't help when the context is full of file bodies. Where the tokens actually are, drawn to scale, plus what Open SWE and Deep Agents do about it and a Genkit middleware that does the same."
pubDate: "Jun 12 2026"
---

Summarizing old conversation history works fine for chat agents and badly for coding agents. The difference is where the tokens are. In a coding loop most of the context is file contents, and they show up twice: once in tool results, once in the tool-call arguments that stay in history after the edit lands. A `write_file` from turn 3 is still carrying its full payload at turn 40, and no summary of the conversation touches it.

I read through [Open SWE](https://github.com/langchain-ai/open-swe) and the [Deep Agents](https://github.com/langchain-ai/deepagents) library it sits on to see what they actually do about this. The short version: summarization exists, but it's the last resort. Most of the savings come from never letting file bodies into the message history at all, and from clipping the ones that got in before each model call. I then implemented the same layers as middleware for Genkit's Python SDK, and used that implementation to measure exactly where the bloat sits in a typical transcript. The code and the measurement script are linked at the end.

## What the message history actually looks like

The mechanics matter here, so it's worth being concrete. An agent loop keeps one growing message list. Every iteration, the entire list goes back to the model: the user's task, every tool call the model made, every tool result that came back. Nothing falls out on its own.

Here is that list for a small, completely ordinary task: one failing test, two file reads, a pytest run, a patch, a rewrite, a green pytest run, then a follow-up request from the user. Eighteen messages. Each bar is one message, drawn to scale by character count. The file bodies are real files from the Genkit repo; the transcript shape is constructed, and every number comes from running it through the actual middleware code.

![Per-message sizes of an 18-message coding agent transcript. A handful of tool results and tool arguments dwarf all the prose.](/figures/compaction-anatomy.svg)

A few things jump out. The prose is invisible: the task, the model's commentary, the small tool acks are all under 100 characters each, slivers at this scale. Three tool results (two `read_file` calls and a traceback) account for 24k characters. And the two rust-colored bars are the part summarization can never reach: message 8 carries a 2.6k patch in `edit_file` arguments, and message 10 carries the *entire updated file*, 14.3k characters, as the `content` argument of a `write_file` call. The file is already on disk at that point. Those bytes ride along on every subsequent model call anyway.

Total: 48.7k characters, roughly 12k tokens, for a task a human would describe in one sentence. By message 18 the model needs almost none of it. That ratio is the whole problem, and it gets worse linearly with every tool call.

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

## A Genkit implementation

Genkit's middleware plugin already had one of these layers: the `Filesystem` middleware queues `read_file` content as user messages so the tool response itself stays small, and the `Artifacts` middleware gives sessions a `read_artifact` / `write_artifact` store. What was missing was the middle of the stack, so I added a `Compaction` middleware that implements layers 2 and 3.

```python
from genkit.plugins.middleware import Artifacts, Compaction, Filesystem

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

`wrap_generate` runs before each model call and clips messages outside the `keep_recent_messages` window. Tool request inputs lose their bulky fields (`content`, `old_string`, `patch`, and so on, the same set Deep Agents targets), and oversized tool response outputs get cut to a preview plus a character count.

Here is the transcript from the first figure again, run through this implementation at its default settings, drawn at the same scale:

![The same transcript at three stages: 48.7k characters raw, 18.8k after offloading tool outputs to artifacts, 2.5k after also clipping old tool arguments.](/figures/compaction-stages.svg)

The middle bar is the interesting one. Offloading tool outputs alone gets you from 48.7k to 18.8k, and it's tempting to stop there. But look at what survives: almost all of the remaining bulk is that one `write_file` argument, the rust segment, which no amount of output-side work will ever touch. Only the history-clipping pass removes it, taking the total to 2.5k. If you implement just one of these layers, the output side feels like the obvious choice; the figure is the argument for why you need both.

A note on what clipping costs: nothing visible, in my testing so far, though my testing is unit tests plus this measurement, not long production runs. The clipped arguments belong to edits that already landed. If the model wants the current state of a file, re-reading it is both cheaper and more correct than trusting a stale argument in history.

Four unit tests cover the truncation logic, the keep-window behavior, and the artifact offload. They run with:

```bash
cd py && uv run pytest plugins/middleware/tests/compaction_test.py -q --no-cov
```

## What's missing

`Compaction` implements the structural layers and stops there. Deep Agents also has token-fraction triggers (clip at 85% of the window rather than a fixed message count), LLM-written summaries of evicted history, and the conversation log file that makes summarization reversible. Those belong in a separate summarization middleware, and the structural layers are the right place to start anyway: they're cheap, they're lossless in practice, and they delay the day you need the expensive layer at all.

Sources: Open SWE's [`agent/middleware/tool_artifact.py`](https://github.com/langchain-ai/open-swe) and `agent/server.py`; Deep Agents' [`middleware/summarization.py`](https://github.com/langchain-ai/deepagents) and `graph.py`. The Genkit middleware is `py/plugins/middleware/src/genkit/plugins/middleware/_compaction.py` in [genkit](https://github.com/firebase/genkit). The figures were generated by [a script](https://github.com/jeffdh5/agent-eng-blog/tree/main/scripts) that builds the transcript above and runs it through the real middleware functions.
