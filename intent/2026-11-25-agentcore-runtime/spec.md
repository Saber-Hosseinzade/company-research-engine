# Spec: run the filings agent on AgentCore Runtime

From: intent.md (Saber-Hosseinzade). Status: ready for review. Date: 2026-11-25.
Skills applied: security-policy, ai-design-standards.

## Requirements

Container

- R1. One Dockerfile builds one ARM64 image, on Python 3.14 (the newest the AWS tooling supports, after
  the toolchain change, concern 9), for both local Compose and AgentCore. The app listens on
  `0.0.0.0:8080`. Compose publishes it as `127.0.0.1:8000` (v0 R15 unchanged for users).
- R2. The image carries only what the app needs at runtime. `edgartools`, which brings pandas, numpy
  and pyarrow, moves to a `fetch` dependency group used by `make fetch`, so it is not in the image.
- R3. `POST /invocations` takes `{ticker, question}` and returns the same `Note` JSON as `/research`.
  It uses the same code path and the same error mapping: 422 for out of scope before any model call,
  502, 503 and 500 with fixed messages.
- R4. `GET /ping` returns HTTP 200 with `{"status": "Healthy"}` (the AgentCore contract) and no
  `time_of_last_update`.

Settings and secrets

- R5. When the setting `APP_SECRET_ID` is set, the app reads `OPENROUTER_API_KEY` and
  `SUPABASE_DATABASE_URL` from that Secrets Manager secret (one JSON secret, eu-central-1) at startup.
  When it is not set, settings come from the environment and `.env.local` as today. Secret values are
  never logged or returned (S1).
- R6. New settings `APP_SECRET_ID`, `DB_CLUSTER_ARN`, `DB_SECRET_ARN`, `DB_NAME` and
  `SUPABASE_DATABASE_URL` are added empty to `.env.example` and typed in `config.py` (S2).

Filings database (Aurora)

- R7. When `DB_CLUSTER_ARN` is set, retrieval and the validator's page and filing lookups run through
  the RDS Data API (`rds-data` `ExecuteStatement`, IAM auth, the cluster's credential secret in
  `DB_SECRET_ARN`). When it is not set, they use psycopg and `DATABASE_URL` as today. The SQL is the
  same in both modes, and the M4 retrieval tests pass in both.
- R8. A Data API call that fails with `DatabaseResumingException` (the cluster was paused) is retried
  with backoff, for up to 60 seconds in total. Resume takes about 15 s, or 30 s or more after a day
  paused. Then the request returns 503. The boto3 read timeout is at least 60 s, above the Data API's
  45-second statement timeout, so a slow call is never retried while it is still running.
- R9. `make db-copy` copies `filings`, `pages` and the window-strategy `chunks` (with their embeddings)
  from the local database to Aurora through the Data API. The schema is applied first with the
  existing `apply_schema`. It refuses to write to a non-empty target unless `--replace` is given,
  then checks that the row counts match (16 / 1,525 / 5,940 today). There is no re-embedding, so the
  deployed agent searches the vectors that were measured.
- R10. After the copy, `make golden-check` against Aurora reports problems=0.
- R10a. The local database moves to the same major version: `compose.yaml` uses the pgvector image for
  PostgreSQL 18. The existing PostgreSQL 17 data is moved across once with `pg_dump` and `pg_restore`
  (no re-embedding). `make test-db` and the M4 recall numbers are re-checked on 18.

Supabase

- R11. When `SUPABASE_DATABASE_URL` is set, the app checks once at startup that Supabase answers: a
  TLS connection with a 10-second timeout and `SELECT 1`. It logs `supabase reachable seconds=…` or
  `supabase unreachable error=<type>`. The check never stops the app from starting. No Supabase data is
  read or written in this step.

AWS (CDK, Python, `infra/`)

