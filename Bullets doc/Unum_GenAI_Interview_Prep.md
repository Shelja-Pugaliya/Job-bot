# Manager Round Prep — Gen AI Engineer, Unum

## 1. Deep Dive on Your Current Work (most likely focus, since she may know it already)

**"Walk me through the AI-assisted tooling framework you built. What did the agentic workflow actually do, step by step?"**
- Be ready to explain: what triggered the workflow, what the agent(s) did at each stage (e.g., read a spec → generate CLI plugin scaffold → run tests → flag issues), and where a human checked the output.
- She will ask "what LLM/API did you call, and how did you structure the prompt?" — have the actual prompt structure or a simplified version ready (system prompt, few-shot examples, output format constraints, etc.)
- Have the "70% reduction in engineering lifecycle friction" number ready to defend: friction in *what specifically* (time to scaffold a plugin? code review cycles? something else), measured how, compared to what baseline.

**"How did you validate the AI-generated output was correct/safe to use?"**
- This is the "design and lead validation and iteration cycles" JD requirement — she'll want a concrete answer. Think about: did you have test cases, manual review gates, SonarQube/Mocha checks on generated code, a rollback path if generation was wrong?
- Sample answer framework: *"Every generated plugin went through the same CI quality gates as human-written code — SonarQube for static analysis, Mocha for functional tests — so the AI output wasn't trusted blindly, it had to clear the same bar. Where I iterated on the prompt itself was when I saw a class of failures repeat, e.g. [specific pattern], and I'd adjust the prompt template or add a few-shot example to correct it."*

**"What would you do differently if you rebuilt this?"**
- Have one honest answer ready — evaluators like self-awareness over "it was perfect." E.g., "I'd build a proper eval set upfront rather than validating case-by-case" (this also signals you understand LLM evaluation, which is a gap area).

## 2. LLM / GenAI Fundamentals (likely, since JD names GPT-4 and BERT specifically)

Your CV leans DevOps-with-prompt-engineering rather than deep LLM theory, so expect her to probe whether you understand what you've been using.

**"What's the difference between a generative model like GPT-4 and a discriminative model like BERT? Why would you use one over the other?"**
- Answer: GPT-4 is generative/autoregressive — predicts next tokens, good for generating new text (code, prose, plugin scaffolds). BERT is encoder-only/discriminative — good for classification, understanding/labeling existing text (sentiment, NER, intent classification), not for generating fluent new text.
- Tie back to your work: *"My work has been generative-side — using GPT-4-class models to produce code — but I understand BERT-style models would fit better for a classification task, e.g. auto-triaging support tickets or classifying prompt failure types."*

**"How would you evaluate whether one prompt is better than another?"** (this is your weak spot — prep this one carefully)
- Answer framework: define a small labeled eval set of representative inputs and expected outputs → run both prompt versions against it → score with a mix of automated metrics (exact match, pass/fail on tests, or an LLM-as-judge) and human spot-checks → track regressions over time, not just one-off comparisons.
- If you haven't done formal benchmarking, say so honestly and pivot to what you *have* done: *"I haven't built a formal eval harness yet, but I did informal iteration — comparing outputs against a fixed set of prior tickets/cases and checking pass rate on the CI gates. I'd want to formalize that into a proper eval set given the chance."*

**"What's prompt injection / how do you keep generated output safe?"**
- Basic answer: sanitize/validate any user-supplied content that gets inserted into a prompt, don't let generated output execute with elevated privileges without review, treat model output as untrusted input to your pipeline (same as you'd treat any external input).

**"What's fine-tuning vs. prompt engineering, and when would you reach for each?"**
- Prompt engineering: fast, no training cost, good when the base model already "knows" the task and just needs framing/examples. Fine-tuning: needed when you want consistent behavior change across many inputs, have a large labeled dataset, or need the model to learn domain-specific patterns prompting can't reliably produce.

## 3. Your Other Projects

**Subtitle Translator (Azure OpenAI)**
- "How did you preserve context/structure across subtitle chunks?" — be ready to describe how you handled continuity between subtitle segments (e.g., passing prior lines as context, timestamp/structure constraints in the prompt).
- "Why Azure OpenAI over the direct OpenAI API?" — likely reasons: existing Azure infra, data residency/compliance, AD auth integration.

**Renewable Energy Risk Monitoring**
- "Why Random Forest and not a neural network/other model?" — be ready to justify: interpretability, less data-hungry, good baseline for tabular/time-series features, faster to iterate.
- "How did you handle streaming data with Kafka — what happens if the model needs to score in real time?" — think latency, windowing, feature freshness.

## 4. System Design / Architecture (likely given "very technical")

**"How would you design a system that generates and validates AI output at scale, safely, for a regulated company like Unum (insurance)?"**
- Talk through: input validation → prompt template with guardrails → generation → automated eval/quality gate (reuse your CI/CD instinct here — this is your strongest card) → human-in-the-loop review for high-risk cases → logging/audit trail (important for insurance/compliance) → rollback mechanism.
- This is a great question to bridge your DevOps strength into the GenAI role — lean into it.

## 5. Behavioral (STAR format — prep 1 story each)

- **"Tell me about a time you had to learn something completely new quickly."** → Use the pivot into prompt engineering/agentic workflows at Unum, or picking up GenAI concepts for your MSc alongside a full-time internship.
- **"Tell me about a conflict or disagreement with a teammate/stakeholder."**
- **"Tell me about a time you mentored or helped someone less experienced."** (JD explicitly wants mentoring — if you don't have a formal example, think of any code review, pairing, or informal knowledge-sharing you've done)
- **"Why do you want to move from DevOps into a Gen AI Engineer role specifically?"** — be honest and coherent: your interest was sparked by building the agentic tooling framework, and you want to go deeper into the AI/prompt side rather than just the platform/CI side.
- **"Why Unum, and why this team?"** — since you're internal, this should be genuine and specific (what you've seen of the team, what you want to keep building on).

## 6. Questions to Ask Her

Asking sharp questions signals seniority. A few options:
- "What does the evaluation/benchmarking process for prompts or models look like on the team today — is there a formal eval harness, or is it more ad hoc?"
- "How does the team balance moving fast on GenAI features with the compliance/audit requirements that come with insurance data?"
- "What would success look like in this role in the first 6 months?"
- "What's the split between building new GenAI capabilities vs. maintaining/improving existing prompt-based tooling?"

## 7. One Honest Gap to Own, Not Hide

If she asks directly about GPT-4/BERT evaluation depth or fine-tuning experience and you don't have it — don't overclaim. Say plainly: *"My hands-on GenAI experience so far has been on the prompt engineering and integration side rather than formal model evaluation or fine-tuning — that's the area I'm most keen to grow into, and it's part of why this role appeals to me."* Technical interviewers generally respect a clear, confident admission far more than a stretched answer that falls apart under a follow-up question.
