"""Cliente de Bedrock Runtime para la API Converse.

Converse tiene el mismo formato para todos los modelos de Bedrock (Nova, Mistral, Qwen, DeepSeek, gpt-oss…):
cambiar de modelo es cambiar MODEL_ID, sin tocar el código.
"""
from functools import lru_cache

import boto3
from botocore.config import Config

from . import config


@lru_cache(maxsize=1)
def cliente():
    return boto3.client("bedrock-runtime", region_name=config.REGION,
                        config=Config(read_timeout=120, retries={"max_attempts": 4, "mode": "adaptive"}))