- R12. One CDK stack in the sandbox account and eu-central-1 (S3) creates:
  - a VPC with two private isolated subnets and no internet or NAT gateway, for Aurora only;
  - an Aurora PostgreSQL Serverless v2 cluster:
    - the latest stable Aurora PostgreSQL, 18.6 today: 0 ACU from 18.3, Data API from 18.3,
      pgvector 0.8.2. A single `db.serverless` writer. New clusters get the newest Aurora serverless
      platform version (4 in eu-central-1) automatically;
    - minimum 0 ACU, auto-pause after 5 minutes idle, maximum 4 ACU;
    - Data API enabled, master credentials managed in Secrets Manager;
    - storage encrypted, a security group with no inbound rules;
  - the image asset (built for `linux/arm64`, pushed to ECR);
  - one app secret for `OPENROUTER_API_KEY` and `SUPABASE_DATABASE_URL`, created without a value; the
    user sets it with the AWS CLI, so no secret is ever in CDK, synth output or Git;
  - the runtime's execution role;
  - a CloudWatch log group with 30-day retention;
  - the AgentCore Runtime:
    - platform version V2, set explicitly (V1 is the default). V2 starts sessions from a snapshot,
      which keeps cold starts fast at any image size;
    - container image, network mode PUBLIC (so it reaches OpenRouter and Supabase), HTTP protocol;
    - IAM (SigV4) authorisation, idle session timeout of 5 minutes;
    - environment variables `APP_SECRET_ID`, `DB_CLUSTER_ARN`, `DB_SECRET_ARN`, `DB_NAME`,
      `MODEL_PROVIDER`, `MODEL_ID` and `EMBEDDING_MODEL_ID`.
- R13. The execution role allows only what the runtime needs, with no `*` on both action and
  resource (S4):
  - ECR pull from the asset repository;
  - writing to its log group;
  - `secretsmanager:GetSecretValue` on the app secret and the cluster secret;
  - `rds-data:ExecuteStatement` and `BatchExecuteStatement` on the cluster.
- R14. Every resource carries an `owner` tag and a `project` tag (S6). The owner value comes from CDK
  context or the environment at deploy time, not from the repo.
- R15. The runtime accepts only SigV4 calls from principals in the sandbox account. It has no
  resource policy for other accounts, no OAuth authoriser and no public URL. Aurora has no public
  endpoint (S5).
- R16. `make deploy` runs `cdk deploy`, `make destroy` runs `cdk destroy`, and `make invoke` sends one
  question to the deployed runtime and prints the note. All three are pinned to profile `sandbox` and
  region `eu-central-1`. Claude asks before running deploy or destroy.
- R17. `make destroy` deletes the cluster without a final snapshot, because the data is rebuilt with
  `make fetch ingest chunk embed db-copy`. A teardown section in `infra/README.md` lists what
  `make destroy` removes and what stays, with the command to remove each remaining item:
  - the CDK bootstrap stack and its image repository;
  - the secrets' recovery window.

Operation and evaluation

- R18. Logs in CloudWatch follow v0 R17: IDs, counts, tokens and timings only. AgentCore observability
  (tracing) is not enabled in this step.
- R19. `make eval-v0 ARGS="--runtime-arn <arn>"` sends each golden question to the deployed runtime
  (`bedrock-agentcore` `InvokeAgentRuntime`, boto3, SigV4). It uses one runtime session ID per eval
  run and scores the notes with the same code and targets as the local run (acceptance >= 0.85,
  not-found 1.0, citation validity 1.0, no advice). The page store for scoring is read from the local
  database, which holds the same data.
- R20. `make test` stays offline (A10). The secret overlay, the Data API adapter (on a faked `rds-data`
  client, including resume retries), the `/invocations` route, the Supabase check and the remote eval
  client are all tested with fakes. The CDK stack has a synth test that asserts R12–R15 (no NAT,
  0 ACU minimum, roles, tags, auth, network mode) with no AWS calls.

## Design

**Flow.** Caller (AWS credentials) → `InvokeAgentRuntime` (SigV4) → AgentCore microVM (public network)
→ container `POST /invocations` → the same per-request `Agent` as v0. Retrieval and validation go to
Aurora through the Data API over HTTPS with IAM. Chat and embeddings go to OpenRouter. At startup, the
container also checks it can reach Supabase. The note JSON comes back to the caller.

