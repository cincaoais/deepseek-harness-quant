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


def test_turnover_puts_shares_and_volume_on_one_split_basis():
    bars = pd.DataFrame({"date": ["2020-08-03", "2020-09-01"], "volume": [4.0, 4.0]})
    counts = pd.DataFrame({"date": ["2020-07-17"], "value": [100.0], "available_from": ["2020-08-21"],
                           "source": ["yahoo"]})
    splits = pd.DataFrame({"date": ["2020-08-31"], "ratio": [4.0]})
    turn = market_update.compute_turnover(bars, market_update.resolve_share_counts(counts, splits))
    assert turn.to_dict() == {"2020-08-03": 1.0, "2020-09-01": 1.0}


@pytest.mark.parametrize("sec_value, expected_sources", [(100.0, ["sec_edgar", "yahoo"]), (500.0, ["yahoo"])])
def test_sec_counts_extend_yahoo_only_when_they_agree(sec_value, expected_sources):
    counts = pd.DataFrame({
        "date": ["2012-03-31", "2016-03-31", "2016-04-05"],
        "value": [sec_value, sec_value, 100.0],
        "available_from": ["2012-05-05", "2016-05-05", "2016-04-12"],
        "source": ["sec_edgar", "sec_edgar", "yahoo"],
    })
    resolved = market_update.resolve_share_counts(counts, pd.DataFrame(columns=["date", "ratio"]))
    assert sorted(resolved["source"].unique()) == expected_sources


def test_foreign_and_multi_class_tickers_get_no_turnover(market_in):
    market = market_in("US")
    bars = pd.DataFrame({"date": ["2026-01-05"], "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
                         "preclose": None, "volume": 50.0, "amount": 500.0, "turn": None, "pct_chg": None})
    filers = pd.DataFrame({"code": ["AAA.US", "TM.US", "GOOG.US"], "cik": [1, 2, 3],
                           "loc": ["US-CA", "JP-", "US-CA"], "ticker_count": [1, 1, 2]})
    market_store.update_filers(market, filers, "CY2026Q1I")
    for code in filers["code"]:
        market_store.update_bars(market, code, bars, "split", "test")
        market_store.update_bars(market, code, bars, "qfq", "test")
        market_store.update_shares(market, pd.DataFrame({"code": [code], "date": ["2025-12-31"], "value": [1000.0],
                                                         "available_from": ["2026-01-01"]}), "yahoo")
    market_update.update_turnover(market, list(filers["code"]))
    cache = market_store.get_bar_cache(market)
    turn = {c: cache.get_daily(c, adjust="qfq")["turn"].iloc[0] for c in filers["code"]}
    assert turn["AAA.US"] == 5.0
    assert pd.isna(turn["TM.US"]) and pd.isna(turn["GOOG.US"])


def test_update_bars_keeps_exchange_sessions_only(market_in, monkeypatch):
    market = market_in("US")
    monkeypatch.setattr(market_update, "get_provider", lambda name: FakeBars())
    monkeypatch.setattr(market_update, "get_last_closed_session", lambda m: "2026-01-09")
    market_update.update_bars(market, ["AAPL.US"])
    dates = market_store.get_bar_cache(market).get_daily("AAPL.US", adjust="qfq")["date"].tolist()
    assert dates[0] == "2026-01-02"
    assert "2026-01-01" not in dates
