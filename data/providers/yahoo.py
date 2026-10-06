# -*- coding: utf-8 -*-
"""Yahoo Finance via yfinance: free, unofficial, personal use; delisted tickers are missing."""
import pandas as pd

COLUMNS = {"Open": "open", "High": "high", "Low": "low", "Close": "close",
           "Adj Close": "adj_close", "Volume": "volume"}
ACTION_COLUMNS = {"Dividends": "dividend", "Stock Splits": "split"}
SCREEN_PAGE = 250


def list_daily_bars(symbols, start: str, end: str = None) -> dict:
    """Bars per Yahoo symbol from `start` (inclusive) to `end` (exclusive, default today)."""
    import yfinance as yf

    symbols = list(symbols)
    raw = yf.download(symbols, start=start, end=end, auto_adjust=False, actions=True,
                      group_by="ticker", threads=True, progress=False)
    frames = {s: parse_download(raw, s) for s in symbols}
    return {s: f for s, f in frames.items() if f is not None}


def parse_download(raw: pd.DataFrame, symbol: str):
    """One symbol's bars (plus dividend/split columns when present); None when absent or empty."""
    if raw is None or raw.empty or symbol not in raw.columns.get_level_values(0):
        return None
    frame = raw[symbol].dropna(subset=["Close"])
    if frame.empty:
        return None
    names = {**COLUMNS, **{k: v for k, v in ACTION_COLUMNS.items() if k in frame.columns}}
    out = frame[list(names)].rename(columns=names)
    out.insert(0, "date", frame.index.strftime("%Y-%m-%d"))
    return out.reset_index(drop=True)


def list_symbols(exchange: str) -> pd.DataFrame:
    """Current listings on a Yahoo exchange code (e.g. KLS for Bursa) via the Yahoo screener."""
    import yfinance as yf

    query = yf.EquityQuery("eq", ["exchange", exchange])
    quotes, offset = [], 0
    while True:
        page = yf.screen(query, offset=offset, size=SCREEN_PAGE, sortField="ticker", sortAsc=True)
        quotes.extend(page.get("quotes") or [])
        offset += SCREEN_PAGE
        if offset >= (page.get("total") or 0):
            break
    return parse_screen(quotes, exchange)


def parse_screen(quotes: list, exchange: str) -> pd.DataFrame:
    rows = [{"code": q["symbol"], "name": q.get("longName") or q.get("shortName"),
             "exchange": exchange, "is_etf": q.get("quoteType") == "ETF", "is_active": True}
            for q in quotes if q.get("symbol")]
    return pd.DataFrame(rows, columns=["code", "name", "exchange", "is_etf", "is_active"]).drop_duplicates("code")


def get_shares_history(symbol: str, start: str) -> pd.DataFrame:
    """Share counts as Yahoo recorded them (not split-adjusted): date, value."""
    import yfinance as yf

    series = yf.Ticker(symbol).get_shares_full(start=start)
    if series is None or series.empty:
        return pd.DataFrame(columns=["date", "value"])
    frame = pd.DataFrame({"date": series.index.strftime("%Y-%m-%d"), "value": series.values})
    return frame.drop_duplicates("date", keep="last").reset_index(drop=True)
