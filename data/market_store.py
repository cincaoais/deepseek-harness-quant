# -*- coding: utf-8 -*-
"""Local stores for US / Bursa Malaysia data, one directory per market under <cache>/markets/<id>/.

bars.db          DailyCache schema; adjust='qfq' (split+dividend adjusted), 'split' (split-adjusted only)
reference.db     symbol master, macro series, corporate actions (splits, dividends)
fundamentals.db  filed facts (SEC XBRL, read point-in-time with get_fact_asof), share counts
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from data.cache import _SCHEMA as BARS_SCHEMA, DailyCache
from data.markets import Market, get_last_closed_session

BASE = Path(__file__).resolve().parent.parent
REFERENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    code TEXT PRIMARY KEY, name TEXT, exchange TEXT, is_etf INTEGER, is_active INTEGER,
    source TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS macro_series (
    series_id TEXT NOT NULL, date TEXT NOT NULL, value REAL, source TEXT,
    PRIMARY KEY (series_id, date)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    code TEXT NOT NULL, date TEXT NOT NULL, kind TEXT NOT NULL, value REAL, source TEXT,
    PRIMARY KEY (code, date, kind)
);
"""
FUNDAMENTALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    code TEXT NOT NULL, cik INTEGER, concept TEXT NOT NULL, unit TEXT NOT NULL,
    period_start TEXT NOT NULL, period_end TEXT NOT NULL, value REAL,
    fiscal_year INTEGER, fiscal_period TEXT, form TEXT, filed TEXT NOT NULL, accession TEXT NOT NULL,
    PRIMARY KEY (code, concept, unit, period_start, period_end, accession)
);
CREATE INDEX IF NOT EXISTS idx_facts_asof ON facts (code, concept, filed);
CREATE TABLE IF NOT EXISTS fact_updates (code TEXT PRIMARY KEY, cik INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS shares_outstanding (
    code TEXT NOT NULL, date TEXT NOT NULL, value REAL, available_from TEXT NOT NULL, source TEXT NOT NULL,
    PRIMARY KEY (code, date, source)
);
CREATE TABLE IF NOT EXISTS fetch_log (key TEXT PRIMARY KEY, updated_at TEXT);
CREATE TABLE IF NOT EXISTS filers (
    code TEXT PRIMARY KEY, cik INTEGER, loc TEXT, last_period TEXT, ticker_count INTEGER
);
"""
DURATION_DAYS = {"quarter": (80, 100), "annual": (350, 380)}


BAR_COLUMNS = ["open", "high", "low", "close", "preclose", "volume", "amount", "turn", "pct_chg"]


def delete_bars_off_calendar(market: Market, sessions: set) -> int:
    """Remove bars dated on exchange holidays (Yahoo emits flat zero-volume placeholders) and refresh bar_meta."""
    with connect(market, "bars.db", BARS_SCHEMA) as con:
        dates = [r[0] for r in con.execute("SELECT DISTINCT date FROM daily_bar")]
        off = [(d,) for d in dates if d not in sessions]
        if off:
            con.executemany("DELETE FROM daily_bar WHERE date=?", off)
            con.execute("UPDATE bar_meta SET "
                        "rows=(SELECT COUNT(*) FROM daily_bar b WHERE b.code=bar_meta.code AND b.adjust=bar_meta.adjust), "
                        "start_date=(SELECT MIN(date) FROM daily_bar b WHERE b.code=bar_meta.code AND b.adjust=bar_meta.adjust), "
                        "end_date=(SELECT MAX(date) FROM daily_bar b WHERE b.code=bar_meta.code AND b.adjust=bar_meta.adjust)")
    return len(off)


def get_bar_cache(market: Market) -> DailyCache:
    return DailyCache(db_path=market.data_dir / "bars.db")


def update_bars(market: Market, code: str, bars: pd.DataFrame, adjust: str, source: str) -> int:
    """Bulk upsert into the DailyCache schema (same tables DailyCache reads), plus bar_meta."""
    values = bars[BAR_COLUMNS].astype(float).to_numpy()
    rows = [(code, d, *[None if v != v else v for v in row], 0, adjust, source)
            for d, row in zip(bars["date"], values.tolist())]
    with connect(market, "bars.db", BARS_SCHEMA) as con:
        con.executemany("INSERT OR REPLACE INTO daily_bar (code,date,open,high,low,close,preclose,volume,"
                        "amount,turn,pct_chg,is_st,adjust,source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        first, last, count = con.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_bar "
                                         "WHERE code=? AND adjust=?", (code, adjust)).fetchone()
        con.execute("INSERT OR REPLACE INTO bar_meta VALUES (?,?,?,?,?,?)",
                    (code, adjust, first, last, count, now_text()))
    return len(rows)


@contextmanager
def connect(market: Market, name: str, schema: str):
    """Connection to a market database that commits on success and always closes."""
    market.data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(market.data_dir / name)
    try:
        con.executescript(schema)
        yield con
        con.commit()
    finally:
        con.close()


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def update_symbols(market: Market, frame: pd.DataFrame, source: str) -> int:
    rows = [(r.code, r.name, r.exchange, int(bool(r.is_etf)), int(bool(r.is_active)), source, now_text())
            for r in frame.itertuples(index=False)]
    with connect(market, "reference.db", REFERENCE_SCHEMA) as con:
        con.executemany("INSERT OR REPLACE INTO symbols VALUES (?,?,?,?,?,?,?)", rows)
    return len(rows)


def list_bar_codes(market: Market) -> list:
    """Codes with stored daily bars."""
    rows = query(market.data_dir / "bars.db", "SELECT code FROM bar_meta WHERE adjust='qfq' ORDER BY code")
    return [r[0] for r in rows]


def list_symbols(market: Market) -> pd.DataFrame:
    with connect(market, "reference.db", REFERENCE_SCHEMA) as con:
        return pd.read_sql("SELECT code, name, exchange, is_etf, is_active FROM symbols ORDER BY code", con)


def update_macro(market: Market, series_id: str, frame: pd.DataFrame, source: str) -> int:
    rows = [(series_id, r.date, float(r.value), source) for r in frame.itertuples(index=False)]
    with connect(market, "reference.db", REFERENCE_SCHEMA) as con:
        con.executemany("INSERT OR REPLACE INTO macro_series VALUES (?,?,?,?)", rows)
    return len(rows)


def update_actions(market: Market, code: str, frame: pd.DataFrame, source: str) -> int:
    """Splits (ratio, e.g. 4.0 for 4-for-1) and cash dividends from a provider bar frame."""
    rows = [(code, r["date"], kind, float(r[kind]), source)
            for kind in ("split", "dividend") if kind in frame
            for _, r in frame[frame[kind].fillna(0) > 0].iterrows()]
    with connect(market, "reference.db", REFERENCE_SCHEMA) as con:
        con.executemany("INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?,?,?)", rows)
    return len(rows)


def list_splits(market: Market, code: str) -> pd.DataFrame:
    with connect(market, "reference.db", REFERENCE_SCHEMA) as con:
        return pd.read_sql("SELECT date, value AS ratio FROM corporate_actions "
                           "WHERE code=? AND kind='split' ORDER BY date", con, params=(code,))


def update_shares(market: Market, frame: pd.DataFrame, source: str) -> int:
    """Share counts (code, date, value, available_from); a count is usable from available_from."""
    rows = [(r.code, r.date, float(r.value), r.available_from, source) for r in frame.itertuples(index=False)]
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        con.executemany("INSERT OR REPLACE INTO shares_outstanding VALUES (?,?,?,?,?)", rows)
    return len(rows)


def list_shares(market: Market, code: str) -> pd.DataFrame:
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        return pd.read_sql("SELECT date, value, available_from, source FROM shares_outstanding "
                           "WHERE code=? ORDER BY available_from", con, params=(code,))


def get_fetch_time(market: Market, key: str):
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        row = con.execute("SELECT updated_at FROM fetch_log WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def update_fetch_time(market: Market, key: str):
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        con.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?)", (key, now_text()))


def update_filers(market: Market, frame: pd.DataFrame, period: str):
    """Latest SEC filer location per code (loc like "US-CA" or "JP-"), from a frame response."""
    rows = [(r.code, int(r.cik), r.loc, period, int(r.ticker_count)) for r in frame.itertuples(index=False)]
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        con.executemany("INSERT INTO filers VALUES (?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                        "cik=excluded.cik, loc=excluded.loc, last_period=excluded.last_period, "
                        "ticker_count=excluded.ticker_count WHERE excluded.last_period >= filers.last_period", rows)


def list_domestic_codes(market: Market, since_period: str) -> set:
    """Single-ticker filers with a US location in a frame from `since_period` (e.g. CY2024Q1I) on."""
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        rows = con.execute("SELECT code FROM filers WHERE loc LIKE 'US%' AND ticker_count=1 "
                           "AND last_period>=?", (since_period,)).fetchall()
    return {r[0] for r in rows}


def clear_turnover(market: Market, code: str):
    con = sqlite3.connect(market.data_dir / "bars.db")
    try:
        con.execute("UPDATE daily_bar SET turn=NULL WHERE code=?", (code,))
        con.commit()
    finally:
        con.close()


def update_turnover(market: Market, code: str, turn: pd.Series) -> int:
    """Write daily turnover (%, indexed by date) into both adjust variants in bars.db."""
    rows = [(None if pd.isna(v) else float(v), code, d) for d, v in turn.items()]
    con = sqlite3.connect(market.data_dir / "bars.db")
    try:
        con.executemany("UPDATE daily_bar SET turn=? WHERE code=? AND date=?", rows)
        con.commit()
    finally:
        con.close()
    return len(rows)


def update_facts(market: Market, code: str, cik: int, frame: pd.DataFrame) -> int:
    rows = [(code, cik, r.concept, r.unit, r.period_start, r.period_end, float(r.value),
             None if pd.isna(r.fiscal_year) else int(r.fiscal_year), r.fiscal_period, r.form,
             r.filed, r.accession) for r in frame.itertuples(index=False)]
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        con.executemany("INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        con.execute("INSERT OR REPLACE INTO fact_updates VALUES (?,?,?)", (code, cik, now_text()))
    return len(rows)


def list_fact_updates(market: Market) -> dict:
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        return dict(con.execute("SELECT code, updated_at FROM fact_updates").fetchall())


def get_fact_asof(market: Market, code: str, concept: str, asof: str, duration: str = None):
    """Latest fact filed strictly before `asof`, so a backtest on `asof` never sees later filings.

    duration: None (any), "instant", "quarter" or "annual" (filters by period length).
    """
    sql = ("SELECT period_start, period_end, value, unit, form, filed FROM facts "
           "WHERE code=? AND concept=? AND filed<?")
    if duration == "instant":
        sql += " AND period_start=''"
    elif duration:
        low, high = DURATION_DAYS[duration]
        sql += (f" AND period_start<>'' AND julianday(period_end)-julianday(period_start) "
                f"BETWEEN {low} AND {high}")
    sql += " ORDER BY period_end DESC, filed DESC LIMIT 1"
    with connect(market, "fundamentals.db", FUNDAMENTALS_SCHEMA) as con:
        row = con.execute(sql, (code, concept, asof)).fetchone()
    keys = ("period_start", "period_end", "value", "unit", "form", "filed")
    return dict(zip(keys, row)) if row else None


def query(path: Path, sql: str) -> list:
    """Rows from an existing database file; empty when the file or table is missing."""
    if not path.exists():
        return []
    con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def build_status(market: Market) -> dict:
    """Coverage and freshness of a market's local data, for /api/markets."""
    d = market.data_dir
    bars = query(d / "bars.db", "SELECT COUNT(*), MIN(start_date), MAX(end_date), SUM(rows) "
                                "FROM bar_meta WHERE adjust='qfq'")
    symbols = query(d / "reference.db", "SELECT COUNT(*), SUM(is_active), SUM(is_etf) FROM symbols")
    facts = query(d / "fundamentals.db", "SELECT COUNT(DISTINCT code), COUNT(*), MAX(filed) FROM facts")
    shares = query(d / "fundamentals.db", "SELECT COUNT(DISTINCT code), MIN(date), MAX(date) FROM shares_outstanding")
    macro = query(d / "reference.db", "SELECT series_id, MAX(date), COUNT(*) FROM macro_series GROUP BY series_id")
    bar_count, first_date, last_date, bar_rows = bars[0] if bars else (0, None, None, 0)
    last_session = get_last_closed_session(market)
    last_run_path = BASE / "output" / f"market_update_{market.id.lower()}.json"
    last_run = json.loads(last_run_path.read_text(encoding="utf-8")) if last_run_path.exists() else None
    return {
        "id": market.id,
        "name": market.name,
        "currency": market.currency,
        "calendar": market.calendar,
        "benchmark": market.benchmark_symbol,
        "providers": market.providers,
        "is_survivorship_safe": market.providers.get("bars") == "eodhd",
        "last_closed_session": last_session,
        "bars": {"symbols": bar_count, "rows": bar_rows or 0, "first_date": first_date,
                 "last_date": last_date,
                 "is_current": bool(last_date and last_session and last_date >= last_session)},
        "symbol_master": dict(zip(("count", "active", "etfs"), symbols[0])) if symbols else {},
        "fundamentals": dict(zip(("companies", "facts", "last_filed"), facts[0])) if facts else {},
        "shares": dict(zip(("companies", "first_date", "last_date"), shares[0])) if shares else {},
        "turnover": (last_run or {}).get("turnover"),
        "macro": [{"series_id": s, "last_date": dt, "rows": n} for s, dt, n in macro],
        "last_run": last_run,
    }
