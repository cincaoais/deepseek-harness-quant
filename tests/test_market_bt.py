import math

import pandas as pd
import pytest

from backtest import market_bt
from backtest.market_rules import compute_trade_cost, is_buyable, round_to_lot
from backtest.market_validation import resolve_grade


@pytest.mark.parametrize("market_id, side, value, expected", [
    # 0.03% + RM3 + clearing 0.03% + stamp RM11 (11 started thousands) + 0.2% slippage
    ("MY", "buy", 10500, 3.15 + 3.0 + 3.15 + 11.0 + 21.0),
    # 0.03% + $0.99 + stamp 0.1% + SEC fee on the sell + 0.05% slippage
    ("US", "sell", 5000, 1.5 + 0.99 + 5.0 + 0.103 + 2.5),
])
def test_trade_cost_schedules(market_in, market_id, side, value, expected):
    assert compute_trade_cost(market_in(market_id).costs, side, value) == pytest.approx(expected)


@pytest.mark.parametrize("market_id, open_price, preclose, price_level, expected", [
    ("MY", 13.0, 10.0, 10.0, False),
    ("MY", 12.9, 10.0, 10.0, True),
    ("MY", 0.80, 0.50, 0.50, False),
    ("MY", 0.79, 0.50, 0.50, True),
    ("US", 20.0, 10.0, 10.0, True),
    ("US", math.nan, 10.0, 10.0, False),
])
def test_open_at_upper_limit_is_not_buyable(market_in, market_id, open_price, preclose, price_level, expected):
    assert is_buyable(market_in(market_id).rules, open_price, preclose, price_level) is expected


def test_board_lots_round_down_and_fractional_passes_through():
    assert (round_to_lot(3333.3, 100), round_to_lot(3.7, None)) == (3300, 3.7)


def test_portfolio_buys_whole_lots_at_next_open_after_costs(market_in):
    market = market_in("MY")
    dates = ["d1", "d2", "d3", "d4"]
    close = pd.DataFrame({"AAA": [3.0, 3.0, 3.3, 3.3], "BBB": [5.0, 5.0, 5.0, 5.0]}, index=dates)
    open_ = pd.DataFrame({"AAA": [3.0, 3.0, 3.0, 3.3], "BBB": [5.0, 5.0, 5.0, 5.0]}, index=dates)
    panel = {"close": close, "open": open_, "raw_close": close}
    score = pd.DataFrame({"AAA": 1.0, "BBB": 0.0}, index=dates)
    mask = pd.DataFrame(True, index=dates, columns=close.columns)
    result = market_bt.run_portfolio(market, panel, score, mask, ["d2"], top_n=1, capital=10000)
    cost = compute_trade_cost(market.costs, "buy", 3300 * 3.0)
    assert result["picks"] == {"d2": ["AAA"]}
    assert result["returns"]["d3"] == pytest.approx((3300 * 0.3 - cost) / 10000)
    assert result["returns"]["d4"] == 0


@pytest.mark.parametrize("ic, annual, holdout, is_safe, grade, reason", [
    (0.05, 0.12, 0.08, True, "valid", None),
    (0.05, 0.12, 0.08, False, "watch", "survivorship"),
    (0.05, 0.12, -0.02, True, "watch", "holdout"),
    (-0.01, 0.12, 0.08, True, "invalid", "IC"),
    (0.05, -0.01, 0.08, True, "invalid", "annual"),
])
def test_grade_follows_acceptance_rules(ic, annual, holdout, is_safe, grade, reason):
    result = {"portfolio": {"annual_return": annual, "sharpe": 0.9, "n_days": 2000},
              "ic": {"mean": ic}, "holdout": {"portfolio": {"annual_return": holdout}}}
    got, reasons = resolve_grade(result, is_safe)
    assert got == grade
    assert (reason is None and not reasons) or any(reason in r for r in reasons)


def test_halted_position_keeps_value_and_books_the_gap_on_resumption(market_in):
    market = market_in("US")
    dates = ["d1", "d2", "d3", "d4", "d5"]
    close = pd.DataFrame({"AAA": [10.0, 10.0, 10.0, math.nan, 12.0]}, index=dates)
    open_ = pd.DataFrame({"AAA": [10.0, 10.0, 10.0, math.nan, 12.0]}, index=dates)
    panel = {"close": close, "open": open_, "raw_close": close}
    score = pd.DataFrame({"AAA": 1.0}, index=dates)
    mask = pd.DataFrame(True, index=dates, columns=["AAA"])
    returns = market_bt.run_portfolio(market, panel, score, mask, ["d2"], top_n=1, capital=10000)["returns"]
    assert returns["d4"] == 0
    assert returns["d5"] == pytest.approx(0.2, rel=0.01)
