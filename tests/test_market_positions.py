import pandas as pd
import pytest

from backtest.market_rules import compute_trade_cost
from data import market_store
from strategy import market_positions, market_track


@pytest.fixture
def market(market_in, tmp_path, monkeypatch):
    monkeypatch.setattr(market_track, "DESK_DIR", tmp_path / "desk")
    return market_in("US")


def store_closes(market, code, closes: dict, splits: dict = None):
    dates = list(closes)
    bars = pd.DataFrame({"date": dates, "open": list(closes.values()), "high": list(closes.values()),
                         "low": list(closes.values()), "close": list(closes.values()), "preclose": None,
                         "volume": 100.0, "amount": None, "turn": None, "pct_chg": None})
    market_store.update_bars(market, code, bars, "split", "test")
    if splits:
        actions = pd.DataFrame({"date": list(splits), "split": list(splits.values())})
        market_store.update_actions(market, code, actions, "test")


def test_fill_defaults_to_fees_without_slippage_and_links_the_approved_card(market):
    market_track.write_json(market_track.get_desk_path(market, "decisions"),
                            [{"code": "AAPL.US", "action": "buy", "card_date": "2026-10-05"}])
    position = market_positions.create_position(market, "aapl", 10, 200, "2026-10-06")
    costs = {k: v for k, v in market.costs.items() if k != "slippage_pct"}
    assert (position["code"], position["source"]) == ("AAPL.US", "card 2026-10-05")
    assert position["entry_fees"] == round(compute_trade_cost(costs, "buy", 2000), 2)


def test_marks_restate_a_position_across_a_later_split(market):
    store_closes(market, "AAPL.US", {"2020-08-27": 125.0, "2020-08-31": 129.0}, splits={"2020-08-31": 4.0})
    position = market_positions.create_position(market, "AAPL", 10, 500, "2020-08-27", fees=0)
    marks = market_positions.build_book(market)["open"][0]["marks"]
    assert (marks["entry"], marks["shares"], marks["last"]) == (125.0, 40.0, 129.0)
    assert marks["pnl"] == pytest.approx(40 * 4.0)
    closed = market_positions.close_position(market, position["id"], 130, "2020-09-01", fees=0)
    assert market_positions.build_book(market)["closed"][0]["realized"] == pytest.approx(40 * 130 - 10 * 500)
    assert closed["status"] == "closed"


@pytest.mark.parametrize("last, sessions, on_cards, expected", [
    (84.0, 5, True, ["hard_loss"]),
    (101.0, 45, False, ["rebalance"]),
    (101.0, 45, True, []),
])
def test_exit_alerts_follow_the_stop_and_the_rebalance_rule(market, last, sessions, on_cards, expected):
    marks = {"entry": 100.0, "last": last, "peak": max(last, 100.0), "sessions_held": sessions,
             "pnl_pct": last / 100 - 1}
    alerts = market_positions.list_exit_alerts(market, marks, on_cards)
    assert [a["rule"] for a in alerts] == expected