There is no VPC in the request path, so no NAT gateway or VPC endpoints are needed: the VPC exists
only because Aurora requires one, and nothing connects into it.

**Agent.** Unchanged from v0: one LangGraph graph, the same nodes, prompts, validator and failure path
(A4, A5, A7, A8, A12). This step changes only where it runs, where its data lives and where its
settings come from.

**Database access.**
- `store.connect()` returns either a psycopg connection (local) or a `DataApiConnection` (when
  `DB_CLUSTER_ARN` is set).
- `DataApiConnection` implements the small part of the psycopg interface our code uses:
  `execute(sql, params)` with `fetchone()` and `fetchall()`, and `transaction()` as a no-op.
- It rewrites `%s` placeholders to named parameters. Vectors are sent as text and cast with
  `::vector`, as the SQL already does for psycopg. Dates come back as strings and are parsed where the
  code expects dates.
- Retrieval, `DbSources` and `apply_schema` then need no changes.
- The Data API supports the `VECTOR` type and passes dates as strings (`typeHint` `DATE`).
- Responses stay far below the 1 MiB Data API limit: the largest call reads 8 pages of about
  4 KB each. Bulk reads such as `db_page_store` are used only by the local eval and golden-check, and
  are paged when they run against Aurora.

**Data copy.** `app.db_copy` reads from local psycopg and writes with `BatchExecuteStatement`. Each
request must stay under the Data API's 4 MiB limit. A window chunk with its vector as text is about
12 KB, so batches of 200 rows (about 2.4 MB) fit. It writes in the order filings, pages, chunks and checks
the counts at the end. Only window chunks are copied, because the page strategy was not kept in M4.

**Remote eval.** `eval_v0` gets an "answerer" choice:
- in-process `Agent` (today);
- `RuntimeAnswerer`, which calls `InvokeAgentRuntime` and parses the reply as `Note` (S11).

Scoring, summaries and saved files are shared.

**Infra.** Python CDK app in `infra/` with `aws-cdk-lib` (the `aws_bedrockagentcore.Runtime` construct;
`rds.DatabaseCluster` for Aurora) and `constructs`, in an `infra` dependency group (S12). The CDK CLI
runs with `npx` at the latest stable version, pinned, on Node 24: the newest Node that CDK supports.
CDK ended support for Node 18 on 2025-11-30 and for Node 20 on 2026-10-30. `aws-cdk-lib` (2.278.0
today), `constructs`, jsii and the boto3 used for `rds-data` and `bedrock-agentcore` are the latest
stable releases at build time, each checked for Python 3.14 support. If `aws-cdk-lib` has no named
constant for Aurora PostgreSQL 18.6, the stack uses `AuroraPostgresEngineVersion.of("18.6", "18")`. Every version is pinned in `uv.lock` or `package-lock.json` (the user's rule: latest stable
versions throughout the stack). New runtime dependencies:
none (boto3 is already one).

**Cost (A3, estimates until measured).**

| Item | Estimate per month |
| --- | --- |
| AgentCore Runtime, about 1 hour of use a day, 1 vCPU / 2 GB (v2 rates; no eu-central-1 rates published) | $1–5 |
| Aurora compute: about 1 ACU while in use, paused otherwise (about $0.14 per ACU-hour, estimated from US East $0.12) | about $4–5 |
| Aurora storage: about 0.1 GB now, 5–15 GB with hundreds of companies (about $0.11 per GB) | $0.01 now, $0.50–1.70 later |
| Aurora I/O (Standard, $0.20 per million in US East) and backups (free up to the cluster size) | under $1 |
| Secrets Manager: app secret plus cluster secret | about $0.80 |
| ECR image, under 1 GB after R2 | about $0.10 |
| CloudWatch logs | cents |
| Supabase: the user's existing dev project | unchanged |
| OpenRouter: about $0.002 per question (Haiku 5.5, measured in M6); one 50-item eval about $0.10 | usage-based |

Total AWS: about $7–12 a month at 1 hour a day. Without auto-pause, the 0.5 ACU minimum alone would
add about $50, and a NAT gateway about $38. This design avoids both.

## How each constraint in the intent is met

