from functools import lru_cache

from anthropic import AnthropicBedrock

from . import config


@lru_cache(maxsize=1)
def cliente() -> AnthropicBedrock:
    return AnthropicBedrock(aws_region=config.REGION, max_retries=3, timeout=120.0)
