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
