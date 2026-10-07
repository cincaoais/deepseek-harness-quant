# -*- coding: utf-8 -*-
"""Price/volume factors for US / Bursa Malaysia panels (dates × codes), market-agnostic.

Close-only factors reuse factors/factor_engine.FACTOR_FUNCS; the rest need volume data, and the
fundamental ones need panel fields equity / assets / net_income (SEC frames, backtest.market_bt).
Raw values: the direction (sign) to trade them comes from config/markets.yaml → validation.factors.
"""
import numpy as np
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


def compute_market_cap(panel: dict) -> pd.DataFrame:
    """Market cap from traded value and turnover (value / turnover% = shares × price), 20-day median."""
    cap = (panel["amount"] * 100 / panel["turn"]).where(panel["turn"] > 0).replace(np.inf, np.nan)
    return cap.rolling(20, min_periods=5).median()


def compute_book_to_price(panel: dict) -> pd.DataFrame:
    return panel["equity"] / compute_market_cap(panel)


def compute_earnings_yield(panel: dict) -> pd.DataFrame:
    return panel["net_income"] / compute_market_cap(panel)


def compute_roa(panel: dict) -> pd.DataFrame:
    return panel["net_income"] / panel["assets"].where(panel["assets"] > 0)


def compute_asset_growth(panel: dict) -> pd.DataFrame:
    """Total assets vs one year (252 sessions) earlier."""
    assets = panel["assets"].where(panel["assets"] > 0)
    return assets / assets.shift(252) - 1


PANEL_FACTORS = {
    "turn_20": compute_turn_20,
    "mom_12_1": compute_mom_12_1,
    "amihud_20": compute_amihud_20,
    "max_ret_20": compute_max_ret_20,
    "book_to_price": compute_book_to_price,
    "earnings_yield": compute_earnings_yield,
    "roa": compute_roa,
    "asset_growth": compute_asset_growth,
}
FACTOR_NAMES = tuple(PANEL_FACTORS) + tuple(FACTOR_FUNCS)
# factors that read turnover, directly or through the market cap derived from it
TURNOVER_FACTORS = {"turn_20", "book_to_price", "earnings_yield"}
FUNDAMENTAL_FACTORS = {"book_to_price", "earnings_yield", "roa", "asset_growth"}


def compute_factor(name: str, panel: dict) -> pd.DataFrame:
    if name in PANEL_FACTORS:
        return PANEL_FACTORS[name](panel)
    if name in FACTOR_FUNCS:
        return FACTOR_FUNCS[name](panel["close"])
    raise KeyError(f"unknown factor {name!r}; available: {FACTOR_NAMES}")
