# Intent: run the filings agent on AgentCore Runtime

Author: Saber-Hosseinzade. Status: draft. Date: 2026-11-25.

## Problem

The v0 filings agent (merged 2026-11-25, PR #11) runs only on a developer machine: FastAPI in Docker
Compose, with filings and embeddings in a local Postgres. Nothing outside that machine can call it, so
the stock tool (roadmap step 3) has no endpoint to use. The local API has no authentication and is safe
only because it binds to localhost (v0 spec concern 8).

## Proposed outcome

The same container image runs on Amazon Bedrock AgentCore Runtime in eu-central-1, in the sandbox
account, and answers research questions for authorised AWS callers.

- The image serves AgentCore's contract (`/invocations` and `/ping` on port 8080, ARM64) next to the
  existing API, with the same agent and the same answers.
- Filings, pages and embeddings live in the dev Supabase project (Postgres + pgvector) instead of the
  local database. `make ingest` and the embedding step can load it.
- Chat and embeddings stay on OpenRouter (Haiku 5.5, Titan v2). The API key and the database connection
  string come from AWS Secrets Manager at runtime, never from the image or the repo.
- Only IAM-authenticated (SigV4) callers in the sandbox account can invoke it.
- The AWS resources are defined in CDK, deployed only after the user approves, and the teardown is
  documented.

Success is measured by:

- one invocation of the deployed runtime returns a validated note;
- `make eval-v0` run against the deployed runtime passes the v0 targets: acceptance >= 0.85, not-found
  1.0, citation validity 1.0, no advice;
- the local Compose setup and `make test` (offline) keep working unchanged.

## Affected users and systems

- The user, invoking the runtime with AWS credentials; later the stock tool's role (step 3).
- AWS sandbox account, eu-central-1: AgentCore Runtime, ECR, IAM roles, Secrets Manager, CloudWatch
  logs, and CDK bootstrap resources if they are missing.
- Dev Supabase project: new tables and data (filings, pages, chunks, embeddings).
- OpenRouter: called from AWS instead of the dev container. Prompts and public filing text only, as
  in v0.

## Constraints

- AWS profile `sandbox` and region `eu-central-1` only. No deploy, change or destroy without asking first.
- No secrets in code, the image or the repo; Secrets Manager in AWS, `.env.local` locally.
- Logs carry IDs, counts, timings and tokens only (v0 R17), in CloudWatch as well.
- No user identifier is sent to OpenRouter (v0 concern 6); OpenRouter routing stays zero-retention.
- Answers and validation behave the same as v0. No change to the agent, prompts or validator unless
  the deploy requires it.
- Public filing data only. Data stored in Supabase leaves AWS; accepted for public filings (user
  decision, 2026-11-25).

## Out of scope

- The stock tool integration (step 3) and any other caller.
- User logins (Cognito/JWT) or a hosted question page.
- The Bedrock model path; models stay on OpenRouter.
- New companies, filing types or agent features (roadmap step 4).
- Production hardening: multiple environments, autoscaling tuning, alerting, CI/CD deploys.

## Open questions

- Monthly cost of the runtime, ECR, Secrets Manager and Supabase at this usage; estimate in the spec.
- Supabase project region and plan limits (storage, connections) for about 7.5k chunks with 1,024-dim
  vectors.
- Load Supabase by re-embedding (about $0.05) or by copying the existing vectors.
- Does AgentCore's public network mode reach Supabase and OpenRouter, or is VPC setup needed?
- How `make eval-v0` calls the deployed runtime (SigV4 client) while keeping its scoring the same.
- AgentCore session and timeout limits against a run of about 10 seconds, and concurrent invocations.
- Is CDK already bootstrapped in the sandbox account and region?
