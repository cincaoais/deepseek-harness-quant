# -*- coding: utf-8 -*-
"""Price/volume factors for US / Bursa Malaysia panels (dates × codes), market-agnostic.

Close-only factors reuse factors/factor_engine.FACTOR_FUNCS; the rest need volume data.
Raw values: the direction (sign) to trade them comes from config/markets.yaml → validation.factors.
"""
import pandas as pd

from factors.factor_engine import FACTOR_FUNCS


def compute_turn_20(panel: dict) -> pd.DataFrame:
    """20-day mean daily turnover (%)."""
    return panel["turn"].rolling(20, min_periods=15).mean()


def compute_mom_12_1(panel: dict) -> pd.DataFrame:
    """12-month return skipping the latest month."""
    close = panel["close"]
    return close.shift(21) / close.shift(252) - 1


def compute_amihud_20(panel: dict) -> pd.DataFrame:
    """Amihud illiquidity: mean |daily return| per unit of traded value, scaled to per million."""
    ret = panel["close"].pct_change(fill_method=None).abs()
    return (ret / panel["amount"] * 1e6).rolling(20, min_periods=15).mean()


def compute_max_ret_20(panel: dict) -> pd.DataFrame:
    """Largest single-day return over 20 days (lottery demand)."""
    return panel["close"].pct_change(fill_method=None).rolling(20, min_periods=15).max()


PANEL_FACTORS = {
    "turn_20": compute_turn_20,
    "mom_12_1": compute_mom_12_1,
    "amihud_20": compute_amihud_20,
    "max_ret_20": compute_max_ret_20,
}
FACTOR_NAMES = tuple(PANEL_FACTORS) + tuple(FACTOR_FUNCS)
TURNOVER_FACTORS = {"turn_20"}


def compute_factor(name: str, panel: dict) -> pd.DataFrame:
    if name in PANEL_FACTORS:
        return PANEL_FACTORS[name](panel)
    if name in FACTOR_FUNCS:
        return FACTOR_FUNCS[name](panel["close"])
    raise KeyError(f"unknown factor {name!r}; available: {FACTOR_NAMES}")
