# -*- coding: utf-8 -*-
"""Bank Negara Malaysia OpenAPI (free, no key, ~60 requests/minute): OPR decisions and daily FX."""
from datetime import date

import pandas as pd

from data.providers.base import http_get

API = "https://api.bnm.gov.my/public"
HEADERS = {"Accept": "application/vnd.BNM.API.v1+json"}


def get_series(spec: dict, start: str) -> pd.DataFrame:
    """spec endpoint "opr": OPR level at each decision; "fx": MYR per unit of spec currency (noon middle rate)."""
    first, today = int(start[:4]), date.today()
    if spec["endpoint"] == "opr":
        rows = [r for y in range(first, today.year + 1) for r in http_get(f"{API}/opr/year/{y}", headers=HEADERS).json()["data"]]
        return parse_opr(rows)
    rows = []
    for y in range(first, today.year + 1):
        for m in range(1, 13 if y < today.year else today.month + 1):
            url = f"{API}/exchange-rate/{spec['currency']}/year/{y}/month/{m}"
            rows.extend(http_get(url, params={"session": "1200", "quote": "rm"}, headers=HEADERS).json()["data"]["rate"])
    return parse_fx(rows)


def parse_opr(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "new_opr_level"]).drop_duplicates("date")
    return df.rename(columns={"new_opr_level": "value"}).sort_values("date").reset_index(drop=True)


def parse_fx(rows: list) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "middle_rate"]).dropna()
    return df.rename(columns={"middle_rate": "value"}).drop_duplicates("date").sort_values("date").reset_index(drop=True)
