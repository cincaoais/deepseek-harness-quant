import pandas as pd
import pytest

from backtest import market_timing
from data.markets import list_sessions


def test_calendar_windows_flag_month_turns_holiday_eves_and_year_end(market_in):
    market = market_in("US")
    sessions = sorted(list_sessions(market, "2025-06-20", "2026-01-05"))
    flags = market_timing.build_calendar_windows(market, sessions)
    on = lambda name: [d for d in sessions if flags[name][d]]
    assert ["2025-06-30", "2025-07-01", "2025-07-02", "2025-07-03"] == [d for d in on("turn_of_month") if d < "2025-07-10"]
    assert "2025-07-03" in on("pre_holiday") and "2025-07-02" not in on("pre_holiday")
    assert on("year_end") == ["2025-12-24", "2025-12-26", "2025-12-29", "2025-12-30", "2025-12-31"]


def test_named_holiday_windows_snap_to_the_bursa_closure(market_in):
    market = market_in("MY")
    sessions = sorted(list_sessions(market, "2026-02-01", "2026-03-10"))
    flags = market_timing.build_calendar_windows(market, sessions)
    assert [d for d in sessions if flags["pre_chinese_new_year"][d]] == ["2026-02-12", "2026-02-13", "2026-02-16"]
    assert [d for d in sessions if flags["post_chinese_new_year"][d]] == ["2026-02-19", "2026-02-20", "2026-02-23"]


def test_overlay_uses_yesterdays_exposure_and_charges_changes():
    bench = pd.Series([0.01, 0.02, -0.03], index=["d1", "d2", "d3"])
    path = pd.DataFrame({"cash_ratio": [0.0, 1.0, 1.0]}, index=["d1", "d2", "d3"])
    overlay = market_timing.compute_overlay(bench, path, cost_pct=0.001)
    assert overlay.round(6).tolist() == [0.01, 0.02, -0.001]


@pytest.mark.parametrize("stats, is_safe, grade", [
    ({"t_stat": 3.0, "years_inside_better": 0.8, "holdout_gap_bp": 5.0}, True, "valid"),
    ({"t_stat": 3.0, "years_inside_better": 0.8, "holdout_gap_bp": 5.0}, False, "watch"),
    ({"t_stat": 3.0, "years_inside_better": 0.8, "holdout_gap_bp": -5.0}, True, "watch"),
    ({"t_stat": 0.4, "years_inside_better": 0.5, "holdout_gap_bp": 1.0}, True, "invalid"),
])
def test_window_grades(stats, is_safe, grade):
    assert market_timing.resolve_window_grade(stats, is_safe)[0] == grade


def test_equal_weight_universe_is_chosen_on_past_trading_value_not_todays(market_in, tmp_path):
    import dataclasses
    from data import market_store

    market = dataclasses.replace(market_in("MY"), data_dir=tmp_path)
    dates = [f"2026-01-{d:02d}" for d in range(1, 26)]
    for code, amounts, changes in (("AAA.MY", [1e6] * 25, [0.0] * 25),
                                   ("BBB.MY", [1.0] * 24 + [1e9], [0.0] * 24 + [40.0])):
        bars = pd.DataFrame({"date": dates, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "preclose": 1.0,
                             "volume": 1.0, "amount": amounts, "turn": None, "pct_chg": changes})
        market_store.update_bars(market, code, bars, "qfq", "test")
    ew = market_timing.build_ew_returns(market, 1e5)
    assert ew["2026-01-25"] == 0.0
