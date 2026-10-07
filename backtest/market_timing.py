# -*- coding: utf-8 -*-
"""Market-level timing hypotheses for US / Bursa Malaysia, tested before the system relies on them.

1. L0 regime overlay: the benchmark held at (1 - cash ratio) of the strategy/timing.py state
   machine, decided at the close and applied from the next session, vs buy-and-hold.
2. Calendar windows: turn of month, pre-holiday, January, year end and named holidays
   (config calendar_windows), mean daily return inside vs outside each window.

Benchmark results carry no survivorship bias; the equal-weight universe (Yahoo bars) does, so
its calendar grades are capped at "watch". Cash earns nothing in the overlay.

Usage:
  python backtest/market_timing.py --market MY
"""
import argparse
import json
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from backtest.bt_report import compute_metrics  # noqa: E402
from backtest.market_validation import ARCHIVE_DIR, to_dated, to_json_safe  # noqa: E402
from data import market_store  # noqa: E402
from data.markets import Market, get_market, list_sessions  # noqa: E402
from strategy.timing import RegimeDetector  # noqa: E402

# enough history for the detector's 200-day average, ATR comparison and 60-day volatility
REGIME_WINDOW = 300
SENSITIVITY = [{"crisis_vol": v, "confirm_days": c} for v in (0.25, 0.30, 0.40) for c in (3, 5, 10)]
NAMED_WINDOW_SESSIONS = 3
EW_MIN_AMOUNT = {"US": 5_000_000, "MY": 200_000}


def compute_regime_path(bars: pd.DataFrame, config: dict, start: int = 250) -> pd.DataFrame:
    """Daily state, cash ratio and can_trade of the regime state machine (as known at each close)."""
    detector = RegimeDetector(config)
    rows = []
    for end in range(start, len(bars) + 1):
        state = detector.update(bars.iloc[max(0, end - REGIME_WINDOW):end])
        rows.append((bars["date"].iloc[end - 1], state, detector.cash_ratio(), detector.can_trade()))
    return pd.DataFrame(rows, columns=["date", "state", "cash_ratio", "can_trade"]).set_index("date")


def compute_overlay(bench: pd.Series, path: pd.DataFrame, cost_pct: float) -> pd.Series:
    """Benchmark returns scaled by yesterday's exposure, minus the cost of each exposure change."""
    exposure = (1 - path["cash_ratio"]).reindex(bench.index).ffill().shift(1).fillna(1.0)
    return exposure * bench - exposure.diff().abs().fillna(0) * cost_pct


def summarize(returns: pd.Series) -> dict:
    keys = ("annual_return", "sharpe", "max_drawdown", "volatility")
    metrics = compute_metrics(to_dated(returns)) if len(returns) > 1 else {}
    return {k: metrics.get(k) for k in keys}


def resolve_overlay_verdict(bench: dict, overlay: dict, bench_holdout: dict, overlay_holdout: dict) -> str:
    """helps: better Sharpe and shallower drawdown, holdout Sharpe not worse; hurts: lower Sharpe and return."""
    better_sharpe = (overlay["sharpe"] or 0) >= (bench["sharpe"] or 0)
    shallower = (overlay["max_drawdown"] or 0) >= (bench["max_drawdown"] or 0)
    holdout_ok = (overlay_holdout["sharpe"] or 0) >= (bench_holdout["sharpe"] or 0)
    if better_sharpe and shallower and holdout_ok:
        return "helps"
    if not better_sharpe and (overlay["annual_return"] or 0) < (bench["annual_return"] or 0):
        return "hurts"
    return "mixed"


def study_regime(market: Market, bars: pd.DataFrame, start: str, holdout: str) -> dict:
    bench = bars.set_index("date")["close"].pct_change(fill_method=None).fillna(0)
    cost_pct = sum(market.costs.get(k, 0) for k in ("commission_pct", "stamp_duty_pct", "slippage_pct"))
    path = compute_regime_path(bars, market.timing)
    overlay = compute_overlay(bench, path, cost_pct)
    window = bench.index >= start
    b, o = bench[window], overlay[window]
    hold_b, hold_o = b[b.index >= holdout], o[o.index >= holdout]
    exposure = (1 - path["cash_ratio"]).reindex(b.index).ffill()
    states = path["state"].reindex(b.index).ffill()
    result = {
        "config": market.timing or "A-share defaults",
        "benchmark": summarize(b), "overlay": summarize(o),
        "holdout": {"benchmark": summarize(hold_b), "overlay": summarize(hold_o)},
        "avg_exposure": float(exposure.mean()),
        "switches_per_year": float((states != states.shift()).sum() / max(len(states) / 252, 1e-9)),
        "time_in_state": {k: round(float(v), 3) for k, v in states.value_counts(normalize=True).items()},
        "current": {"state": path["state"].iloc[-1], "cash_ratio": float(path["cash_ratio"].iloc[-1]),
                    "as_of": path.index[-1]},
        "by_year": {int(y): {"benchmark": round(float((1 + b[b.index.str[:4] == y]).prod() - 1), 4),
                             "overlay": round(float((1 + o[o.index.str[:4] == y]).prod() - 1), 4)}
                    for y in sorted(set(b.index.str[:4]))},
    }
    result["verdict"] = resolve_overlay_verdict(result["benchmark"], result["overlay"],
                                                result["holdout"]["benchmark"], result["holdout"]["overlay"])
    result["sensitivity"] = []
    for override in SENSITIVITY:
        alt = compute_overlay(bench, compute_regime_path(bars, {**market.timing, **override}), cost_pct)[window]
        result["sensitivity"].append({**override, **summarize(alt)})
    return result


