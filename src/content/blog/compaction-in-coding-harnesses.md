---
title: "Compaction in coding harnesses"
description: "I spent a few days studying Open SWE's compaction stack and reimplemented what mattered as Genkit middleware you can copy into your own coding-agent harness."
pubDate: "Jun 12 2026"
---

If you are building a coding agent, you will eventually hit the context ceiling. Summarizing old chat turns sounds like the fix, and for a chat agent it mostly is. For a coding agent the bloat is structural: file bodies, command output, and tool-call arguments that never leave the message list. A `write_file` from turn 3 is still carrying its full payload at turn 40, and no summary of the conversation touches it.

I spent a few days studying the [Open SWE](https://github.com/langchain-ai/open-swe) harness and the [Deep Agents](https://github.com/langchain-ai/deepagents) library it sits on, taking notes on what I thought was interesting. Compaction was the topic I kept coming back to. It matters more for coding agents than chat agents, and I wanted to see what they actually do about it. The first half of this post is that distillation. The second half is how I mapped it onto Genkit middleware, with a full example at the end.

# Part 1: What I found interesting

## What the message history actually looks like

The mechanics matter here, so it is worth being concrete. An agent loop keeps one growing `messages` array. Every iteration, the entire array goes back to the model: the user's task, every tool call the model made, every tool result that came back. Nothing falls out on its own. Compaction is surgery on that array before the next `generate()` call.

Here is a real run: one failing test, two file reads, a pytest run, a patch, a rewrite, a green pytest run, then a follow-up from the user. Twenty-one slots in `messages`. The figure below is the array itself: each index drawn to token weight (taller slot = more context burned), with hot zones highlighted.

![The messages array at turn 21: each index sized by token weight, hot zones visible, and the same array after wrap_generate compacts the prefix.](/figures/compaction-messages-array.svg)

Read it left to right as two snapshots of the same list. On the left, `messages` at turn 21: what you would pass verbatim to the model. Most slots are thin slivers (under 100 characters). A handful are towers: `messages[3]` and `messages[6]` are `<read_file>` user deliveries, `messages[8]` is a pytest traceback, `messages[9]` and `messages[11]` are tool-call arguments still carrying patches and whole files the agent already wrote to disk. Those indices are the hot zones. They dominate the ~12k-token budget even though the task is one sentence.

On the right, the same array after `wrap_generate`. The prefix slots shrink; `messages[15]` onward (the keep window) stay verbatim. The array is still 21 elements long, but the total drops from about 50.5k characters to 9.1k. That is what compaction implements: mutate the `messages` list in place before the model sees it, not a separate summarization channel off to the side.

The call site is literally:

```python
response = await ai.generate(messages=messages, ...)
```

Every tool round appends more slots. Until something compacts the prefix, they all ride along.

For the same array drawn as a flat bar chart (easier to compare slot sizes at a glance):

![Per-message sizes of a 21-message coding agent transcript. File deliveries and tool arguments dwarf all the prose.](/figures/compaction-anatomy.svg)

Total: about 50.5k characters, roughly 12k tokens, for a task a human would describe in one sentence. By `messages[20]` the model needs almost none of the bytes in `messages[0]` through `messages[14]`. That ratio is the whole problem, and it gets worse linearly with every tool call.

## What Open SWE does at the tool boundary

Open SWE's main agent is a `create_deep_agent()` call in `agent/server.py` with a custom middleware stack on top. The piece I kept coming back to is `ToolArtifactMiddleware` in `agent/middleware/tool_artifact.py`. Its module docstring states the design plainly:

```text
edit_file / write_file return only a one-line summary, but the dashboard
renders a full-file diff per edit. This middleware reads the file's *before*
content from the sandbox once, computes the *after* content locally, and
stamps the result's artifact with a {"diff": {...}} payload.
```

The model sees a one-line acknowledgment after an edit. The full diff goes into `ToolMessage.artifact`, a serialized field the dashboard reads directly, both live and on reload. The diff exists for the human reviewing the work, not for the model, so it never enters the context window. If you are deciding what your own file-edit tools should return, this separation between what the model needs and what the UI needs is the decision that matters most.

Open SWE also caps its own before-read at 20,000 lines (`_MAX_DIFF_LINES`) when computing those diffs. Even the bookkeeping around compaction is budgeted.

## What Deep Agents does underneath

Deep Agents wires summarization into every agent by default. `create_deep_agent` adds `create_summarization_middleware(model, backend)` to the stack, and Open SWE rides on it without modification. The middleware does three things, in increasing order of severity, and the ordering is the lesson.

First, it clips large tool-call arguments in older messages. `TruncateArgsSettings` names the failure mode in its docstring:

```text
This is a lightweight, pre-summarization optimization that fires at a lower
token threshold than full conversation compaction. When triggered, only the
args values on AIMessage.tool_calls in messages *before* the keep window
are shortened. Recent messages are left intact. Typical large arguments
include write_file content, edit_file patches, and verbose execute outputs.
```

This is aimed squarely at the rust bars in the figure above. The arguments to a `write_file` call from twenty turns ago are dead weight: the edit already landed, the file is on disk, and the model can re-read it if it ever needs it again. Clipping them is nearly free and loses nothing in practice.

Second, large tool results get offloaded to the backend under `/large_tool_results/`, with a pointer left in the message.

Third, when token usage crosses a threshold, the middleware summarizes the evicted span with an LLM call and appends the full original messages to `/conversation_history/{thread_id}.md` on the backend, so nothing is unrecoverable. With a known model profile, the defaults trigger at 85% of the context window and keep the most recent 10%.

## The stack, in order

Putting Open SWE and Deep Agents together, the layers fire cheapest first:

1. Keep bulk out of the message history at the tool boundary (short acks, diffs in artifacts, side channels for the UI)
2. Clip bulky arguments in old tool calls before each model call
3. Offload huge tool results, leaving a pointer the agent can follow
4. Summarize with an LLM only near the context ceiling, with the verbatim transcript archived somewhere recoverable

Summarization is the only layer that costs a model call and the only one that loses information you cannot point back to. Both projects treat it accordingly. Everything above it is structural: cheap, and in practice lossless because the files are still on disk and the archived logs still exist.

A note on where the bytes land before any of this runs: harness design matters. Open SWE and Deep Agents paginate `read_file` and keep the body in the tool result. Genkit's official `Filesystem` middleware still queues full file bodies as user messages. Compaction can shrink stale deliveries either way, but paginated reads are the better starting point.

# Part 2: Implementing this in Genkit

Genkit's middleware plugin gives you `BaseMiddleware` with two hooks that map cleanly onto the stack above. `wrap_tool` runs once after each tool call. `wrap_generate` runs on the full message list right before each model call. You pass middleware instances in `use=[...]` alongside your tools, same as the official `Filesystem` and `Artifacts` helpers.

Open SWE's short acks and artifact-side diffs land in the tool implementation plus `Artifacts`, with optional logic in `wrap_tool`. Deep Agents' argument clipping and summarization live in `wrap_generate`. Immediate offload of a fresh enormous tool result lives in `wrap_tool`. The recoverable conversation log uses session artifacts the same way Deep Agents uses backend files.

Compaction is a recipe, not part of the official plugin. Copy [`compaction.py`](https://github.com/jeffdh5/python-middleware-recipes/blob/main/recipes/compaction/compaction.py) from [jeffdh5/python-middleware-recipes](https://github.com/jeffdh5/python-middleware-recipes) into your project, then import it as `from compaction import Compaction`.

## What the recipe does in each hook

In `wrap_tool`, tool results above `offload_tool_threshold_chars` (about 80k characters) go to a session artifact with a head/tail sample left inline. `read_file`, `write_file`, `edit_file`, and `list_files` are excluded because those tools should already be bounded at the harness level:

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

In `wrap_generate`, messages outside the keep window get structurally compacted before the model sees them: bulky tool-call arguments clipped, oversized tool responses and user-message text truncated to a preview. When estimated usage crosses `trigger_fraction` of `max_context_tokens`, the evicted prefix is appended to a conversation-log artifact and replaced with an LLM-written handoff note. The handoff embeds the log path so the agent can `read_artifact` the verbatim transcript later.

If you are assembling your own middleware instead of copying the recipe, the mapping is direct. Open SWE's artifact-side diffs are a `wrap_tool` concern plus whatever your UI reads from `ctx.session` artifacts. Deep Agents' argument clipping and summarization are a `wrap_generate` concern over `params.options.messages`. You do not need to fork Genkit's agent loop; you add middleware classes and list them in `use`.

## Full example

A minimal coding-agent session with the full stack wired in. `Filesystem` supplies the tools, `Artifacts` gives the model `read_artifact` for recovery, and `Compaction` runs the structural layers plus optional summarization:

```python
from pathlib import Path

from genkit import Genkit, Message, Part, Role, TextPart
from genkit.plugins.google_genai import GoogleAI
from genkit.plugins.middleware import Artifacts, Filesystem, Middleware
from compaction import Compaction

workspace = Path('./workspace')
workspace.mkdir(exist_ok=True)

ai = Genkit(
    plugins=[GoogleAI(), Middleware()],
    model='googleai/gemini-flash-latest',
)

middleware = [
    Filesystem(root_dir=str(workspace), allow_write_access=True),
    Artifacts(),
    Compaction(
        max_context_tokens=200_000,
        trigger_fraction=0.85,
        keep_fraction=0.10,
        summary_model='googleai/gemini-flash-latest',
    ),
]

messages: list[Message] = [
    Message(
        role=Role.SYSTEM,
        content=[Part(root=TextPart(text=(
            'You are a coding agent. Work only inside the workspace directory. '
            'Read files before editing them.'
        )))],
    ),
]

async def run_turn(user_input: str) -> None:
    global messages
    response = await ai.generate(
        prompt=user_input,
        messages=messages,
        max_turns=20,
        use=middleware,
    )
    messages = response.messages
    print(response.text)
```

Tell your coding agent to copy `compaction.py` from the recipe repo into your app if you have not already. The knobs worth tuning first are `keep_fraction` and `trigger_fraction` for your model's real context size.

## What it buys you

The messages-array figure in Part 1 shows compaction slot by slot. Here is the same list aggregated into one bar per stage:

![The same transcript at two stages: 50.5k characters raw, then structurally compacted with old read deliveries and tool arguments clipped.](/figures/compaction-stages.svg)

The drop is almost entirely structural. Old `<read_file>` user deliveries shrink to short previews, the `write_file` argument clip removes the 15k rust bar, and the pytest traceback outside the keep window gets truncated. What remains in the tail is prose, recent reads, and small acks. On a transcript this size summarization does not fire yet; that layer kicks in near the context ceiling.

Tests and a longer README live in the recipe repo:

```bash
git clone https://github.com/jeffdh5/python-middleware-recipes
cd python-middleware-recipes && uv sync && uv run pytest -q
```

## What is still open

Re-read dedup exists in Genkit's Go plugin but not Python yet. The official `Filesystem` middleware still queues reads instead of paginating inline. Those are harness fixes upstream of compaction, but they change where the bytes land before your middleware ever runs.

A note on scope: this is a recipe you copy and tune, not a supported Genkit plugin. The measurement above is one synthetic transcript plus unit tests, not a long production run.

Further reading: Open SWE's [`agent/middleware/tool_artifact.py`](https://github.com/langchain-ai/open-swe) and `agent/server.py`; Deep Agents' [`middleware/summarization.py`](https://github.com/langchain-ai/deepagents) and `graph.py`. The Genkit recipe is [jeffdh5/python-middleware-recipes](https://github.com/jeffdh5/python-middleware-recipes). The figures were generated by [a script](https://github.com/jeffdh5/agent-eng-blog/tree/main/scripts) that builds the transcript above and runs it through the recipe's compaction functions.
