# -*- coding: utf-8 -*-
"""US / Bursa Malaysia decision cards: composite factor score → top-N cards with sizing, stop and evidence.

The score is the weighted mean of each factor's percentile rank among tradable stocks (same
universe filters as validation). Factors graded "invalid" by the latest validation are dropped.
Cards feed the /pitch "US / MY" tab; approved cards become the buy list (strategy/market_track.py).

Usage:
  python strategy/market_pitch.py --market US
"""
import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from backtest.market_bt import build_eligibility, build_panel, compute_adv, compute_affordable_value  # noqa: E402
from backtest.market_rules import compute_trade_cost, round_to_lot  # noqa: E402
from backtest.market_validation import get_latest_report, list_validation_codes  # noqa: E402
from data import market_store  # noqa: E402
from data.markets import Market, get_market  # noqa: E402
from factors.market_factors import TURNOVER_FACTORS, compute_factor  # noqa: E402
from strategy import market_track  # noqa: E402
from strategy.timing import RegimeDetector  # noqa: E402

# enough sessions for 252-day factors plus the one-year listing-age filter
LOOKBACK_DAYS = 600


def resolve_factors(market: Market, report) -> tuple:
    """Configured factors with their validation evidence, minus those graded invalid."""
    graded = (report or {}).get("factors", {})
    used, skipped = [], []
    for spec in market.pitch.get("factors") or []:
        result = graded.get(spec["name"])
        evidence = {"grade": result["grade"], "annual": result["portfolio"].get("annual_return"),
                    "sharpe": result["portfolio"].get("sharpe"), "ic": result["ic"].get("mean"),
                    "holdout_annual": result["holdout"]["portfolio"].get("annual_return")} if result else None
        (skipped if evidence and evidence["grade"] == "invalid" else used).append({**spec, "evidence": evidence})
    return used, skipped


def compute_scores(panel: dict, mask_today: pd.Series, factors: list) -> pd.DataFrame:
    """Per-factor percentile ranks among today's tradable stocks and the weighted composite (0–100)."""
    ranks = {}
    for spec in factors:
        today = compute_factor(spec["name"], panel).iloc[-1] * spec.get("sign", 1)
        ranks[spec["name"]] = today[mask_today].rank(pct=True)
    frame = pd.DataFrame(ranks).dropna()
    weights = pd.Series({f["name"]: f.get("weight", 1) for f in factors})
    frame["score"] = (frame[weights.index] * weights).sum(axis=1) / weights.sum() * 100
    return frame.sort_values("score", ascending=False)


def build_position(market: Market, price: float) -> dict:
    """Equal slice of the configured capital, lot-rounded at the latest unadjusted close."""
    cfg = market.pitch
    budget = compute_affordable_value(market.costs, cfg.get("capital", 0) / max(cfg.get("positions", 1), 1))
    shares = round_to_lot(budget / price, market.rules.get("lot_size")) if price > 0 else 0
    value = shares * price
    return {"shares": round(shares, 4), "value": round(value, 2),
            "est_cost": round(compute_trade_cost(market.costs, "buy", value), 2)}


def compute_regime(market: Market) -> dict:
    """L0 regime of the market benchmark (strategy/timing.py state machine over the last two years)."""
    start = (date.today() - timedelta(days=1100)).isoformat()
    bars = market_store.get_bar_cache(market).get_daily(market.benchmark_symbol, start=start, adjust="qfq")
    if bars is None or len(bars) < 260:
        return {"state": None, "note": "not enough benchmark history"}
    detector = RegimeDetector()
    for end in range(250, len(bars) + 1):
        detector.update(bars.iloc[:end])
    return {**detector.health_report(), "benchmark": market.benchmark_symbol, "as_of": bars["date"].iloc[-1]}


def build_cards(market: Market) -> dict:
    report = get_latest_report(market.id)
    factors, skipped = resolve_factors(market, report)
    codes, excluded = list_validation_codes(market)
    start = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    panel = build_panel(market, codes, start=start)
    panel["adv_20"] = compute_adv(panel)
    mask = build_eligibility(panel, market.validation.get("filters") or {}, excluded)
    signal_date = panel["close"].index[-1]
    scores = compute_scores(panel, mask.iloc[-1], factors).head(market.pitch.get("top_n", 10))
    names = market_store.list_symbols(market).set_index("code")["name"].to_dict()
    stop_pct = market.positions.get("stop_rules", {}).get("stop_loss_pct", 0.15)
    cards = []
    for rank, (code, row) in enumerate(scores.iterrows(), start=1):
        price = float(panel["raw_close"].at[signal_date, code])
        cards.append({
            "rank": rank, "code": code, "name": names.get(code), "score": round(float(row["score"]), 1),
            "factors": {f["name"]: round(float(row[f["name"]]) * 100, 1) for f in factors},
            "price": round(price, 4), "adv_20": round(float(panel["adv_20"].at[signal_date, code]), 0),
            "position": build_position(market, price),
            "stop": {"pct": stop_pct, "price": round(price * (1 - stop_pct), 4)},
        })
    warnings = []
    if market.providers.get("bars") != "eodhd":
        warnings.append("prices exclude delisted stocks, so the validation evidence is optimistic")
    if any(f["name"] in TURNOVER_FACTORS for f in factors):
        warnings.append(f"turnover is validated only from {market.shares.get('reliable_from')} "
                        "(share-count history boundary)")
    if report is None:
        warnings.append("no validation report yet: run backtest/market_validation.py first")
    return {
        "market": market.id, "currency": market.currency, "signal_date": signal_date,
        "execute": "next session open", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "regime": compute_regime(market),
        "factors": factors, "skipped_factors": skipped,
        "eligible": int(mask.iloc[-1].sum()), "scored": int(len(scores)),
        "warnings": warnings, "cards": cards,
    }


def update_cards(market: Market) -> dict:
    """Build today's cards, save them as the latest set and add them to the forward pool."""
    cards = build_cards(market)
    market_track.write_json(market_track.get_desk_path(market, "cards"), cards)
    market_track.write_json(market_track.get_desk_path(market, f"cards_{cards['signal_date']}"), cards)
    cards["tracked"] = market_track.append_track(market, cards)
    cards["forward_updated"] = market_track.update_forward(market)
    return cards


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="US")
    args = parser.parse_args()
    cards = update_cards(get_market(args.market))
    print(f"{cards['market']} cards for {cards['signal_date']} · regime {cards['regime'].get('state')} "
          f"· {cards['eligible']} tradable · factors {[f['name'] for f in cards['factors']]}")
    for c in cards["cards"]:
        print(f"  {c['rank']:>2} {c['code']:<10} {str(c['name'] or '')[:30]:<30} score {c['score']:>5} "
              f"price {c['price']:<9} shares {c['position']['shares']:<8} stop {c['stop']['price']}")


if __name__ == "__main__":
    main()
