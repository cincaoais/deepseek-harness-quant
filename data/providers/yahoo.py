# -*- coding: utf-8 -*-
"""Yahoo Finance daily bars via yfinance: free, unofficial, personal use; delisted tickers are missing."""
import pandas as pd

COLUMNS = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
           "Adj Close": "adj_close", "Volume": "volume"}


def list_daily_bars(symbols, start: str, end: str = None) -> dict:
    """Bars per Yahoo symbol from `start` (inclusive) to `end` (exclusive, default today)."""
    import yfinance as yf

    symbols = list(symbols)
    raw = yf.download(symbols, start=start, end=end, auto_adjust=False, actions=False,
                      group_by="ticker", threads=True, progress=False)
    frames = {s: parse_download(raw, s) for s in symbols}
    return {s: f for s, f in frames.items() if f is not None}


def parse_download(raw: pd.DataFrame, symbol: str):
    """One symbol's bars out of a group_by="ticker" yf.download frame; None when absent or empty."""
    if raw is None or raw.empty or symbol not in raw.columns.get_level_values(0):
        return None
    frame = raw[symbol].dropna(subset=["Close"])
    if frame.empty:
        return None
    out = frame[list(COLUMNS)].rename(columns=COLUMNS)
    out.insert(0, "date", frame.index.strftime("%Y-%m-%d"))
    return out.reset_index(drop=True)
