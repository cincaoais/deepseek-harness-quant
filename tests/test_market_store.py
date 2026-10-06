import pandas as pd
import pytest

from data import market_store, market_update


def build_facts(rows):
    columns = ["concept", "unit", "period_start", "period_end", "value",
               "fiscal_year", "fiscal_period", "form", "filed", "accession"]
    return pd.DataFrame(rows, columns=columns)


def test_fact_asof_ignores_filings_on_or_after_the_date(market_in):
    market = market_in("US")
    ni = "us-gaap:NetIncomeLoss"
    market_store.update_facts(market, "AAPL.US", 320193, build_facts([
        [ni, "USD", "2025-12-28", "2026-03-28", 29.0, 2026, "Q2", "10-Q", "2026-05-01", "a1"],
        [ni, "USD", "2025-12-28", "2026-03-28", 28.5, 2026, "Q2", "10-Q/A", "2026-06-15", "a2"],
        [ni, "USD", "2024-09-29", "2025-09-27", 112.0, 2025, "FY", "10-K", "2025-10-31", "a0"],
    ]))
    def value(asof, duration):
        fact = market_store.get_fact_asof(market, "AAPL.US", ni, asof, duration)
        return fact and fact["value"]
    assert value("2026-05-01", "quarter") is None
    assert value("2026-05-02", "quarter") == 29.0
    assert value("2026-07-01", "quarter") == 28.5
    assert value("2026-07-01", "annual") == 112.0


def test_bar_frames_split_dividend_adjustment():
    frame = pd.DataFrame({"date": ["d1", "d2"], "open": [10.0, 11.0], "high": [10.0, 11.0],
                          "low": [10.0, 11.0], "close": [10.0, 11.0], "adj_close": [9.0, 11.0],
                          "volume": [100, 200]})
    bars = market_update.build_bar_frames(frame)
    assert bars["qfq"]["close"].tolist() == [9.0, 11.0]
    assert bars["split"]["close"].tolist() == [10.0, 11.0]
    assert bars["qfq"]["pct_chg"].round(4).tolist()[1] == pytest.approx(22.2222)
    assert bars["qfq"]["amount"].tolist() == [1000.0, 2200.0]


class FakeBars:
    """Bar provider whose history and adjustment can change between runs."""

    def __init__(self):
        self.adj = 1.0

    def list_daily_bars(self, symbols, start):
        dates = pd.bdate_range("2026-01-01", "2026-01-12").strftime("%Y-%m-%d")
        frame = pd.DataFrame({"date": dates, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                              "adj_close": 10.0 * self.adj, "volume": 100})
        return {s: frame[frame["date"] >= start].reset_index(drop=True) for s in symbols}


def test_update_bars_refetches_history_after_readjustment(market_in, monkeypatch):
    market = market_in("US")
    fake = FakeBars()
    monkeypatch.setattr(market_update, "get_provider", lambda name: fake)
    monkeypatch.setattr(market_update, "get_last_closed_session", lambda m: "2026-01-09")
    first = market_update.update_bars(market, ["AAPL.US"])
    fake.adj = 0.9
    second = market_update.update_bars(market, ["AAPL.US"])
    stored = market_store.get_bar_cache(market).get_daily("AAPL.US", adjust="qfq")
    assert (first["new"], second["readjusted"]) == (1, 1)
    assert stored["date"].max() == "2026-01-09"
    assert stored["close"].round(6).unique().tolist() == [9.0]
