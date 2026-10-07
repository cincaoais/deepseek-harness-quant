# -*- coding: utf-8 -*-
"""Credentials and HTTP helpers shared by the provider adapters."""
import os
import time
from pathlib import Path

import requests
import yaml

BASE = Path(__file__).resolve().parent.parent.parent
PARAMS_PATH = BASE / "config" / "params.yaml"
HARNESS_CREDENTIALS_PATH = BASE / "harness" / "home" / ".credentials.yaml"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    def __init__(self, message: str, status: int = None):
        super().__init__(message)
        self.status = status


def get_credential(name: str):
    """Environment variable, then config/params.yaml → data.api_keys.<name>, then the HARNESS
    credentials file (harness/home/.credentials.yaml, where 接入API.cmd writes keys); None if unset."""
    value = os.environ.get(name)
    if value:
        return value.strip()
    candidates = []
    if PARAMS_PATH.exists():
        cfg = yaml.safe_load(PARAMS_PATH.read_text(encoding="utf-8")) or {}
        candidates.append(((cfg.get("data") or {}).get("api_keys") or {}).get(name))
    if HARNESS_CREDENTIALS_PATH.exists():
        candidates.append((yaml.safe_load(HARNESS_CREDENTIALS_PATH.read_text(encoding="utf-8")) or {}).get(name))
    # the shipped templates use "<your-config>" style placeholders
    value = next((v for v in candidates if v and not str(v).startswith("<")), None)
    return str(value).strip() if value else None


def http_get(url: str, params=None, headers=None, timeout=30, retries=3) -> requests.Response:
    return http_request("GET", url, params=params, headers=headers, timeout=timeout, retries=retries)


def http_post(url: str, body: dict, headers=None, timeout=30, retries=3) -> requests.Response:
    return http_request("POST", url, body=body, headers=headers, timeout=timeout, retries=retries)


def http_request(method: str, url: str, params=None, body=None, headers=None, timeout=30, retries=3) -> requests.Response:
    """Request with retry on throttling and server errors; other failures raise ProviderError."""
    error = None
    for attempt in range(retries):
        try:
            resp = requests.request(method, url, params=params, json=body, headers=headers, timeout=timeout)
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
