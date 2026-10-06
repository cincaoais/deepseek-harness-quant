# -*- coding: utf-8 -*-
"""EODHD adapter (paid key, includes delisted tickers); inactive until EODHD_API_KEY is set."""
import pandas as pd

from data.providers.base import ProviderError, get_credential, http_get

API = "https://eodhd.com/api"
KEPT_TYPES = {"Common Stock", "ETF"}


def get_token() -> str:
    token = get_credential("EODHD_API_KEY")
    if not token:
        raise ProviderError("EODHD_API_KEY is not set (environment or config/params.yaml → data.api_keys)")
    return token


def list_daily_bars(symbols, start: str, end: str = None) -> dict:
    token = get_token()
    frames = {}
    for symbol in symbols:
        params = {"api_token": token, "fmt": "json", "from": start}
        if end:
            params["to"] = end
        try:
            rows = http_get(f"{API}/eod/{symbol}", params=params).json()
            splits = http_get(f"{API}/splits/{symbol}", params={"api_token": token, "fmt": "json"}).json()
        except ProviderError as exc:
            if exc.status == 404:
                continue
            raise
        frame = parse_eod(rows, splits)
        if frame is not None:
            frames[symbol] = frame
    return frames


def parse_eod(rows: list, splits: list):
    """EODHD prices are raw while volume is split-adjusted; rescale prices to split-adjusted."""
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    factor = pd.Series(1.0, index=df.index)
    for split in splits:
        new, old = (float(x) for x in split["split"].split("/"))
        factor[df["date"] < split["date"]] *= new / old
    for col in ("open", "high", "low", "close"):
        df[col] = df[col] / factor
    df = df.rename(columns={"adjusted_close": "adj_close"})
    return df[["date", "open", "high", "low", "close", "adj_close", "volume"]]


def list_symbols(exchange: str) -> pd.DataFrame:
    """Active and delisted common stocks and ETFs on an EODHD exchange (e.g. US, KLSE)."""
    token = get_token()
    frames = []
    for is_delisted in (False, True):
        params = {"api_token": token, "fmt": "json", "delisted": int(is_delisted)}
        rows = http_get(f"{API}/exchange-symbol-list/{exchange}", params=params).json()
        frames.append(parse_symbol_list(rows, is_active=not is_delisted))
    return pd.concat(frames, ignore_index=True).drop_duplicates("code")


def parse_symbol_list(rows: list, is_active: bool) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["Code", "Name", "Exchange", "Type"])
    df = df[df["Type"].isin(KEPT_TYPES)]
    return pd.DataFrame({
        "code": df["Code"].astype(str),
        "name": df["Name"],
        "exchange": df["Exchange"],
        "is_etf": df["Type"].eq("ETF"),
        "is_active": is_active,
    })
