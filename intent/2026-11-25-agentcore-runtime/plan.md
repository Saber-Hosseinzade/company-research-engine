# Plan: run the filings agent on AgentCore Runtime

From: intent.md and spec.md (intent/2026-11-25-agentcore-runtime/). Accepted by: Saber-Hosseinzade.
Date: 2026-11-25. Committed as `intent/2026-11-25-agentcore-runtime/plan.md` on branch
`intent/agentcore-runtime` (intent + spec + plan + code, one PR; user decision). The branch is pushed as a
**draft PR** once the offline and local DB checks pass; it is marked ready only after deploy, invoke and
the remote eval are measured in the new AWS account (user decision, 2026-11-25).

## Context

v0 runs locally (FastAPI + Compose + local Postgres). Roadmap step 2 runs the same image on AgentCore
Runtime, with filings data in Aurora Serverless v2 reached through the RDS Data API (no VPC in the
request path, no NAT), secrets in Secrets Manager, and a startup reachability check to Supabase. The
agent itself does not change. The new AWS account does not exist yet, so everything up to `make deploy`
is built and tested now; R12–R17 deploy, R10 and R19 measurements wait for it.

## Files that change

- `src/app/api.py` (`/invocations`, `/ping` contract, startup Supabase check), `config.py` (new settings,
  secret overlay), `store.py` (`Conn` protocol, `connect()` picks driver, paged `db_page_store`),
  `retrieve.py`, `validate.py` callers (type hints only), `ingest.py` (`apply_schema` per statement),
  `eval_v0.py` (answerer choice, `--runtime-arn`)
- New: `src/app/dataapi.py` (`DataApiConnection`), `src/app/db_copy.py`, `src/app/supabase.py`,
  `src/app/runtime_client.py` (`RuntimeAnswerer`, `make invoke`)
- New: `infra/` (`main.py`, `research_stack.py`, `cdk.json`, `package.json` + `package-lock.json` pinning the CDK CLI,
  `README.md` with teardown)
- `Dockerfile` (port 8080, `--no-default-groups`), `compose.yaml` (pg18, new volume, 8000→8080),
  `.dockerignore` (+ `infra`, `cdk.out`, `tests*`), `pyproject.toml` + `uv.lock` (`fetch` and `infra` groups,
  `default-groups`), `Makefile`, `.env.example`, `README.md`
- Tests: `tests/test_dataapi.py`, `test_config.py`, `test_api.py`, `test_supabase.py`, `test_eval_v0.py`,
  `tests/test_infra.py`; `tests_db/conftest.py`, `tests_db/test_db_copy.py`

## Order of work

1. **Dependency groups (R2).** Move `edgartools` to a `fetch` group; add `infra` group (`aws-cdk-lib`,
   `constructs`, latest stable, checked for Python 3.14 on PyPI). `[tool.uv] default-groups = ["dev",
   "fetch", "infra"]`, so local `uv sync` and CI are unchanged. Dockerfile uses `uv sync --frozen
   --no-default-groups`. edgartools is already imported lazily (`app/edgar.py:110`), so the API never
   imports it. Check: image builds, `docker run … python -c "import edgar"` fails, image size recorded.
2. **Container (R1, R3, R4).** Uvicorn on `0.0.0.0:8080`; Compose maps `127.0.0.1:8000:8080`. Factor the
   `/research` body into one handler used by `/research` and `POST /invocations` (same errors).
   `/ping` returns `{"status": "Healthy"}` (`test_ping` updated: the spec changes the contract; the
   question page does not call `/ping`).
3. **Settings and secret overlay (R5, R6).** New typed settings `app_secret_id`, `db_cluster_arn`,
   `db_secret_arn`, `db_name`, `supabase_database_url`; added empty to `.env.example`. `get_settings()`:
   when `app_secret_id` is set, overlay `OPENROUTER_API_KEY` and `SUPABASE_DATABASE_URL` from one JSON
   secret (boto3 `secretsmanager`, cached once per process). Secrets as `SecretStr` where new, never
   logged. Tests with a fake client: overlay applied, not called when unset, cached, bad JSON fails with a
   message that carries no value.
4. **Data API adapter (R7, R8).** `dataapi.DataApiConnection`: `execute(sql, params)` returns an object
   with `fetchone()`/`fetchall()`; `transaction()` no-op; `rollback()` no-op; context manager.
   - Rewrites `%s` to `:p1…:pn`; a placeholder followed by `::type` becomes `CAST(:pN AS type)` (avoids
     the Data API parsing `::` after a named parameter).
   - Params: str→stringValue, int→longValue, float→doubleValue, None→isNull, date→stringValue + `DATE`
     typeHint. Lists are rejected (Data API has no arrays).
   - Results use `includeResultMetadata`; `date` columns parse to `datetime.date`, so `DbSources` and
     `RetrievedPage` get the same types as from psycopg.
   - `DatabaseResumingException` retried with backoff up to 60 s total (injected clock/sleep in tests),
     then `DatabaseUnavailable` → 503. boto3 `Config(read_timeout=70, retries standard)`.
   - `store.connect()` returns it when `db_cluster_arn` is set. A `Conn` protocol in `store.py` replaces
     the `psycopg.Connection` hints in `retrieve.py`, `store.py`, `eval_v0.py`; SQL unchanged.
   - `db_page_store` reads one filing at a time (each well under the 1 MiB response cap), for both drivers.
