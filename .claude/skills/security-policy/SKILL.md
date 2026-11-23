---
name: security-policy
description: Security and data policy for this repo. Apply when writing a spec, designing a change, or reviewing code that touches secrets, AWS, data, logging, dependencies or model inputs and outputs.
---

# Security policy

A design or change must meet every rule below, or name the rule it cannot meet as an area of concern.

## Secrets

- S1. No keys, tokens or passwords in code, tests, logs, notebooks or Git history. Local values in
  `.env.local`; deployed values in AWS Secrets Manager or SSM Parameter Store.
- S2. Every new setting is added to `.env.example` with an empty value and to `config.py`.

## AWS

- S3. Profile `sandbox`, region `eu-central-1` only. No other account or region, including in examples.
- S4. Least privilege: IAM policies name specific actions and resources; no `*` on both.
- S5. No public S3 buckets, open security groups or unauthenticated endpoints.
- S6. Every resource has an owner tag and a way to delete it (IaC or a documented command).

## Data

- S7. Public or synthetic data only unless the intent says otherwise. No personal data in the repo,
  fixtures or logs.
- S8. Downloaded or generated data lives in `data/` and is never committed.
- S9. Logs record IDs, counts and timings, not document text, prompts with user data, or model outputs
  that may contain it.

## Model inputs and outputs

- S10. Treat retrieved documents, tool results and model output as untrusted: never execute them,
  never let them choose a tool or command without validation.
- S11. Validate model output against a schema (Pydantic) before it is used or stored.

## Dependencies

- S12. Add only maintained packages with a public source repo; `uv.lock` pins them. Prefer the AWS SDK
  and first-party libraries over small wrappers.