| Constraint | Met by |
| --- | --- |
| Sandbox profile and eu-central-1 only; ask before deploy or destroy | R12, R16 (S3) |
| No secrets in code, image or repo | R5, R6, R12 (secrets created empty) (S1, S2) |
| Logs: IDs, counts, timings, tokens only | R11, R18 (S9) |
| No user identifier to OpenRouter; zero-retention routing | Unchanged v0 code (`llm.py`); R3 shares the v0 handler |
| Answers behave as in v0 | Same agent and SQL (R7); R9 copies the same vectors; R19 re-runs the v0 targets |
| Low fixed cost: no always-on NAT or database compute | R12: no NAT, 0 ACU minimum with auto-pause |
| Runtime reaches Supabase | R11, R12 (PUBLIC network mode) |
| IAM callers only | R15 (S5) |
| CDK with documented teardown | R12, R16, R17 (S6) |
| Local Compose and offline tests keep working | R1, R7 (psycopg mode unchanged), R20 (A10) |
| Room for hundreds of companies | Aurora storage grows automatically; searches stay scoped to one ticker |

## Evaluation

- Offline tests (`make test`): R20.
- `make test-db`:
  - the existing retrieval and store tests still pass on psycopg;
  - `db-copy` between two local test schemas copies all rows and refuses a non-empty target (the
    Data API writer is swapped for psycopg in this test).
- Measured after deploy:
  - `make invoke` returns a validated note;
  - cold-start latency (runtime and Aurora resume together), warm latency and the Supabase check line;
  - `make eval-v0 ARGS="--runtime-arn …"` meets the v0 targets (A11): acceptance >= 0.85 on the 40
    answerable items, not-found 1.0 on the 10 unanswerable, citation validity 1.0, no advice;
  - Aurora pauses after idle (`ServerlessDatabaseCapacity` reaches 0);
  - a week of the bill replaces the cost estimates above (A3).

## Open questions from the intent

- **Monthly cost:** estimated above (about $7–12 a month in AWS at 1 hour a day, plus OpenRouter use);
  measured after deploy.
- **Auto-pause** (checked 2026-11-25, AWS docs):
  - it needs Aurora PostgreSQL 16.3, 15.7, 14.12 or 13.15 or later;
  - resume takes about 15 s, or 30 s or more after more than a day paused;
  - a Data API call resumes a paused writer and fails with `DatabaseResumingException` until it is up,
    which R8 retries;
  - the Data API is available in eu-central-1 for 16.1+, 17.4+ and 18.3+;
  - the latest version, 18.6, ships pgvector 0.8.2.

  Still to check in the account: that 18.6 can be created with 0 ACU in eu-central-1 (concern 2).
- **Data API limits** (checked 2026-11-25):
  - responses are at most 1 MiB and requests at most 4 MiB, SQL is at most 64 KB, and statements time
    out after 45 s by default;
  - queries go to the writer only, and T instance classes are not supported (we use `db.serverless`);
  - `VECTOR` is a supported type;
  - array parameters are not supported, and our SQL uses none.
- **Copy or re-embed:** copy (R9). It is free, deterministic, and keeps the measured vectors.
- **Eval against the runtime:** R19.
- **Timeouts and concurrency** (AgentCore quotas page):
  - synchronous requests may run 15 minutes;
  - a cold first request may take about 10 s for the run plus up to 30 s or more for Aurora to resume;
  - sessions end after 5 minutes idle (R12) or 8 hours at most;
  - this region allows 2,500 active sessions per account.
- **CDK bootstrap:** the project gets a new, dedicated AWS account (user decision 2026-11-25, not
  created yet), so bootstrapping is needed. The build asks before running `cdk bootstrap` (concern 3).

## Areas of concern

1. **Supabase is open to the internet.** AgentCore's public egress IPs are not fixed, so Supabase's
   network restrictions must allow all addresses. Only the database password and TLS protect it, which
   meets S5 (authenticated) but is weaker than a private network. The free plan pauses a project after
   about a week without activity. This step only checks the connection; which data goes to Supabase,
   and its region, is decided with the step that uses it. Owner: user.
