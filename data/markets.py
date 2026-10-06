# -*- coding: utf-8 -*-
"""US / Bursa Malaysia market registry: config, canonical symbols, calendars, data paths.

Canonical symbol = "<local code>.<market id>", e.g. AAPL.US, BRK.B.US, 0166.MY.
Config comes from config/markets.yaml, falling back to config/markets.yaml.example.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from data.cache import CACHE_DIR

BASE = Path(__file__).resolve().parent.parent
CONFIG_PATHS = (BASE / "config" / "markets.yaml", BASE / "config" / "markets.yaml.example")


@dataclass(frozen=True)
class Market:
    id: str
    name: str
    currency: str
    calendar: str
    history_start: str
    benchmark: dict
    providers: dict
    provider_symbols: dict
    universe: dict
    fundamentals: dict
    macro: dict
    data_dir: Path

    @property
    def benchmark_symbol(self) -> str:
        return self.to_canonical(self.benchmark["code"])

    def to_canonical(self, local_code) -> str:
        return f"{str(local_code).strip().upper()}.{self.id}"

    def to_provider(self, canonical: str, provider: str) -> str:
        if canonical == self.benchmark_symbol and provider in self.benchmark:
            return self.benchmark[provider]
        fmt = self.provider_symbols.get(provider, {})
        local = canonical.rsplit(".", 1)[0]
        return local.replace(".", fmt.get("class_separator", ".")) + fmt.get("suffix", "")

    def from_provider(self, symbol: str, provider: str) -> str:
        if symbol == self.benchmark.get(provider):
            return self.benchmark_symbol
        fmt = self.provider_symbols.get(provider, {})
        suffix = fmt.get("suffix", "")
        local = symbol[: -len(suffix)] if suffix and symbol.endswith(suffix) else symbol
        return self.to_canonical(local.replace(fmt.get("class_separator", "."), "."))


def list_markets(paths=CONFIG_PATHS) -> dict:
    """All configured markets keyed by id; the first existing config path wins."""
    path = next((p for p in paths if Path(p).exists()), None)
    if path is None:
        return {}
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    markets = (parse_market(mid, block) for mid, block in (cfg.get("markets") or {}).items())
    return {m.id: m for m in markets}


def parse_market(market_id: str, block: dict) -> Market:
    return Market(
        id=market_id.upper(),
        name=block.get("name", market_id),
        currency=block["currency"],
        calendar=block["calendar"],
        history_start=str(block.get("history_start", "2010-01-01")),
        benchmark=block["benchmark"],
        providers=block.get("providers") or {},
        provider_symbols=block.get("provider_symbols") or {},
        universe=block.get("universe") or {},
        fundamentals=block.get("fundamentals") or {},
        macro=block.get("macro") or {},
        data_dir=CACHE_DIR / "markets" / market_id.lower(),
    )


def get_market(market_id: str) -> Market:
    markets = list_markets()
    key = market_id.upper()
    if key not in markets:
        raise KeyError(f"market {key!r} not in config/markets.yaml (configured: {sorted(markets)})")
    return markets[key]


def get_last_closed_session(market: Market, now=None) -> str:
    """Most recent session whose close has already passed, as YYYY-MM-DD."""
    import exchange_calendars as xcals

    cal = xcals.get_calendar(market.calendar)
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    now = now.tz_localize("UTC") if now.tzinfo is None else now
    end = min(now.tz_convert(None).normalize(), cal.last_session)
    sessions = cal.sessions_in_range(end - timedelta(days=14), end)
    closed = [s for s in sessions if cal.session_close(s) <= now]
    return closed[-1].strftime("%Y-%m-%d") if closed else None
