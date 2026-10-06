# -*- coding: utf-8 -*-
"""Credentials and HTTP helpers shared by the provider adapters."""
import os
import time
from pathlib import Path

import requests
import yaml

PARAMS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "params.yaml"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int = None):
        super().__init__(message)
        self.status = status


def get_credential(name: str):
    """Environment variable first, then config/params.yaml → data.api_keys.<name>; None if unset."""
    value = os.environ.get(name)
    if value:
        return value.strip()
    if not PARAMS_PATH.exists():
        return None
    cfg = yaml.safe_load(PARAMS_PATH.read_text(encoding="utf-8")) or {}
    value = ((cfg.get("data") or {}).get("api_keys") or {}).get(name)
    # the shipped template uses "<your-config>" placeholders
    if not value or str(value).startswith("<"):
        return None
    return str(value).strip()


def http_get(url: str, params=None, headers=None, timeout=30, retries=3) -> requests.Response:
    """GET with retry on throttling and server errors; other failures raise ProviderError."""
    error = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            error = ProviderError(f"{url}: {exc}")
        else:
            if resp.status_code == 200:
                return resp
            error = ProviderError(f"{url} → HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)
            if resp.status_code not in RETRYABLE_STATUS:
                raise error
        time.sleep(2 ** attempt)
    raise error
