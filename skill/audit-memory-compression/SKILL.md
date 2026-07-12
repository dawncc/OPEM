---
name: audit-memory-compression
description: Trace and evaluate multi-turn context or long-term memory compression in an agent codebase. Use when Codex must determine whether compression is rule-based, LLM-generated, or provider-native; locate page, API, worker, hook, and model-request boundaries; reconstruct compression prompts and rules; assess information-loss risks; or propose and validate a safe prompt/rules design.
---

# Audit Memory Compression

Trace behavior from source evidence before describing the architecture. Distinguish context-window compaction from long-term memory consolidation and session summaries.

## Workflow

1. Search for `compact`, `compress`, `summary`, `prompt`, token budgets, lifecycle hooks, model endpoints, and provider compaction APIs.
2. Build the actual call graph: page -> API -> queue/worker -> model request -> storage, plus the separate model-input assembly path.
3. Classify every compression stage as deterministic rules, extractive selection, LLM abstractive summary, provider-native compaction, or hybrid.
4. Verify production reachability. Treat code used only by tests, experiments, or documentation as unintegrated.
5. Reconstruct exact prompt messages, roles, interpolation fields, model settings, output parsing, and fallback behavior.
6. Reconstruct rules in execution order, including protected content, budgets, relevance scoring, recent-turn retention, tool-output handling, hard caps, and failure fallback.
7. Report facts, gaps, risks, and recommendations separately. Cite file and line evidence.

## Required distinctions

- Do not call an extractive heuristic an LLM summary.
- Do not assume a page constructs prompts merely because it displays compression status.
- Do not treat a pre-compaction hook as the component performing compaction.
- Do not infer production use from a standalone function; find its caller.
- Keep complete archives separate from derived summaries and working context.

## Prompt and rules review

Read [references/prompt-and-rules.md](references/prompt-and-rules.md) when reconstructing or designing multi-turn compression prompts and policies.

Read [references/industry-patterns.md](references/industry-patterns.md) when comparing a Python backend with provider-native compaction, rolling LLM summaries, selective clearing, or hybrid context management.

For LLM prompts, check source fidelity, protected facts, unresolved work, decisions and rationale, tool evidence, source identifiers, structured output, injection resistance, and whether a previous summary is re-summarized without original evidence.

For deterministic rules, check invariant priority before scoring heuristics. Verify that current user intent, system/repository rules, explicit protected items, errors, exact numbers, paths, code/diffs, schemas, and negations survive budget pressure.

## Output format

Return:

1. A one-paragraph conclusion answering whether an LLM performs compression.
2. A stage table with trigger, implementation, input, output, model use, fallback, persistence, and production status.
3. The reconstructed prompt and ordered rules.
4. Risks and missing integration.
5. A recommended hybrid design and validation plan when changes are requested.

Never present a proposed prompt as the current implementation. Label inferred behavior and unverified provider behavior explicitly.
