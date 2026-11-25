"""The AgentCore deployment (AgentCore spec R12-R15, R17): one stack in the sandbox account, eu-central-1.

Aurora PostgreSQL Serverless v2 (0 ACU when idle) holds the filings; the runtime reaches it only through
the RDS Data API over HTTPS with IAM, so the runtime runs in PUBLIC network mode with no NAT gateway. The
VPC exists only because Aurora needs one; nothing connects into it.
"""

from pathlib import Path
from typing import Any

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack, Tags
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

REPO = Path(__file__).resolve().parents[1]  # the Dockerfile's build context
PROJECT = "company-research-engine"
RUNTIME_NAME = "company_research_engine"
DB_NAME = "app"
LOG_PREFIX = "/aws/bedrock-agentcore/runtimes/"  # where AgentCore writes runtime logs


class ResearchStack(Stack):  # type: ignore[misc]  # aws_cdk is not analysed (pyproject)
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        owner: str,
        model_id: str,
        embedding_model_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("owner", owner)  # from context or the environment, never the repo (R14)
        Tags.of(self).add("project", PROJECT)

        # Aurora only: two isolated subnets, no internet or NAT gateway (R12). The default security group
        # is left as created (no custom-resource Lambda); nothing uses it.
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            restrict_default_security_group=False,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="db", subnet_type=ec2.SubnetType.PRIVATE_ISOLATED, cidr_mask=24)
            ],
        )
        db_sg = ec2.SecurityGroup(
            self, "DbSecurityGroup", vpc=vpc, allow_all_outbound=False, description="Aurora: no inbound rules"
        )
        cluster = rds.DatabaseCluster(
            self,
            "Filings",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=rds.AuroraPostgresEngineVersion.VER_18_6
            ),
            writer=rds.ClusterInstance.serverless_v2("writer"),
            serverless_v2_min_capacity=0,
            serverless_v2_max_capacity=4,
            serverless_v2_auto_pause_duration=Duration.minutes(5),
            enable_data_api=True,
            credentials=rds.Credentials.from_generated_secret("postgres"),
            default_database_name=DB_NAME,
            storage_encrypted=True,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            security_groups=[db_sg],
            deletion_protection=False,
            removal_policy=RemovalPolicy.DESTROY,  # no final snapshot: `make ... db-copy` rebuilds it (R17)
        )
        assert cluster.secret is not None

        # OPENROUTER_API_KEY and SUPABASE_DATABASE_URL. CloudFormation fills it with a random placeholder;
        # the user sets the real JSON with the AWS CLI, so no secret is in CDK, synth output or Git (R12).
        app_secret = secretsmanager.Secret(
            self,
            "AppSecret",
            secret_name=f"{PROJECT}/app",
            description="OPENROUTER_API_KEY and SUPABASE_DATABASE_URL for the runtime (set with the AWS CLI)",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template="{}", generate_string_key="placeholder"
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        role = iam.Role(
            self,
            "RuntimeRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:*"},
                },
            ),
            description="AgentCore Runtime execution role for the filings agent",
        )
        # R13: only what the runtime needs.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[app_secret.secret_arn, cluster.secret.secret_arn],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["rds-data:ExecuteStatement", "rds-data:BatchExecuteStatement"],
                resources=[cluster.cluster_arn],
            )
        )
        # The Runtime construct adds the rest itself: ECR pull from the asset repository, writing to its
        # log groups, X-Ray and CloudWatch metrics (those two accept only resource "*"), workload tokens.

        runtime = agentcore.Runtime(
            self,
            "Runtime",
            runtime_name=RUNTIME_NAME,
            description="company-research-engine filings agent",
            agent_runtime_artifact=agentcore.AgentRuntimeArtifact.from_asset(
                str(REPO), platform=ecr_assets.Platform.LINUX_ARM64
            ),
            execution_role=role,
            platform_version=agentcore.PlatformVersion.V2,  # snapshot starts; V1 is the default
            network_configuration=agentcore.RuntimeNetworkConfiguration.using_public_network(),
            protocol_configuration=agentcore.ProtocolType.HTTP,
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_iam(),
            lifecycle_configuration=agentcore.LifecycleConfiguration(
                idle_runtime_session_timeout=Duration.minutes(5)
            ),
            tracing_enabled=False,  # R18: observability is a later step
            environment_variables={
                "APP_SECRET_ID": app_secret.secret_arn,
                "DB_CLUSTER_ARN": cluster.cluster_arn,
                "DB_SECRET_ARN": cluster.secret.secret_arn,
                "DB_NAME": DB_NAME,
                "MODEL_PROVIDER": "openrouter",
                "MODEL_ID": model_id,
                "EMBEDDING_MODEL_ID": embedding_model_id,
            },
        )
        # Created by the stack (not on first write by the service) so it has a retention and is destroyed.
        logs.LogGroup(
            self,
            "RuntimeLogs",
            log_group_name=f"{LOG_PREFIX}{runtime.agent_runtime_id}-DEFAULT",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )

        CfnOutput(self, "RuntimeArn", value=runtime.agent_runtime_arn)
        CfnOutput(self, "ClusterArn", value=cluster.cluster_arn)
        CfnOutput(self, "ClusterSecretArn", value=cluster.secret.secret_arn)
        CfnOutput(self, "AppSecretArn", value=app_secret.secret_arn)
