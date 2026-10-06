# -*- coding: utf-8 -*-
"""Portfolio backtest for US / Bursa Malaysia with the project's execution rules.

Signal at the close of day t, trade at the open of t+1; top-N equal weight; per-order costs,
board-lot rounding and the daily price limit from config/markets.yaml. Positions are tracked in
money terms: adjusted (qfq) prices move them, unadjusted prices size the lots.
"""
import math

import numpy as np
import pandas as pd

from backtest.market_rules import compute_trade_cost, is_buyable, round_to_lot
from data import market_store
from data.markets import Market

CHUNK = 500


def build_panel(market: Market, codes: list, start: str, end: str = None) -> dict:
    """Wide float frames (dates × codes): open/close/amount/turn (qfq) and raw_close (unadjusted)."""
    cache = market_store.get_bar_cache(market)
    fields = {"open": [], "close": [], "amount": [], "turn": [], "split_close": []}
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        qfq = cache.get_daily_batch(chunk, start=start, end=end, adjust="qfq",
                                    fields=["open", "close", "amount", "turn"])
        split = cache.get_daily_batch(chunk, start=start, end=end, adjust="split", fields=["close"])
        for name in ("open", "close", "amount", "turn"):
            fields[name].append(pd.DataFrame({c: df.set_index("date")[name] for c, df in qfq.items()}))
        fields["split_close"].append(pd.DataFrame({c: df.set_index("date")["close"] for c, df in split.items()}))
    panel = {name: pd.concat(parts, axis=1).sort_index().astype("float64") for name, parts in fields.items()}
    index = panel["close"].index
    panel = {name: frame.reindex(index) for name, frame in panel.items()}
    panel["raw_close"] = build_raw_close(market, panel.pop("split_close"))
    return panel


def build_raw_close(market: Market, split_close: pd.DataFrame) -> pd.DataFrame:
    """Undo split adjustment: multiply by every split ratio after each date."""
    raw = split_close.copy()
    for code in split_close.columns:
        for row in market_store.list_splits(market, code).itertuples():
            raw.loc[raw.index < row.date, code] *= row.ratio
    return raw


def compute_adv(panel: dict) -> pd.DataFrame:
    """20-day median traded value (market currency)."""
    return panel["amount"].rolling(20, min_periods=15).median()


def build_eligibility(panel: dict, filters: dict, excluded: set) -> pd.DataFrame:
    """Tradable universe per day: price, 20-day median traded value and listing-age floors."""
    liquid = panel["adv_20"] >= filters.get("min_dollar_volume", 0)
    priced = panel["raw_close"] >= filters.get("min_price", 0)
    seasoned = panel["close"].notna().cumsum() >= filters.get("min_history_days", 0)
    mask = liquid & priced & seasoned
    mask.loc[:, [c for c in mask.columns if c in excluded]] = False
    return mask


def compute_forward_returns(panel: dict, horizon: int) -> pd.DataFrame:
    """Return from the open after day t to the open `horizon` days later (what a T+1 trader earns)."""
    open_ = panel["open"]
    return open_.shift(-1 - horizon) / open_.shift(-1) - 1


def compute_rank_ic(score: pd.DataFrame, target: pd.DataFrame, mask: pd.DataFrame, dates) -> pd.Series:
    """Spearman correlation across codes of score vs target on each date (needs ≥ 20 names)."""
    values = {}
    for d in dates:
        ok = mask.loc[d] & score.loc[d].notna() & target.loc[d].notna()
        if ok.sum() < 20:
            continue
        values[d] = score.loc[d, ok].rank().corr(target.loc[d, ok].rank())
    return pd.Series(values, dtype=float)


def list_rebalance_dates(index, start: str, every: int) -> list:
    first = int(np.searchsorted(index, start))
    return list(index[first:-1:every])


def compute_affordable_value(costs: dict, budget: float) -> float:
    """Largest order value whose percentage costs and per-order fee fit inside `budget`."""
    pct = sum(costs.get(k, 0) for k in ("commission_pct", "clearing_pct", "stamp_duty_pct", "slippage_pct"))
    return max(budget - costs.get("platform_fee", 0), 0) / (1 + pct)


def run_portfolio(market: Market, panel: dict, score: pd.DataFrame, mask: pd.DataFrame,
                  rebalance_dates: list, top_n: int, capital: float) -> dict:
    """Simulate the top-N strategy; returns daily returns, costs, turnover and the pick history.

    A position is held as units of the adjusted price and valued at its last traded close,
    so halted days carry the position and the gap return lands when trading resumes.
    """
    close, open_, raw = panel["close"], panel["open"], panel["raw_close"]
    rules, costs = market.rules, market.costs
    signal_days = set(rebalance_dates)
    index = close.index
    start = index.get_loc(rebalance_dates[0])
    cash, units, last_close = float(capital), {}, {}
    equity = pd.Series(np.nan, index=index[start:])
    equity.iloc[0] = capital
    total_cost = traded = 0.0
    picks_log = {}

    def trade(code, value, side):
        nonlocal cash, total_cost, traded
        cost = compute_trade_cost(costs, side, value)
        cash += value - cost if side == "sell" else -(value + cost)
        units[code] = units.get(code, 0.0) + (value if side == "buy" else -value) / open_.at[today, code]
        total_cost, traded = total_cost + cost, traded + value

    for i in range(start + 1, len(index)):
        prev, today = index[i - 1], index[i]
        if prev in signal_days:
            eligible = score.loc[prev][mask.loc[prev]].dropna()
            picks = list(eligible.nlargest(top_n).index)
            picks_log[prev] = picks
            tradable = {c for c in set(units) | set(picks) if open_.at[today, c] > 0}
            for code in [c for c in units if c not in picks and c in tradable]:
                trade(code, units[code] * open_.at[today, code], "sell")
                del units[code], last_close[code]
            value_now = {c: u * (open_.at[today, c] if c in tradable else last_close[c]) for c, u in units.items()}
            target = (cash + sum(value_now.values())) / top_n
            for code in picks:
                held = value_now.get(code, 0.0)
                if code not in tradable:
                    continue
                if held > target * 1.05:
                    trade(code, held - target, "sell")
                elif held < target * 0.95 and is_buyable(rules, open_.at[today, code], close.at[prev, code], raw.at[prev, code]):
                    raw_open = open_.at[today, code] * raw.at[prev, code] / close.at[prev, code]
                    budget = compute_affordable_value(costs, min(target - held, cash))
                    value = round_to_lot(budget / raw_open, rules.get("lot_size")) * raw_open
                    if value > 0:
                        trade(code, value, "buy")
                        last_close.setdefault(code, open_.at[today, code])
        for code in units:
            price = close.at[today, code]
            if price > 0:
                last_close[code] = price
        equity[today] = cash + sum(u * last_close[c] for c, u in units.items())
    returns = equity.pct_change().dropna()
    years = max(len(returns) / 252, 1e-9)
    return {
        "returns": returns,
        "total_cost": total_cost,
        "annual_turnover": traded / equity.mean() / years,
        "picks": picks_log,
    }
