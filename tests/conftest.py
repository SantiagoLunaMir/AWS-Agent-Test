import os
import sys
from pathlib import Path

import boto3
import pytest

os.environ.update({
    "AWS_REGION": "us-east-2",
    "AWS_DEFAULT_REGION": "us-east-2",
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "BUCKET_FOTOS": "fotos-test",
})
for var in ("AWS_PROFILE", "AWS_SESSION_TOKEN"):
    os.environ.pop(var, None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from moto import mock_aws  # noqa: E402

from taller import config, store  # noqa: E402


@pytest.fixture
def aws():
    with mock_aws():
        store._tabla.cache_clear()
        ddb = boto3.client("dynamodb", region_name="us-east-2")
        ddb.create_table(TableName=config.TABLA_CONVERSACIONES, BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"}],
                         AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"}])
        ddb.create_table(TableName=config.TABLA_MENSAJES, BillingMode="PAY_PER_REQUEST",
                         KeySchema=[{"AttributeName": "session_id", "KeyType": "HASH"},
                                    {"AttributeName": "sk", "KeyType": "RANGE"}],
                         AttributeDefinitions=[{"AttributeName": "session_id", "AttributeType": "S"},
                                               {"AttributeName": "sk", "AttributeType": "S"}])
        ddb.create_table(
            TableName=config.TABLA_CITAS, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "cita_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"}
                                  for n in ("cita_id", "fecha", "hora", "telefono")],
            GlobalSecondaryIndexes=[
                {"IndexName": "por_fecha", "Projection": {"ProjectionType": "ALL"},
                 "KeySchema": [{"AttributeName": "fecha", "KeyType": "HASH"},
                               {"AttributeName": "hora", "KeyType": "RANGE"}]},
                {"IndexName": "por_telefono", "Projection": {"ProjectionType": "ALL"},
                 "KeySchema": [{"AttributeName": "telefono", "KeyType": "HASH"},
                               {"AttributeName": "fecha", "KeyType": "RANGE"}]},
            ])
        boto3.client("s3", region_name="us-east-2").create_bucket(
            Bucket="fotos-test", CreateBucketConfiguration={"LocationConstraint": "us-east-2"})
        yield
        store._tabla.cache_clear()
