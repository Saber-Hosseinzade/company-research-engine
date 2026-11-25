"""CDK app: `make deploy` / `make destroy` (AgentCore spec R16). Profile sandbox, eu-central-1 only."""

import os
from pathlib import Path

import aws_cdk as cdk
from research_stack import ResearchStack

from app.config import Settings

REGION = "eu-central-1"
# The deployed models are the ones local runs use (.env.local or the environment), so the runtime embeds
# queries with the model the copied chunks were embedded with (db-copy checks the chunks).
local = Settings(_env_file=Path(__file__).resolve().parents[1] / ".env.local")
if local.model_provider != "openrouter" or not local.embedding_model_id:
    raise SystemExit("Set MODEL_PROVIDER=openrouter, MODEL_ID and EMBEDDING_MODEL_ID (as for local runs).")

app = cdk.App()
owner = app.node.try_get_context("owner") or os.environ.get("OWNER")
if not owner:
    raise SystemExit("Set the owner tag: OWNER=<name> make deploy (or -c owner=<name>).")
ResearchStack(
    app,
    "CompanyResearchEngine",
    owner=owner,
    model_id=local.model_id,
    embedding_model_id=local.embedding_model_id,
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=REGION),
)
app.synth()
