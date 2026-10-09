# -*- coding: utf-8 -*-
"""Trading rules and costs for US / Bursa Malaysia, read from config/markets.yaml (rules, costs)."""
import math


def compute_trade_cost(costs: dict, side: str, value: float) -> float:
    """Cost of one order of `value` (market currency): commission, platform fee, clearing,
    stamp duty, sell-side regulatory fee and slippage."""
    if value <= 0:
        return 0.0
    commission = value * costs.get("commission_pct", 0) + costs.get("platform_fee", 0)
    clearing = min(value * costs.get("clearing_pct", 0), costs.get("clearing_max", math.inf))
    step = costs.get("stamp_duty_step")
    stamp_base = math.ceil(value / step) * step if step else value
    stamp = min(stamp_base * costs.get("stamp_duty_pct", 0), costs.get("stamp_duty_max", math.inf))
    sell_fee = value * costs.get("sell_fee_pct", 0) if side == "sell" else 0.0
    return commission + clearing + stamp + sell_fee + value * costs.get("slippage_pct", 0)


def round_to_lot(shares: float, lot_size) -> float:
    """Whole board lots; a missing lot size means fractional shares are allowed."""
    if not lot_size:
        return shares
    return math.floor(shares / lot_size) * lot_size


def is_buyable(rules: dict, open_price: float, preclose: float, price_level: float) -> bool:
    """False when the open sits at the upper daily limit (no sellers), or there is no open.

    open_price/preclose may be adjusted prices; price_level is the unadjusted price that
    decides which limit applies (e.g. Bursa: ±30%, or ±30 sen under RM1).
    """
    if not open_price or math.isnan(open_price):
        return False
    limit = rules.get("price_limit")
    if not limit or not preclose or math.isnan(preclose):
        return True
    below = price_level < limit.get("below_price", 0)
    up_pct = limit["abs"] / price_level if below else limit["pct"]
    return open_price < preclose * (1 + up_pct) * (1 - 1e-6)


def compute_limit_price(rules: dict, ref_price: float, side: str, buffer_pct: float) -> float:
    """Limit `buffer_pct` through the reference price, on the tick grid and never past the buffer:
    buys round down, sells round up. `tick_sizes` lists [from price, tick] pairs."""
    target = ref_price * (1 + buffer_pct if side == "buy" else 1 - buffer_pct)
    tick = next((t for start, t in reversed(rules.get("tick_sizes") or [[0, 0.01]]) if target >= start), 0.01)
    ticks = math.floor(target / tick + 1e-9) if side == "buy" else math.ceil(target / tick - 1e-9)
    return round(ticks * tick, 6)
