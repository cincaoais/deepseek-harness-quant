# -*- coding: utf-8 -*-
"""Update local data for a US / Bursa Malaysia market: symbol master, daily bars, SEC facts, FRED macro.

Usage:
  python data/market_update.py --market US                          bars for the seed universe + benchmark
  python data/market_update.py --market US --universe all --limit 500
  python data/market_update.py --market US --symbols --bars --fundamentals --macro
  python data/market_update.py --market MY
  python data/market_update.py --status

Providers, universe and history start come from config/markets.yaml (see docs/markets-data.md).
"""
import argparse
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from data import market_store  # noqa: E402
from data.markets import Market, get_last_closed_session, get_market, list_markets  # noqa: E402
from data.providers import get_provider  # noqa: E402
from data.providers.base import ProviderError  # noqa: E402

BATCH_SIZE = 100
OVERLAP_DAYS = 10
# a stored adjusted close drifting more than this means a new split/dividend → refetch history
READJUST_TOLERANCE = 1e-3


def build_seed_frame(market: Market) -> pd.DataFrame:
    codes = [market.to_canonical(c) for c in market.universe.get("seed", [])]
    return pd.DataFrame({"code": codes, "name": None, "exchange": None, "is_etf": False, "is_active": True})


def update_symbol_master(market: Market) -> int:
    source = market.providers.get("symbols", "seed")
    if source == "seed":
        frame = build_seed_frame(market)
    else:
        provider = get_provider(source)
        if source == "eodhd":
            frame = provider.list_symbols(market.provider_symbols["eodhd"]["exchange"])
            frame["code"] = frame["code"].map(market.to_canonical)
        else:
            frame = provider.list_symbols()
            frame["code"] = frame["code"].map(lambda c: market.from_provider(c, source))
    return market_store.update_symbols(market, frame, source)


def resolve_universe(market: Market, universe: str, limit: int = None) -> list:
    """Canonical codes to update, benchmark first."""
    if universe == "seed":
        codes = [market.to_canonical(c) for c in market.universe.get("seed", [])]
    else:
        master = market_store.list_symbols(market)
        if master.empty:
            update_symbol_master(market)
            master = market_store.list_symbols(market)
        master = master[master["is_active"] == 1]
        if not market.universe.get("include_etfs", True):
            master = master[master["is_etf"] == 0]
        patterns = market.universe.get("exclude_name_patterns") or []
        if patterns:
            excluded = master["name"].fillna("").str.contains("|".join(patterns), flags=re.IGNORECASE)
            master = master[~excluded]
        codes = master["code"].tolist()
    codes = [market.benchmark_symbol] + [c for c in dict.fromkeys(codes) if c != market.benchmark_symbol]
    return codes[:limit] if limit else codes


def build_bar_frames(frame: pd.DataFrame) -> dict:
    """Provider bars → DailyCache rows for adjust='qfq' and adjust='split'."""
    ratio = frame["adj_close"] / frame["close"]
    amount = frame["close"] * frame["volume"]
    out = {}
    for adjust, scale in (("qfq", ratio), ("split", 1.0)):
        bars = pd.DataFrame({
            "date": frame["date"],
            "open": frame["open"] * scale, "high": frame["high"] * scale,
            "low": frame["low"] * scale, "close": frame["close"] * scale,
            "volume": frame["volume"], "amount": amount, "turn": None, "is_st": 0,
        })
        bars["preclose"] = bars["close"].shift(1)
        bars["pct_chg"] = (bars["close"] / bars["preclose"] - 1) * 100
        out[adjust] = bars
    return out


def should_readjust(stored: pd.DataFrame, frame: pd.DataFrame) -> bool:
    """True when overlapping adjusted closes changed, i.e. the provider re-adjusted history."""
    if stored is None or stored.empty:
        return False
    both = stored[["date", "close"]].merge(frame[["date", "adj_close"]], on="date")
    if both.empty:
        return False
    drift = (both["close"] / both["adj_close"] - 1).abs().max()
    return bool(drift > READJUST_TOLERANCE)


def store_bars(cache, code: str, frame: pd.DataFrame, source: str, is_full: bool) -> int:
    rows = 0
    for adjust, bars in build_bar_frames(frame).items():
        # the first incremental row has no preclose; it is already stored from the previous run
        bars = bars if is_full else bars.iloc[1:]
        rows += cache.put_daily(code, bars, adjust=adjust, source=source)
    return rows


