"""CDK app: `make deploy` / `make destroy` (AgentCore spec R16). Profile sandbox, eu-central-1 only."""

import os

import aws_cdk as cdk
from research_stack import ResearchStack

REGION = "eu-central-1"

app = cdk.App()
owner = app.node.try_get_context("owner") or os.environ.get("OWNER")
if not owner:
    raise SystemExit("Set the owner tag: OWNER=<name> make deploy (or -c owner=<name>).")
ResearchStack(
    app,
    "CompanyResearchEngine",
    owner=owner,
    model_id=app.node.try_get_context("model_id") or "anthropic/claude-haiku-5.5",
    embedding_model_id=app.node.try_get_context("embedding_model_id") or "amazon/titan-embed-text-v2",
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=REGION),
)
app.synth()
