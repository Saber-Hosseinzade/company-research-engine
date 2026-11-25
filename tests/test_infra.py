"""Synth test for the AgentCore stack (AgentCore spec R12-R15, R17): no AWS calls, no Docker build."""

import json
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template
from research_stack import ResearchStack


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    app = cdk.App()
    stack = ResearchStack(
        app,
        "Test",
        owner="test-owner",
        model_id="anthropic/claude-haiku-5.5",
        embedding_model_id="amazon/titan-embed-text-v2",
        env=cdk.Environment(account="111111111111", region="eu-central-1"),
    )
    result: dict[str, Any] = Template.from_stack(stack).to_json()
    return result


def resources(template: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [r for r in template["Resources"].values() if r["Type"] == kind]


def only(template: dict[str, Any], kind: str) -> dict[str, Any]:
    (found,) = resources(template, kind)
    return found


def statements(template: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for policy in resources(template, "AWS::IAM::Policy"):
        out.extend(policy["Properties"]["PolicyDocument"]["Statement"])
    return out


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def test_no_nat_or_internet_gateway_and_isolated_subnets(template: dict[str, Any]) -> None:
    assert resources(template, "AWS::EC2::NatGateway") == []
    assert resources(template, "AWS::EC2::InternetGateway") == []
    subnets = resources(template, "AWS::EC2::Subnet")
    assert len(subnets) == 2
    assert all(not s["Properties"].get("MapPublicIpOnLaunch") for s in subnets)


def test_aurora_scales_to_zero_with_data_api_and_no_public_endpoint(template: dict[str, Any]) -> None:
    cluster = only(template, "AWS::RDS::DBCluster")
    props = cluster["Properties"]
    assert props["Engine"] == "aurora-postgresql"
    assert props["EngineVersion"] == "18.6"
    assert props["ServerlessV2ScalingConfiguration"] == {
        "MinCapacity": 0,
        "MaxCapacity": 4,
        "SecondsUntilAutoPause": 300,
    }
    assert props["EnableHttpEndpoint"] is True
    assert props["StorageEncrypted"] is True
    assert cluster["DeletionPolicy"] == "Delete"  # no final snapshot (R17)
    instance = only(template, "AWS::RDS::DBInstance")
    assert instance["Properties"]["DBInstanceClass"] == "db.serverless"
    assert instance["Properties"].get("PubliclyAccessible") is not True
    for group in resources(template, "AWS::EC2::SecurityGroup"):
        assert "SecurityGroupIngress" not in group["Properties"]
    assert resources(template, "AWS::EC2::SecurityGroupIngress") == []


def test_runtime_is_v2_public_http_iam_with_short_idle(template: dict[str, Any]) -> None:
    props = only(template, "AWS::BedrockAgentCore::Runtime")["Properties"]
    assert props["PlatformVersion"] == "V2"
    assert props["NetworkConfiguration"] == {"NetworkMode": "PUBLIC"}
    assert props["ProtocolConfiguration"] == "HTTP"
    assert "AuthorizerConfiguration" not in props  # none means IAM (SigV4) only (R15)
    assert props["LifecycleConfiguration"]["IdleRuntimeSessionTimeout"] == 300
    assert set(props["EnvironmentVariables"]) == {
        "APP_SECRET_ID",
        "DB_CLUSTER_ARN",
        "DB_SECRET_ARN",
        "DB_NAME",
        "MODEL_PROVIDER",
        "MODEL_ID",
        "EMBEDDING_MODEL_ID",
    }
    assert props["EnvironmentVariables"]["MODEL_PROVIDER"] == "openrouter"


def test_no_resource_policy_endpoint_or_public_url(template: dict[str, Any]) -> None:
    kinds = {r["Type"] for r in template["Resources"].values()}
    assert not {k for k in kinds if "ResourcePolicy" in k or "Endpoint" in k or k.startswith("AWS::Lambda")}


def test_role_has_only_scoped_statements(template: dict[str, Any]) -> None:
    found = statements(template)
    for statement in found:
        actions, targets = as_list(statement["Action"]), as_list(statement["Resource"])
        assert not ("*" in actions and "*" in targets), statement  # S4
        assert all(not a.endswith(":*") for a in actions), statement
    by_action = {a: s for s in found for a in as_list(s["Action"])}
    assert len(as_list(by_action["secretsmanager:GetSecretValue"]["Resource"])) == 2  # app + cluster
    rds_actions = {a for a in by_action if a.startswith("rds-data:")}
    assert rds_actions == {"rds-data:ExecuteStatement", "rds-data:BatchExecuteStatement"}
    assert "ecr:BatchGetImage" in by_action
    assert {a.split(":")[0] for a in by_action} <= {
        "secretsmanager",
        "rds-data",
        "ecr",
        "logs",
        "xray",
        "cloudwatch",
        "bedrock-agentcore",
    }


def test_trust_is_agentcore_in_this_account(template: dict[str, Any]) -> None:
    role = only(template, "AWS::IAM::Role")["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]
    assert role["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}
    assert role["Condition"]["StringEquals"] == {"aws:SourceAccount": "111111111111"}


def test_secret_has_no_real_value_in_the_template(template: dict[str, Any]) -> None:
    secret = [
        s
        for s in resources(template, "AWS::SecretsManager::Secret")
        if s["Properties"].get("Name") == "company-research-engine/app"
    ]
    assert len(secret) == 1
    assert "SecretString" not in secret[0]["Properties"]
    assert "OPENROUTER" not in json.dumps(secret[0]["Properties"]["GenerateSecretString"])


def test_logs_kept_30_days(template: dict[str, Any]) -> None:
    group = only(template, "AWS::Logs::LogGroup")
    assert group["Properties"]["RetentionInDays"] == 30
    assert group["DeletionPolicy"] == "Delete"


def test_every_taggable_resource_has_owner_and_project(template: dict[str, Any]) -> None:
    checked = 0
    for resource in template["Resources"].values():
        tags = resource["Properties"].get("Tags")
        if tags is None:
            continue
        pairs = tags if isinstance(tags, dict) else {t["Key"]: t["Value"] for t in tags}
        assert pairs.get("owner") == "test-owner", resource["Type"]
        assert pairs.get("project") == "company-research-engine", resource["Type"]
        checked += 1
    assert checked >= 8
