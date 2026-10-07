# -*- coding: utf-8 -*-
"""FRED macro series through the keyless CSV endpoint (latest vintage only)."""
import io

import pandas as pd

from data.providers.base import http_get

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def get_series(spec: dict, start: str) -> pd.DataFrame:
    """Full history of spec["id"]; `start` is applied by the caller."""
    return parse_series_csv(http_get(CSV_URL, params={"id": spec["id"]}).text)


def parse_series_csv(text: str) -> pd.DataFrame:
    """date/value rows; FRED leaves missing observations blank (older files use '.')."""
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = ["date", "value"]
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"]).reset_index(drop=True)
