# -*- coding: utf-8 -*-
"""Nine-step factor validation for US / Bursa Malaysia, same chain as the A-share factor pool:

scope → generate → IC screen → duplicate gate → portfolio (T+1 open) → by year / holdout
→ orthogonality → capacity → archive

Grades follow harness/home/skills/backtest-acceptance (valid / watch / invalid). Data without
delisted stocks, a failed holdout or a duplicate cap a factor at "watch".

Usage:
  python backtest/market_validation.py --market US
  python backtest/market_validation.py --market MY --factors turn_20 lowvol_60
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from backtest.bt_report import compute_metrics  # noqa: E402
from backtest.market_bt import (build_eligibility, build_panel, compute_adv,  # noqa: E402
                                compute_forward_returns, compute_rank_ic, list_rebalance_dates, run_portfolio)
from data import market_store  # noqa: E402
from data.markets import Market, get_market  # noqa: E402
from factors.market_factors import TURNOVER_FACTORS, compute_factor  # noqa: E402

ARCHIVE_DIR = BASE / "output" / "market_validation"
DUPLICATE_CORR = 0.7
CAPACITY_QUANTILE = 0.1


def summarize_ic(ic: pd.Series) -> dict:
    if ic.empty:
        return {"mean": None, "icir": None, "t_stat": None, "positive_ratio": None, "n": 0, "by_year": {}}
    std = ic.std()
    return {
        "mean": float(ic.mean()),
        "icir": float(ic.mean() / std) if std > 0 else None,
        "t_stat": float(ic.mean() / std * np.sqrt(len(ic))) if std > 0 else None,
        "positive_ratio": float((ic > 0).mean()),
        "n": int(len(ic)),
        "by_year": {y: round(float(v), 4) for y, v in ic.groupby(ic.index.str[:4]).mean().items()},
    }


def summarize_returns(returns: pd.Series, bench: pd.Series) -> dict:
    """Metrics, excess over the benchmark and calendar-year returns."""
    r = returns.copy()
    r.index = pd.to_datetime(r.index)
    b = bench.reindex(returns.index).fillna(0)
    b.index = r.index
    metrics, bench_metrics = compute_metrics(r), compute_metrics(b)
    yearly = lambda x: x.groupby(x.index.year).apply(lambda y: (1 + y).prod() - 1)
    return {
        "metrics": metrics,
        "excess_annual": metrics.get("annual_return", 0) - bench_metrics.get("annual_return", 0),
        "by_year": {int(y): round(float(v), 4) for y, v in yearly(r).items()},
        "excess_by_year": {int(y): round(float(v), 4) for y, v in (yearly(r) - yearly(b)).items()},
    }


def compute_capacity(panel: dict, picks: dict, participation: float, top_n: int):
    """Capital at which a typical rebalance keeps every position within `participation` of
    the 10th-percentile 20-day median traded value of its picks."""
    adv = panel["adv_20"]
    per_date = [adv.loc[d, codes].quantile(CAPACITY_QUANTILE) for d, codes in picks.items() if codes]
    per_date = [v for v in per_date if v == v]
    return float(np.median(per_date) * participation * top_n) if per_date else None


def resolve_grade(result: dict, is_survivorship_safe: bool) -> tuple:
    """valid: annual > 0, Sharpe > 0.5, holdout annual > 0; invalid: annual ≤ 0 or IC ≤ 0."""
    metrics = result["portfolio"]
    annual, sharpe = metrics.get("annual_return", 0), metrics.get("sharpe", 0)
    ic_mean = result["ic"]["mean"]
    holdout = result["holdout"]["portfolio"].get("annual_return")
    reasons = []
    if annual <= 0:
        return "invalid", ["annual return ≤ 0 after costs"]
    if ic_mean is None or ic_mean <= 0:
        return "invalid", ["IC ≤ 0 in the configured direction"]
    if metrics.get("n_days", 0) < 60:
        reasons.append("fewer than 60 trading days")
    if sharpe <= 0.5:
        reasons.append(f"Sharpe {sharpe:.2f} ≤ 0.5")
    if holdout is None or holdout <= 0:
        reasons.append("holdout annual return ≤ 0")
    if result.get("duplicate_of"):
        reasons.append(f"duplicate of {result['duplicate_of']} (score correlation ≥ {DUPLICATE_CORR})")
    if not is_survivorship_safe:
        reasons.append("bars lack delisted stocks (survivorship bias), so at most 'watch'")
    return ("watch" if reasons else "valid"), reasons


def split_holdout(returns: pd.Series, ic: pd.Series, holdout_start: str) -> dict:
    def metrics(r):
        r = r.copy()
        r.index = pd.to_datetime(r.index)
        return compute_metrics(r) if len(r) > 1 else {}
    return {
        "in_sample": {"portfolio": metrics(returns[returns.index < holdout_start]),
                      "ic_mean": float(ic[ic.index < holdout_start].mean()) if len(ic) else None},
        "holdout": {"portfolio": metrics(returns[returns.index >= holdout_start]),
                    "ic_mean": float(ic[ic.index >= holdout_start].mean()) if len(ic) else None},
    }


def list_validation_codes(market: Market) -> tuple:
    """Stocks with stored bars (scope) and the codes never traded: ETFs and the benchmark."""
    codes = market_store.list_bar_codes(market)
    master = market_store.list_symbols(market)
    excluded = {market.benchmark_symbol}
    if market.validation.get("filters", {}).get("exclude_etfs", True):
        excluded |= set(master.loc[master["is_etf"] == 1, "code"])
    return codes, excluded


def run_validation(market: Market, factor_names=None) -> dict:
    t0 = time.time()
    cfg = market.validation
    top_n, every = cfg.get("top_n", 20), cfg.get("rebalance_days", 40)
    codes, excluded = list_validation_codes(market)
    panel = build_panel(market, codes, start=market.history_start)
    panel["adv_20"] = compute_adv(panel)
    mask = build_eligibility(panel, cfg.get("filters") or {}, excluded)
    index = panel["close"].index
    forward = compute_forward_returns(panel, every)
    bench = panel["close"][market.benchmark_symbol].pct_change(fill_method=None).fillna(0)
    is_safe = market.providers.get("bars") == "eodhd"
    reliable_from = market.shares.get("reliable_from", cfg["start"])
    wanted = [f for f in cfg.get("factors") or [] if not factor_names or f["name"] in factor_names]
    grid = list_rebalance_dates(index, cfg["start"], every)

    results, scores_on_dates, excess = {}, {}, {}
    for spec in wanted:
        name, sign = spec["name"], spec.get("sign", 1)
        start = max(cfg["start"], reliable_from) if name in TURNOVER_FACTORS else cfg["start"]
        dates = list_rebalance_dates(index, start, every)
        score = compute_factor(name, panel) * sign
        ic = compute_rank_ic(score, forward, mask, dates)
        port = run_portfolio(market, panel, score, mask, dates, top_n, cfg.get("capital", 100000))
        summary = summarize_returns(port["returns"], bench)
        results[name] = {
            "sign": sign, "start": start,
            "ic": summarize_ic(ic),
            "portfolio": {**summary["metrics"], "excess_annual": summary["excess_annual"],
                          "total_cost": port["total_cost"], "annual_turnover": port["annual_turnover"]},
            "by_year": summary["by_year"], "excess_by_year": summary["excess_by_year"],
            **split_holdout(port["returns"], ic, cfg.get("holdout_start", "2023-01-01")),
            "capacity": compute_capacity(panel, port["picks"], cfg.get("participation", 0.05), top_n),
        }
        scores_on_dates[name] = score.loc[grid]
        excess[name] = port["returns"] - bench.reindex(port["returns"].index).fillna(0)

    apply_duplicate_gate(results, scores_on_dates, mask, grid)
    apply_orthogonality(results, excess)
    for name, result in results.items():
        result["grade"], result["reasons"] = resolve_grade(result, is_safe)
    report = {
        "market": market.id, "currency": market.currency, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "params": {**{k: v for k, v in cfg.items() if k != "factors"}, "universe_codes": len(codes)},
        "data_notes": build_data_notes(market, is_safe, reliable_from),
        "benchmark": {"code": market.benchmark_symbol, **compute_metrics(to_dated(bench.loc[cfg["start"]:]))},
        "factors": results,
        "elapsed_s": round(time.time() - t0, 1),
    }
    archive(market, report)
    return report


def to_dated(series: pd.Series) -> pd.Series:
    out = series.copy()
    out.index = pd.to_datetime(out.index)
    return out


def apply_duplicate_gate(results: dict, scores_on_dates: dict, mask: pd.DataFrame, dates: list):
    """Mark the weaker factor (lower |ICIR|) of each pair whose scores correlate ≥ DUPLICATE_CORR."""
    names = list(results)
    strength = {n: abs(results[n]["ic"]["icir"] or 0) for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            corr = compute_rank_ic(scores_on_dates[a], scores_on_dates[b], mask, dates).mean()
            if corr >= DUPLICATE_CORR:
                weaker, stronger = (a, b) if strength[a] < strength[b] else (b, a)
                results[weaker]["duplicate_of"] = stronger


def apply_orthogonality(results: dict, excess: dict):
    """Highest correlation of each factor's daily excess return with another factor's."""
    frame = pd.DataFrame(excess).dropna(how="all")
    corr = frame.corr()
    for name in results:
        others = corr[name].drop(name).dropna()
        results[name]["max_excess_corr"] = (
            {"with": others.abs().idxmax(), "corr": round(float(others[others.abs().idxmax()]), 3)}
            if len(others) else None)


def build_data_notes(market: Market, is_safe: bool, reliable_from: str) -> list:
    notes = [f"execution: signal at close, trade at next open; costs per config/markets.yaml ({market.currency})"]
    if not is_safe:
        notes.append(f"bars from {market.providers.get('bars')} have no delisted stocks: returns biased upward")
    notes.append(f"turnover factors validated from {reliable_from} (share-count history boundary)")
    return notes


def to_json_safe(value):
    """NaN/inf → None and numpy scalars → Python numbers, so browsers can parse the archive."""
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, (np.integer, np.floating)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def archive(market: Market, report: dict):
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(to_json_safe(report), ensure_ascii=False, indent=2)
    (ARCHIVE_DIR / f"{market.id.lower()}_{time.strftime('%Y%m%d_%H%M%S')}.json").write_text(text, encoding="utf-8")
    (ARCHIVE_DIR / f"latest_{market.id.lower()}.json").write_text(text, encoding="utf-8")


def get_latest_report(market_id: str):
    path = ARCHIVE_DIR / f"latest_{market_id.lower()}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="US")
    parser.add_argument("--factors", nargs="*", help="subset of validation.factors in config")
    args = parser.parse_args()
    report = run_validation(get_market(args.market), args.factors)
    rows = [(n, r["grade"], r["ic"]["mean"], r["portfolio"]["annual_return"], r["portfolio"]["sharpe"],
             r["portfolio"]["excess_annual"], "; ".join(r["reasons"])) for n, r in report["factors"].items()]
    print(pd.DataFrame(rows, columns=["factor", "grade", "ic", "annual", "sharpe", "excess", "reasons"])
          .round(3).to_string(index=False))
    print(f"archived: output/market_validation/latest_{report['market'].lower()}.json ({report['elapsed_s']}s)")


if __name__ == "__main__":
    main()
