---
title: "What this is"
description: "Harness teardowns: what open-source agents converge on, distilled into best practices, implemented with Genkit."
pubDate: "Jun 12 2026"
---

I was on the founding team of [Genkit](https://genkit.dev) and lead its Python SDK. Before that I spent years on Firebase's serverless platform, doing for GCP infrastructure what agent SDKs are now doing for harnesses: abstract the plumbing, make the happy path easy, expose hooks where behavior needs to bend.

If you build SDKs, you end up reading whatever people hand-roll on top of them. So I read agent harnesses — Claude Code, Goose, Open SWE, and the internal agents that companies like Stripe, Ramp, and Coinbase describe on their engineering blogs. Read enough of them and the pattern is hard to miss: independently built systems keep converging on the same pieces. Sandboxed execution, curated toolsets, subagent orchestration, middleware around the loop.

That convergence is the story of this blog, and most posts will follow the same arc:

1. **Read** a real harness — open source or described in public — closely enough to understand a design decision it made.
2. **Distill** the pattern into a best practice: what problem it solves, when it applies, what it costs.
3. **Build** it with Genkit, with real code and a runnable example, so the practice is something you can use rather than nod at.

My core claim, which this blog will spend its life earning: most of a harness is undifferentiated plumbing, and the interesting question isn't whether to own it — it's where the abstraction boundary goes. We've been here before. Last time it was called serverless.

More soon.