5. **Schema and data copy (R9).** `apply_schema` runs `schema.sql` one statement at a time (works on both
   drivers; the file has no `;` inside bodies or comments, asserted by a test). `app.db_copy`
   (`make db-copy [ARGS=--replace]`): apply schema, refuse a non-empty target unless `--replace`
   (then `TRUNCATE filings CASCADE`), add the `embedding vector(<dim>)` column with
   `chunks.ensure_embedding_column`, copy filings → pages → window chunks in batches of 200 via a
   `BatchWriter` (`BatchExecuteStatement`; psycopg `executemany` in tests). Chunk `id`s are kept (search
   ties break on `id`) and the sequence is reset. `items_started` goes as text with `CAST(… AS text[])`,
   embeddings as `embedding::text` → `CAST(… AS vector)`. Ends by comparing counts.
6. **Local Postgres 18 (R10a).** `compose.yaml` → `pgvector/pgvector:pg18`, new volume `pgdata18` mounted at
   `/var/lib/postgresql` (the pg18 image's layout). Move data once: `pg_dump -Fc` from the pg17 container
   into `data/`, start 18, `pg_restore`. The old `pgdata` volume is kept until the user says to remove it.
   Re-check `make test-db` and `make eval-retrieval` (window hybrid recall@10 0.863 expected).
7. **Supabase check (R11).** `supabase.check(url)` in a background thread at app startup (FastAPI lifespan):
   psycopg, `sslmode=require`, `connect_timeout=10`, `SELECT 1`; logs `supabase reachable seconds=…` or
   `supabase unreachable error=<type>`; never raises. Tests with a fake connect.
8. **Remote eval and invoke (R19, R16).** `RuntimeAnswerer` calls `bedrock-agentcore`
   `invoke_agent_runtime` (one session ID per run, ≥33 chars), parses the body as `Note`, maps a
   `ClientError` to an errored item. `eval_v0 --runtime-arn` swaps the in-process agent for it; scoring and
   the page store (local DB) are shared. `python -m app.runtime_client --runtime-arn … TICKER "question"`
   backs `make invoke`. Tests with a fake client.
9. **CDK stack (R12–R15, R17).** `infra/stack.py` as in spec R12: VPC (2 isolated subnets, no NAT/IGW),
   Aurora PostgreSQL 18.6 serverless v2 (0–4 ACU, auto-pause 5 min, Data API, managed secret, encrypted,
   no-inbound SG, `RemovalPolicy.DESTROY`, no final snapshot), `DockerImageAsset` (`linux/arm64`), empty app
   secret, log group (30 days), execution role with the R13 statements only, AgentCore Runtime (V2, PUBLIC,
   HTTP, IAM auth, 5 min idle, env vars). Uses the `aws_bedrockagentcore` L2 `Runtime` if `aws-cdk-lib`
   ships it with the platform-version property; otherwise `CfnRuntime` (checked in the installed version,
   recorded under departures). Tags `owner` (context/env) and `project`. `tests/test_infra.py` synthesises
   with `aws_cdk.assertions` (no AWS calls) and asserts: no NAT gateway, min 0 ACU, Data API on, no public
   DB, role statements and no `*`/`*`, tags, IAM auth, PUBLIC network, V2.
10. **Make targets and docs (R16, R17).** `deploy`, `destroy`, `invoke`, `db-copy`, all with
    `AWS_PROFILE=sandbox AWS_REGION=eu-central-1`; CDK CLI from `infra/package-lock.json` (`npm ci`).
    `infra/README.md`: deploy steps (bootstrap, set the secret with the AWS CLI, `db-copy`), teardown list.
    README updated with measured results only.
11. **Checks, draft PR.** `make check`, `make typecheck`, `make test`, `make test-db`, verifier subagent;
    ask, then push and open a draft PR.
12. **When the account exists (asks before each AWS write).** Read-only check of Aurora 18.6 / 0 ACU in
    eu-central-1 (concern 2); `cdk bootstrap`; `make deploy`; set the secret; `make db-copy`;
    `make golden-check ARGS=--db` against Aurora (R10); `make invoke` (cold and warm latency, Supabase log
    line); `make eval-v0 ARGS="--runtime-arn …"` (R19); confirm Aurora pauses. Record results in
    `PROJECT_CONTEXT.md`, mark the PR ready.

## Risks

- **Data API type and syntax differences (concern 6)** are the riskiest part: `::` casts, tsquery text,
  dates. Guard: the `tests_db` retrieval and store tests also run through `DataApiConnection` on a fake
  `rds-data` client that executes on local psycopg (rewritten SQL and Data API-shaped records), then the
  deployed golden-check and eval.
- **CDK construct shape:** the L2 `Runtime` or its V2 property may be missing in the installed
  `aws-cdk-lib`; fallback is the L1 resource.
- **pg17 → pg18 move** touches the only copy of the embedded data; the old volume and a dump file stay
  until verified.
- **Synth test speed:** jsii start adds seconds to `make test`; acceptable.
- Concerns 2, 4, 5, 10 stay open until deploy.

## Options not taken

- Separate PRs for code and deploy: user chose one PR, held as a draft.
- A real-Aurora test run of `tests_db`: needs the account and costs; the fake-over-local-Postgres client
  covers the SQL rewriting offline.
- Re-embedding into Aurora: copy keeps the measured vectors (spec R9).

## Proof

| Requirement | Shown by |
| --- | --- |
| R1, R2 | image build on ARM64, `import edgar` fails in the image, Compose question answered on :8000 |
| R3, R4 | `tests/test_api.py`: `/invocations` matches `/research` incl. 422/502/503/500; `/ping` body |
| R5, R6 | `tests/test_config.py` with fake Secrets Manager; `.env.example` setting test |
| R7, R8 | `tests/test_dataapi.py` (rewrites, types, resume retry, 60 s give-up → 503); `tests_db` in both modes |
| R9 | `tests_db/test_db_copy.py`: copies all rows between two schemas, refuses a non-empty target |
| R10, R19 | after deploy: golden-check problems=0 on Aurora; remote eval meets targets |
| R10a | `make test-db` and `make eval-retrieval` on pg18 |
| R11 | `tests/test_supabase.py`: both log lines, never raises |
| R12–R15, R17 | `tests/test_infra.py` synth assertions; teardown section reviewed |
| R16 | Make targets pinned to sandbox / eu-central-1; deploy and destroy asked before running |
| R18, R20 | log lines reviewed; `make test` offline |

## Departures (recorded during the build)

- **Role statements from the Runtime construct (R13).** `agentcore.Runtime` adds its own statements to the
  execution role: ECR pull from the bootstrap asset repository, log groups under
  `/aws/bedrock-agentcore/runtimes/*`, X-Ray and `cloudwatch:PutMetricData`, and workload access tokens.
  X-Ray and `PutMetricData` accept only resource `*`, as do `ecr:GetAuthorizationToken` and
  `logs:DescribeLogGroups` (on `log-group:*`); their actions are specific, so no statement has
  `*` on both action and resource (S4), which `tests/test_infra.py` asserts. The stack's own statements
  are secrets (app + cluster) and the two `rds-data` actions only.
- **App secret placeholder.** CDK's `Secret` cannot be created without a value, so CloudFormation fills it
  with a random `{"placeholder": …}`. The user sets the real JSON with the AWS CLI (infra/README.md). No
  secret value is in CDK, synth output or Git.
- **No custom resource for the default security group.** `restrict_default_security_group=False`, so the
  stack has no Lambda; nothing uses the default group.
- **`.dockerignore` is an allow-list** (`pyproject.toml`, `uv.lock`, `README.md`, `src`): the CDK asset
  staging otherwise copied local-only files such as `PROJECT_CONTEXT.md` into `infra/cdk.out`.
- **mypy skips `aws_cdk`, `constructs`, `jsii`** (`follow_imports = "skip"`): analysing aws-cdk-lib was
  killed for memory in the dev container. The stack is checked by the synth test instead.
- **Extra Make targets:** `synth`, `bootstrap`, `outputs`, `golden-check-aurora` (R10 against Aurora).
  Deploy outputs go to the git-ignored `infra/cdk-outputs.json`, which `invoke`, `db-copy` and
  `golden-check-aurora` read.
- **Eval cost totals** are summed from each note's `run` metadata (works for remote answers); tokens of
  items that errored are no longer counted.
- **Files:** also changed `src/app/chunks.py` (`Conn` hints, `connect_pg`), `.gitignore`,
  `tests_db/test_retrieval.py` (both-modes test); the remote eval tests are in
  `tests/test_runtime_client.py`, not `tests/test_eval_v0.py`; `src/app/validate.py` needed no change.
- **Both-modes DB tests** compare results through `DataApiConnection` (fake client on local Postgres)
  with psycopg in one test, rather than parametrising every `tests_db` test.
- **Deployed models come from the local settings (PR review).** `infra/main.py` reads `MODEL_ID` and
  `EMBEDDING_MODEL_ID` from `.env.local` or the environment instead of hard-coded defaults, and
  `make db-copy` refuses chunks embedded with a model other than `EMBEDDING_MODEL_ID`, so the runtime
  always embeds queries with the chunks' model.