2. **Service details left to check.** The docs confirm the Data API, pgvector and auto-pause for
   Aurora PostgreSQL 16 and 17 in eu-central-1, `VECTOR` support in the Data API, the batch limits, and
   the V2 platform version (2026-11-25). Two things are still unchecked:
   - that this account can create Aurora PostgreSQL 18.6 with 0 ACU in eu-central-1 (a read-only
     `describe-db-engine-versions`, once the SSO login is renewed);
   - the pgvector version gap: Aurora 18.6 ships 0.8.2, but we measured on 0.8.7. The local image
     moves to PostgreSQL 18 (R10a), but its pgvector stays newer than Aurora's. Our queries use only
     `<=>` and exact search, so results should match; the deployed golden-check and eval are the
     guard.

   If a check fails, the build stops and asks. Owner: user.
3. **CDK tooling and bootstrap.** The dev container has Node 18. CDK needs Node 24, the newest Node it
   supports (support for 18 ended 2025-11-30 and for 20 on 2026-10-30). The user approved the
   `.devcontainer/` change (2026-11-25); it is made in the toolchain change (concern 9). The shared CDK bootstrap stack stays after
   `make destroy` (R17 documents its removal). Owner: user.
4. **Cold first request.** Aurora may need 15–30 s or more to resume. Runtime V2 starts from a
   snapshot, so its own cold start should be short. The first question of the day may therefore take
   about 25–45 seconds (estimate). Callers with short timeouts, such as the stock tool later, need
   to allow for this. Owner: user, at step 3.
5. **Error detail is lost through AgentCore.** When the container returns 4xx or 5xx, the caller gets
   424 `RuntimeClientError`. Whether our body reaches the caller is unmeasured. Owner: user, at step 3.
6. **Two database modes.** Local psycopg and the deployed Data API run the same SQL through different
   drivers. Type differences (dates, numerics, vector text) could make deployed answers differ. The
   golden-check (R10) and the deployed eval (R19) are the guard. Owner: user.
7. **Little observability.** With tracing off (R18), debugging relies on log lines with IDs and
   timings. Turning on AgentCore observability needs a check that it records no prompts or outputs
   (S9). Owner: user, later.
8. **IAM-only callers.** Anyone in the sandbox account with `bedrock-agentcore:InvokeAgentRuntime`
   can call the runtime. That is acceptable for a single-user sandbox, but not for shared accounts.
   Owner: user, before step 3.
9. **Toolchain change comes first (user decision 2026-11-25).** Before this step's build, a separate
   small change moves the project to the newest toolchain that the whole stack supports:
   - Python 3.12 to **3.14** (3.14.10) in `pyproject.toml`, the Dockerfile base image
     (`python:3.14-slim`) and the dev container;
   - Node 18 to **24** in the dev container.

   Python 3.15.2 is the newest stable release, but it is not chosen. Checked 2026-11-25 on PyPI:
   - boto3 (used for the Data API and for `InvokeAgentRuntime`), jsii and aws-cdk-lib list Python
     support only up to 3.14;
   - the locked dependencies do not all install on 3.15 for ARM64 from wheels: orjson and zstandard
     (pulled in by langgraph through langsmith) have no usable 3.15 wheels;
   - on 3.14, every locked dependency, including the CDK group, resolves to ARM64 wheels.

   Aurora and AgentCore do not constrain Python: Aurora is reached through boto3, and AgentCore runs
   any container that serves its HTTP contract. Node 24 is the newest Node that CDK supports. The
   toolchain change confirms all of this with `uv sync` and the full test suite (langgraph lists only
   3.10–3.13 in its classifiers, but it is pure Python). Owner: user.
10. **New AWS account not created yet.** The deploy steps (R12–R17, R19) and the account check in
    concern 2 wait for it. The code and offline tests do not. Recommendation: point the existing
    `sandbox` profile name at the new account in eu-central-1. Then `CLAUDE.md`, the dev container and
    the approval-gate hook, which allow only that profile and region, need no change. New-account
    quotas (for example AgentCore sessions, Aurora) are the defaults and are checked before deploy.
    Owner: user.
