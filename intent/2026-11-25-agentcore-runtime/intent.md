# Intent: run the filings agent on AgentCore Runtime

Author: Saber-Hosseinzade. Status: draft. Date: 2026-11-25.

## Problem

The v0 filings agent (merged 2026-11-25, PR #11) runs only on a developer machine: FastAPI in Docker
Compose, with filings and embeddings in a local Postgres. Nothing outside that machine can call it, so
the stock tool (roadmap step 3) has no endpoint to use. The local API has no authentication and is safe
only because it binds to localhost (v0 spec concern 8). Later steps will cover hundreds of companies,
so the cloud database must grow from today's 144 MB to an estimated 5–15 GB without a redesign.

## Proposed outcome

The same container image runs on Amazon Bedrock AgentCore Runtime in eu-central-1, in the sandbox
account, and answers research questions for authorised AWS callers.

- The image serves AgentCore's contract (`/invocations` and `/ping` on port 8080, ARM64) next to the
  existing API, with the same agent and the same answers.
- Filings, pages and embeddings live in Aurora Serverless v2 (PostgreSQL with pgvector) that pauses
  when idle. The agent reaches it through the RDS Data API (HTTPS, IAM), so the runtime needs no VPC
  or NAT gateway.
- The runtime can also reach the dev Supabase project over the internet. Supabase will hold other data
  that the agent needs in a later step. In this step only the connection is set up and checked.
- Chat and embeddings stay on OpenRouter (Haiku 5.5, Titan v2). API keys and connection strings come
  from AWS Secrets Manager at runtime, never from the image or the repo.
- Only IAM-authenticated (SigV4) callers in the sandbox account can invoke it.
- The AWS resources are defined in CDK, deployed only after the user approves, and the teardown is
  documented.

Success is measured by:

- one invocation of the deployed runtime returns a validated note;
- `make eval-v0` run against the deployed runtime passes the v0 targets: acceptance >= 0.85, not-found
  1.0, citation validity 1.0, no advice;
- the deployed runtime connects to Supabase (connection check logged);
- the local Compose setup and `make test` (offline) keep working unchanged.

## Affected users and systems

- The user, invoking the runtime with AWS credentials; later the stock tool's role (step 3).
- AWS sandbox account, eu-central-1:
  - AgentCore Runtime, ECR, IAM roles;
  - Aurora Serverless v2 in a VPC with no internet access;
  - Secrets Manager and CloudWatch logs;
  - the CDK bootstrap resources, if they are missing.
- Dev Supabase project: reached from the runtime; no tables or data change in this step.
- OpenRouter: called from AWS instead of the dev container. Prompts and public filing text only, as
  in v0.

## Constraints

- AWS profile `sandbox` and region `eu-central-1` only. No deploy, change or destroy without asking first.
- No secrets in code, the image or the repo; Secrets Manager in AWS, `.env.local` locally.
- Logs carry IDs, counts, timings and tokens only (v0 R17), in CloudWatch as well.
- No user identifier is sent to OpenRouter (v0 concern 6); OpenRouter routing stays zero-retention.
- Answers and validation behave the same as v0. No change to the agent, prompts or validator unless
  the deploy requires it.
- Low fixed cost at about an hour of use a day: no always-on NAT gateway or database compute.
- Public filing data only.

## Out of scope

- Adding companies or filing types (hundreds of companies, 10-Qs): a later step. This step only
  chooses a database that can hold them.
- Which data goes to Supabase and how the agent uses it.
- The stock tool integration (step 3) and any other caller.
- User logins (Cognito/JWT) or a hosted question page.
- The Bedrock model path; models stay on OpenRouter.
- Production hardening: multiple environments, alerting, CI/CD deploys.

## Open questions

- Monthly cost of the runtime, Aurora, ECR and Secrets Manager at this usage; estimate in the spec.
- Does Aurora auto-pause (0 ACU) work in eu-central-1 for a PostgreSQL version that has pgvector and
  the Data API, and how long does resume take?
- Data API limits (result size, parameter types for vectors) against our retrieval queries.
- Load Aurora by copying the measured vectors or by re-embedding.
- How `make eval-v0` calls the deployed runtime (SigV4 client) while keeping its scoring the same.
- AgentCore session and timeout limits against a run of about 10 seconds, and concurrent invocations.
- Is CDK already bootstrapped in the sandbox account and region?
