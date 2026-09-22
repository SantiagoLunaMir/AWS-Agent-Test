#!/usr/bin/env python3
import os

import aws_cdk as cdk

from taller_stack import TallerStack

app = cdk.App()
TallerStack(
    app, "TallerAgente",
    env=cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
                        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-2")),
    description="Demo AWS User Group: agente de citas para un taller mecánico (Bedrock + Lambda + DynamoDB)",
)
cdk.Tags.of(app).add("proyecto", "aws-agent-test")
app.synth()
