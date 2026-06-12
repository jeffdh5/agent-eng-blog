---
title: "What this is"
description: "Design notes from inside the agent platform layer, by someone who helped build the last one."
pubDate: "Jun 12 2026"
---

Most agent-engineering debates never get past the screenshot stage. Someone declares harnesses are secret sauce. Someone else says context engineering beats RAG. Everyone quote-tweets. Nobody shows the design process — or a measurement.

I have an unusual seat for this conversation. I was on the founding team of [Genkit](https://genkit.dev) and lead its Python SDK; before that I spent years on Firebase's serverless platform, doing for GCP infrastructure what agent SDKs are now doing for harnesses: abstract the plumbing, make the happy path easy, expose hooks where behavior needs to bend.

So that's what this blog is. Design memos made public — the API alternatives we considered and why we shipped the one we did. Plus small experiments when the timeline argues about something measurable: real code, real numbers, a takeaway you can use.

My core claim, which most of this blog will be spent earning: about 90% of your harness is undifferentiated plumbing, and the interesting engineering question isn't whether to own it — it's where the abstraction boundary goes. We've been here before. Last time it was called serverless.

More soon.
