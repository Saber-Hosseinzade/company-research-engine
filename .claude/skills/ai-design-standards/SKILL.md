---
name: ai-design-standards
description: Design standards for AI agents, RAG and document AI in this repo. Apply when writing a spec, designing an agent, retrieval or extraction pipeline, or reviewing such code.
---

# AI design standards

A design must meet every rule below, or name the rule it cannot meet as an area of concern.

## Models

- A1. The model is configuration (`MODEL_PROVIDER`, `MODEL_ID`); no code depends on one model.
- A2. Every model call has a timeout, a retry limit and recorded token usage.
- A3. Estimate cost per run in the spec; replace the estimate with a measurement once it runs.

## Agents and graphs

- A4. Deterministic work (arithmetic, parsing, validation, routing by rule) is plain code, not a model
  call. Use an agent only where judgement is needed, and say why in the spec.
- A5. Each agent has a narrow job, its own tools, and a defined failure path ("not found", fallback, or
  ask a human).
- A6. Actions with side effects (writes, sends, payments, deletes) need a human approval step.

## Retrieval and documents

- A7. Every claim in an output cites its source (document, page or chunk ID). Citations are checked in
  code; an unverifiable citation is dropped.
- A8. When the evidence is not enough, answer "not found" rather than guess.
- A9. Chunk metadata (source, page, version) is stored with each chunk.

## Quality

- A10. Tests stay offline: no AWS, no model calls, no network. Model calls are faked.
- A11. Behaviour that depends on a model is measured with an eval set (answerable and unanswerable
  cases), and the metric is stated in the spec.
- A12. Outputs are analysis, not advice; say when a model can be wrong.
