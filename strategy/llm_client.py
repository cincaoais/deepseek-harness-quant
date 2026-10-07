# -*- coding: utf-8 -*-
"""OpenAI-compatible chat completions (DeepSeek, OpenAI, z.ai GLM, ...): one request, one reply.

Settings come from config/market_personas.yaml → llm: base_url, model, api_key_env, max_tokens,
temperature, timeout_s. The key is looked up like every other credential (data/providers/base.py).
"""
from data.providers.base import ProviderError, get_credential, http_post


def create_completion(settings: dict, messages: list) -> str:
    env = settings.get("api_key_env", "DEEPSEEK_API_KEY")
    key = get_credential(env)
    if not key:
        raise ProviderError(f"{env} is not set (environment, config/params.yaml or harness/home/.credentials.yaml)")
    body = {"model": settings["model"], "messages": messages, "stream": False,
            "max_tokens": settings.get("max_tokens", 4000), "temperature": settings.get("temperature", 0.7)}
    resp = http_post(settings["base_url"].rstrip("/") + "/chat/completions", body,
                     headers={"Authorization": f"Bearer {key}"}, timeout=settings.get("timeout_s", 180), retries=2)
    return resp.json()["choices"][0]["message"].get("content") or ""
