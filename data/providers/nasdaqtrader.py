# -*- coding: utf-8 -*-
"""US symbol master from Nasdaq Trader's daily directory files (free; current listings only)."""
import io

import pandas as pd

from data.providers.base import http_get

LISTINGS = {
    "nasdaqlisted": ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "Symbol"),
    "otherlisted": ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "ACT Symbol"),
}
# exchange codes used in otherlisted.txt
OTHER_EXCHANGES = {"A": "NYSE American", "N": "NYSE", "P": "NYSE Arca", "Z": "Cboe BZX", "V": "IEX"}


def list_symbols() -> pd.DataFrame:
    frames = [parse_listing(http_get(url).text, kind) for kind, (url, _) in LISTINGS.items()]
    return pd.concat(frames, ignore_index=True).drop_duplicates("code")


def parse_listing(text: str, kind: str) -> pd.DataFrame:
    """Rows of one directory file, without test issues, the footer line or $-suffixed preferreds."""
    symbol_col = LISTINGS[kind][1]
    df = pd.read_csv(io.StringIO(text), sep="|", dtype=str, keep_default_na=False)
    df = df[~df[symbol_col].str.startswith("File Creation Time") & df["Test Issue"].eq("N")]
    df = df[~df[symbol_col].str.contains(r"[$^=]", regex=True)]
    exchange = "NASDAQ" if kind == "nasdaqlisted" else df["Exchange"].map(OTHER_EXCHANGES)
    return pd.DataFrame({
        "code": df[symbol_col],
        "name": df["Security Name"].str.strip(),
        "exchange": exchange,
        "is_etf": df["ETF"].eq("Y"),
        "is_active": True,
    })