def build_calendar_windows(market: Market, sessions: list) -> dict:
    """Boolean flags per session for each calendar window, computed on the exchange calendar around
    the sample so months and holidays at the sample edges are judged correctly."""
    first, last = pd.Timestamp(sessions[0]) - timedelta(days=60), pd.Timestamp(sessions[-1]) + timedelta(days=60)
    full = sorted(list_sessions(market, first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")))
    frame = pd.DataFrame({"day": pd.to_datetime(full)}, index=full)
    by_month = frame.groupby(frame.index.str[:7])
    next_day = frame["day"].shift(-1)
    december = frame["day"].dt.month == 12
    windows = {
        "turn_of_month": (by_month.cumcount(ascending=False) == 0) | (by_month.cumcount() < 3),
        "pre_holiday": pd.Series([pd.notna(n) and any(x.weekday() < 5 for x in pd.date_range(d, n, inclusive="neither"))
                                  for d, n in zip(frame["day"], next_day)], index=frame.index),
        "january": frame["day"].dt.month == 1,
        "year_end": (frame[december].groupby(frame.index[december].str[:4]).cumcount(ascending=False) < 5)
                    .reindex(frame.index, fill_value=False),
    }
    days, open_days = frame["day"], set(full)
    for name, dates in (market.calendar_windows.get("named_holidays") or {}).items():
        pre, post = pd.Series(False, index=frame.index), pd.Series(False, index=frame.index)
        for approx in pd.to_datetime(dates):
            closure = resolve_closure(open_days, approx) if first < approx < last else None
            if closure:
                pre[days[days < closure[0]].index[-NAMED_WINDOW_SESSIONS:]] = True
                post[days[days > closure[1]].index[:NAMED_WINDOW_SESSIONS]] = True
        windows[f"pre_{name}"], windows[f"post_{name}"] = pre, post
    return {k: v.astype(bool).reindex(sessions) for k, v in windows.items()}


def resolve_closure(sessions: set, approx: pd.Timestamp):
    """First and last closed weekday of the holiday closest to `approx` (within ±3 days), or None."""
    near = [d for d in pd.date_range(approx - timedelta(days=3), approx + timedelta(days=3))
            if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in sessions]
    if not near:
        return None
    first = min(near, key=lambda d: abs((d - approx).days))
    last = first
    while (last + timedelta(days=1)).weekday() >= 5 or (last + timedelta(days=1)).strftime("%Y-%m-%d") not in sessions:
        last += timedelta(days=1)
        if (last - first).days > 10:
            break
    return first, last


def compute_window_stats(returns: pd.Series, flag: pd.Series, holdout: str) -> dict:
    """Mean daily return inside vs outside, Welch t, share of years where inside beat outside, holdout gap."""
    inside, outside = returns[flag], returns[~flag]
    if len(inside) < 10 or len(outside) < 10:
        return {"n": int(len(inside))}
    t = (inside.mean() - outside.mean()) / np.sqrt(inside.var() / len(inside) + outside.var() / len(outside))
    years = sorted(set(returns.index.str[:4]))
    wins = [returns[flag & (returns.index.str[:4] == y)].mean() > returns[~flag & (returns.index.str[:4] == y)].mean()
            for y in years if (flag & (returns.index.str[:4] == y)).any()]
    late = returns.index >= holdout
    return {
        "n": int(len(inside)),
        "inside_bp": round(float(inside.mean() * 1e4), 2), "outside_bp": round(float(outside.mean() * 1e4), 2),
        "t_stat": round(float(t), 2), "years_inside_better": round(float(np.mean(wins)), 2) if wins else None,
        "holdout_gap_bp": round(float((returns[flag & late].mean() - returns[~flag & late].mean()) * 1e4), 2),
    }


def resolve_window_grade(stats: dict, is_survivorship_safe: bool) -> tuple:
    """valid: |t| ≥ 2, ≥ 60% of years agree, holdout gap has the same sign; watch: |t| ≥ 1."""
    if "t_stat" not in stats:
        return "invalid", ["too few window days"]
    t, consistency, gap = stats["t_stat"], stats["years_inside_better"] or 0, stats["holdout_gap_bp"]
    agree = consistency if t > 0 else 1 - consistency
    same_sign = gap * t > 0
    if abs(t) >= 2 and agree >= 0.6 and same_sign:
        if not is_survivorship_safe:
            return "watch", ["equal-weight universe lacks delisted stocks, so at most 'watch'"]
        return "valid", []
    if abs(t) >= 1:
        return "watch", [r for r, bad in (("|t| < 2", abs(t) < 2), ("years disagree", agree < 0.6),
                                          ("holdout flips sign", not same_sign)) if bad]
    return "invalid", [f"|t| = {abs(t):.2f} < 1"]


def build_ew_returns(market: Market, min_amount: float) -> pd.Series:
    """Equal-weight daily return (capped at ±50%) of stocks whose average traded value over the previous
    20 sessions is at least `min_amount`; filtering on the same day's value would favour that day's movers."""
    rows = market_store.query(market.data_dir / "bars.db",
                              "SELECT date, AVG(MIN(MAX(pct_chg, -50), 50)) FROM ("
                              "SELECT date, pct_chg, AVG(amount) OVER (PARTITION BY code ORDER BY date "
                              "ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS adv FROM daily_bar WHERE adjust='qfq') "
                              f"WHERE adv >= {float(min_amount)} AND pct_chg IS NOT NULL GROUP BY date")
    return pd.Series({d: v / 100 for d, v in rows}).sort_index()


def study_calendar(market: Market, bench: pd.Series, ew: pd.Series, start: str, holdout: str) -> dict:
    sessions = sorted(list_sessions(market, start, bench.index[-1]) & set(bench.index))
    windows = build_calendar_windows(market, sessions)
    is_safe = market.providers.get("bars") == "eodhd"
    out = {}
    for name, flag in windows.items():
        out[name] = {}
        for label, returns, safe in (("benchmark", bench, True), ("equal_weight", ew, is_safe)):
            r = returns.reindex(sessions).dropna()
            stats = compute_window_stats(r, flag.reindex(r.index).fillna(False).astype(bool), holdout)
            grade, reasons = resolve_window_grade(stats, safe)
            out[name][label] = {**stats, "grade": grade, "reasons": reasons}
    return out


def run_timing(market: Market) -> dict:
    t0 = time.time()
    cfg = market.validation
    start, holdout = cfg.get("start", "2012-01-01"), cfg.get("holdout_start", "2023-01-01")
    bars = market_store.get_bar_cache(market).get_daily(market.benchmark_symbol, start=market.history_start, adjust="qfq")
    bench = bars.set_index("date")["close"].pct_change(fill_method=None).dropna()
    report = {
        "market": market.id, "benchmark": market.benchmark_symbol, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "start": start, "holdout_start": holdout,
        "regime": study_regime(market, bars, start, holdout),
        "calendar": study_calendar(market, bench[bench.index >= start],
                                   build_ew_returns(market, EW_MIN_AMOUNT.get(market.id, 0)), start, holdout),
        "elapsed_s": None,
    }
    report["elapsed_s"] = round(time.time() - t0, 1)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    (ARCHIVE_DIR / f"timing_{market.id.lower()}.json").write_text(
        json.dumps(to_json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def get_timing_report(market_id: str):
    path = ARCHIVE_DIR / f"timing_{market_id.lower()}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="US")
    r = run_timing(get_market(parser.parse_args().market))
    g = r["regime"]
    fmt = lambda m: f"annual {m['annual_return']:+.1%} Sharpe {m['sharpe']:.2f} maxDD {m['max_drawdown']:.1%}"
    print(f"{r['market']} L0 overlay on {r['benchmark']}: {g['verdict']} · avg exposure {g['avg_exposure']:.0%} · "
          f"{g['switches_per_year']:.1f} switches/yr")
    print(f"  buy & hold  {fmt(g['benchmark'])}\n  overlay     {fmt(g['overlay'])}")
    for name, sides in r["calendar"].items():
        b, e = sides["benchmark"], sides["equal_weight"]
        print(f"  {name:<28} bench {b.get('inside_bp', '–'):>6}bp vs {b.get('outside_bp', '–'):>5}bp t {b.get('t_stat', '–'):>5} "
              f"{b['grade']:<7} | EW t {e.get('t_stat', '–'):>5} {e['grade']}")
    print(f"archived: output/market_validation/timing_{r['market'].lower()}.json ({r['elapsed_s']}s)")


if __name__ == "__main__":
    main()
