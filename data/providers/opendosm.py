# -*- coding: utf-8 -*-
"""OpenDOSM data catalogue (api.data.gov.my, free, no key): Malaysian official statistics."""
import pandas as pd

from data.providers.base import http_get

API = "https://api.data.gov.my/data-catalogue/"


def get_series(spec: dict, start: str) -> pd.DataFrame:
    """Rows of dataset spec["dataset"] matching spec["filter"]; spec["value"] is the value column."""
    rows = http_get(API, params={"id": spec["dataset"]}).json()
    return parse_rows(rows, spec.get("filter") or {}, spec["value"], start)


def parse_rows(rows: list, match: dict, value: str, start: str) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for column, wanted in match.items():
        df = df[df[column].astype(str) == str(wanted)]
    df = df[df["date"] >= start][["date", value]].rename(columns={value: "value"})
    return df.sort_values("date").reset_index(drop=True)
