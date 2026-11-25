# infra: the filings agent on AgentCore Runtime

One CDK stack (`research_stack.py`, Python) in the `sandbox` profile's account, region `eu-central-1`:

- an AgentCore Runtime (platform V2, public network, HTTP, IAM/SigV4 callers only) running the same
  image as local Compose;
- Aurora PostgreSQL 18.6 Serverless v2 (0–4 ACU, pauses after 5 minutes idle) with the RDS Data API,
  in a VPC with two isolated subnets and no NAT or internet gateway;
- the app secret (`company-research-engine/app`), the runtime's execution role and a log group kept
  30 days.

The CDK CLI version is pinned in `package.json` / `package-lock.json` and runs on Node 24. The Python
packages (`aws-cdk-lib`, `constructs`) are in the `infra` dependency group. `tests/test_infra.py`
checks the synthesised template offline.

## Deploy

Each of these changes the AWS account; run them only when you mean to.

1. Log in: `make sso-login`.
2. Once per account: `make bootstrap`.
3. `OWNER=<your name> make deploy`. The outputs (runtime ARN, cluster ARN, secret ARNs) go to
   `infra/cdk-outputs.json`, which is git-ignored.
4. Set the app secret. Write the JSON to a file outside the repo, then:

   ```sh
   aws secretsmanager put-secret-value --profile sandbox --region eu-central-1 \
     --secret-id company-research-engine/app --secret-string file:///path/to/app-secret.json
   ```

   The JSON has two keys: `{"OPENROUTER_API_KEY": "...", "SUPABASE_DATABASE_URL": "postgresql://..."}`.
   A runtime session started before this keeps the old value; new sessions read it at start.
5. Copy the data: `make db-copy` (local Postgres must hold the embedded chunks: `make db-up`). Then
   `make golden-check-aurora` should report `problems=0`.
6. Ask one question: `make invoke` (or `TICKER=AMD Q="..." make invoke`).
7. Score it: `make eval-v0 ARGS="--runtime-arn $(python3 -c "import json;print(json.load(open('infra/cdk-outputs.json'))['CompanyResearchEngine']['RuntimeArn'])")"`.

The first request after Aurora has paused waits for it to resume (about 15 s, 30 s or more after a
day paused); the app retries for up to 60 s, then answers 503.

## Teardown

`make destroy` deletes the stack: the runtime, the Aurora cluster **without a final snapshot** (the
data is rebuilt with `make fetch ingest chunk embed db-copy`), the VPC, the execution role and the
log group. These stay:

| What | Why | How to remove |
| --- | --- | --- |
| App secret and the cluster's credential secret | Secrets Manager keeps a deleted secret for its recovery window (7–30 days), and it can't be recreated with the same name meanwhile | `aws secretsmanager delete-secret --secret-id <arn> --force-delete-without-recovery --profile sandbox --region eu-central-1` |
| CDK bootstrap stack `CDKToolkit`, its S3 bucket and ECR repository (holds the pushed images) | Shared by every CDK app in the account | Empty the bucket and the repository, then `aws cloudformation delete-stack --stack-name CDKToolkit --profile sandbox --region eu-central-1` |
| Log groups the service created outside the stack (if any) | Created by AgentCore on first write | `aws logs delete-log-group --log-group-name <name> --profile sandbox --region eu-central-1` |