def update_bars(market: Market, codes: list) -> dict:
    """Full history for new codes; incremental with an overlap check for the rest."""
    source = market.providers.get("bars", "yahoo")
    provider = get_provider(source)
    cache = market_store.get_bar_cache(market)
    stats = {"requested": len(codes), "rows": 0, "new": 0, "readjusted": 0, "missing": []}
    last_session = get_last_closed_session(market)

    def download(batch, start):
        """Frames keyed by canonical code, cut at the last closed session (no intraday bars)."""
        symbols = {market.to_provider(c, source): c for c in batch}
        frames = provider.list_daily_bars(list(symbols), start)
        frames = {symbols[s]: f[f["date"] <= last_session] for s, f in frames.items()}
        return {c: f for c, f in frames.items() if not f.empty}

    metas = {c: cache.get_meta(c, "qfq") for c in codes}
    new_codes = [c for c in codes if metas[c] is None]
    old_codes = [c for c in codes if metas[c] is not None]
    refetch = []
    for i in range(0, len(old_codes), BATCH_SIZE):
        batch = old_codes[i:i + BATCH_SIZE]
        last = min(metas[c]["end_date"] for c in batch)
        start = (date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)).isoformat()
        frames = download(batch, start)
        for code in batch:
            frame = frames.get(code)
            if frame is None:
                stats["missing"].append(code)
            elif should_readjust(cache.get_daily(code, start=start, adjust="qfq"), frame):
                refetch.append(code)
            else:
                stats["rows"] += store_bars(cache, code, frame, source, is_full=False)
    full_codes = new_codes + refetch
    for i in range(0, len(full_codes), BATCH_SIZE):
        batch = full_codes[i:i + BATCH_SIZE]
        frames = download(batch, market.history_start)
        for code in batch:
            frame = frames.get(code)
            if frame is None:
                stats["missing"].append(code)
                continue
            stats["rows"] += store_bars(cache, code, frame, source, is_full=True)
    stats["new"] = len([c for c in new_codes if c not in stats["missing"]])
    stats["readjusted"] = len(refetch)
    return stats


def update_fundamentals(market: Market, codes: list) -> dict:
    """SEC company facts for codes with a CIK, skipping those refreshed within refresh_days."""
    if market.providers.get("fundamentals") != "sec_edgar":
        return {"skipped": "no fundamentals provider configured"}
    sec = get_provider("sec_edgar")
    ciks = sec.get_ticker_ciks()
    concepts = market.fundamentals.get("concepts") or []
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400 * market.fundamentals.get("refresh_days", 7)))
    recent = {c for c, at in market_store.list_fact_updates(market).items() if at >= cutoff}
    stats = {"companies": 0, "facts": 0, "no_cik": 0, "no_facts": 0, "fresh": 0, "failed": []}
    for code in codes:
        cik = ciks.get(market.to_provider(code, "sec_edgar"))
        if cik is None:
            stats["no_cik"] += 1
        elif code in recent:
            stats["fresh"] += 1
        else:
            try:
                frame = sec.parse_company_facts(sec.get_company_facts(cik), concepts)
            except ProviderError as exc:
                if exc.status == 404:
                    stats["no_facts"] += 1
                else:
                    stats["failed"].append(f"{code}: {exc}"[:160])
                continue
            stats["facts"] += market_store.update_facts(market, code, cik, frame)
            stats["companies"] += 1
    return stats


def update_macro(market: Market) -> dict:
    source = market.providers.get("macro")
    series = market.macro.get("series") or []
    if not source or not series:
        return {"skipped": "no macro provider configured"}
    provider = get_provider(source)
    stats = {"series": 0, "rows": 0, "failed": []}
    for series_id in series:
        try:
            stats["rows"] += market_store.update_macro(market, series_id, provider.get_series(series_id), source)
            stats["series"] += 1
        except ProviderError as exc:
            stats["failed"].append(f"{series_id}: {exc}"[:160])
    return stats


def handle_update(args) -> dict:
    market = get_market(args.market)
    universe = args.universe or market.universe.get("default", "seed")
    is_default_run = not (args.symbols or args.bars or args.fundamentals or args.macro)
    report = {"market": market.id, "universe": universe, "started_at": market_store.now_text()}
    if args.symbols:
        report["symbols"] = update_symbol_master(market)
    codes = resolve_universe(market, universe, args.limit)
    if args.bars or is_default_run:
        report["bars"] = update_bars(market, codes)
    if args.fundamentals:
        report["fundamentals"] = update_fundamentals(market, codes)
    if args.macro:
        report["macro"] = update_macro(market)
    report["finished_at"] = market_store.now_text()
    out = BASE / "output" / f"market_update_{market.id.lower()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="US", help="market id from config/markets.yaml")
    parser.add_argument("--universe", choices=["seed", "all"], help="default: universe.default in config")
    parser.add_argument("--limit", type=int, help="cap the number of symbols (benchmark counts as one)")
    parser.add_argument("--symbols", action="store_true", help="refresh the symbol master")
    parser.add_argument("--bars", action="store_true", help="update daily bars (default when no step given)")
    parser.add_argument("--fundamentals", action="store_true", help="update SEC company facts")
    parser.add_argument("--macro", action="store_true", help="update macro series")
    parser.add_argument("--status", action="store_true", help="print coverage of every market and exit")
    args = parser.parse_args()
    if args.status:
        result = [market_store.build_status(m) for m in list_markets().values()]
    else:
        result = handle_update(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
