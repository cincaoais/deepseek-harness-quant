# -*- coding: utf-8 -*-
"""US / Bursa Malaysia holdings ledger and daily exit checks, kept apart from the A-share portfolio.

output/market_desk/<market>_positions.json holds one row per position (open or closed).
Prices are unadjusted; a split after entry restates entry price and shares on today's basis.
Dividends are not booked yet.

Exit checks: risk/stop_rules.py (hard stop, drawdown from the peak close, take-profit) plus the
rule the validation tested: after `rebalance_days` sessions a stock no longer on the cards is sold.

Usage:
  python strategy/market_positions.py --market US
"""
import argparse
import sys
import time
import uuid
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from backtest.market_rules import compute_trade_cost  # noqa: E402
from data import market_store  # noqa: E402
from data.markets import Market, get_market  # noqa: E402
from risk.stop_rules import StopRules, StopType  # noqa: E402
from strategy import market_track  # noqa: E402

EXIT_DETAILS = {
    StopType.HARD_LOSS: lambda m, cfg: f"loss {m['pnl_pct']:.1%} reached the {cfg['stop_loss_pct']:.0%} stop",
    StopType.TRAILING_HIGH: lambda m, cfg: (f"{m['last'] / m['peak'] - 1:.1%} from the peak close {m['peak']:g} "
                                            f"(limit {cfg['high_drawdown_exit']:.0%})"),
    StopType.TAKE_PROFIT: lambda m, cfg: f"gain {m['pnl_pct']:.1%} (sell half from {cfg['profit_take_low']:.0%}, "
                                         f"all from {cfg['profit_take_high']:.0%})",
}


def estimate_fees(market: Market, side: str, value: float) -> float:
    """Broker and exchange fees for a fill, without the backtest's slippage allowance."""
    costs = {k: v for k, v in market.costs.items() if k != "slippage_pct"}
    return round(compute_trade_cost(costs, side, value), 2)


def list_positions(market: Market) -> list:
    return market_track.read_json(market_track.get_desk_path(market, "positions"), [])


def write_positions(market: Market, positions: list):
    market_track.write_json(market_track.get_desk_path(market, "positions"), positions)


def resolve_code(market: Market, code: str) -> str:
    """Accept "AAPL" or "AAPL.US"."""
    code = str(code).strip().upper()
    return code if code.endswith(f".{market.id}") else market.to_canonical(code)


def create_position(market: Market, code: str, shares: float, price: float, trade_date: str = None,
                    fees: float = None, note: str = "", account: str = "manual") -> dict:
    """Record a buy fill; the source is the card date when the code was an approved card.
    `account` is manual (typed in), live or paper (moomoo fills from strategy/market_orders.py)."""
    shares, price = float(shares), float(price)
    if shares <= 0 or price <= 0:
        raise ValueError("shares and price must be positive")
    code = resolve_code(market, code)
    approved = [d["card_date"] for d in market_track.list_decisions(market) if d["code"] == code and d["action"] == "buy"]
    names = market_store.list_symbols(market).set_index("code")["name"].to_dict()
    position = {
        "id": uuid.uuid4().hex[:8], "code": code, "name": names.get(code), "shares": shares,
        "entry_price": price, "entry_date": trade_date or date.today().isoformat(),
        "entry_fees": round(float(fees), 2) if fees is not None else estimate_fees(market, "buy", shares * price),
        "source": f"card {max(approved)}" if approved else "manual", "note": note, "account": account, "status": "open",
        "exit_price": None, "exit_date": None, "exit_fees": None, "exit_reason": None,
        "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    write_positions(market, list_positions(market) + [position])
    return position


def get_position(positions: list, position_id: str) -> dict:
    match = [p for p in positions if p["id"] == position_id]
    if not match:
        raise ValueError(f"no position {position_id}")
    return match[0]


def close_position(market: Market, position_id: str, price: float, trade_date: str = None,
                   fees: float = None, reason: str = "manual", shares: float = None) -> dict:
    """Record a sell fill; `shares` (today's split basis) below the holding closes that part and
    leaves the rest open under the same id, with entry fees split pro rata."""
    positions = list_positions(market)
    position = get_position(positions, position_id)
    if position["status"] != "open":
        raise ValueError(f"position {position_id} is already closed")
    if float(price) <= 0:
        raise ValueError("price must be positive")
    exit_date = trade_date or date.today().isoformat()
    factor = compute_split_factor(market, position["code"], position["entry_date"], exit_date)
    held = position["shares"] * factor
    if shares is not None and float(shares) < held - 1e-9:
        part = float(shares) / held
        rest = {**position, "shares": position["shares"] * (1 - part),
                "entry_fees": round(position["entry_fees"] * (1 - part), 2)}
        position.update(id=uuid.uuid4().hex[:8], shares=position["shares"] * part,
                        entry_fees=round(position["entry_fees"] * part, 2))
        positions.append(rest)
        held = float(shares)
    position.update(status="closed", exit_price=float(price), exit_date=exit_date, exit_reason=reason,
                    exit_fees=round(float(fees), 2) if fees is not None
                    else estimate_fees(market, "sell", held * float(price)))
    write_positions(market, positions)
    return position


def delete_position(market: Market, position_id: str) -> dict:
    """Remove a mistaken entry."""
    positions = list_positions(market)
    position = get_position(positions, position_id)
    write_positions(market, [p for p in positions if p["id"] != position_id])
    return position


def compute_split_factor(market: Market, code: str, after: str, through: str = "9999-12-31") -> float:
    """Product of split ratios dated after `after` up to `through` (4.0 for one 4-for-1 split)."""
    splits = market_store.list_splits(market, code)
    window = splits[(splits["date"] > after) & (splits["date"] <= through)]
    return float(window["ratio"].prod()) if len(window) else 1.0


def build_marks(market: Market, position: dict, cache) -> dict:
    """Latest close, value and P&L with entry price and shares restated on today's split basis."""
    factor = compute_split_factor(market, position["code"], position["entry_date"])
    entry, shares = position["entry_price"] / factor, position["shares"] * factor
    bars = cache.get_daily(position["code"], start=position["entry_date"], adjust="split")
    if bars is None or bars.empty:
        return {"entry": entry, "shares": shares, "last": None}
    last = float(bars["close"].iloc[-1])
    return {
        "entry": entry, "shares": shares, "last": last, "last_date": bars["date"].iloc[-1],
        "peak": float(bars["close"].max()), "sessions_held": int((bars["date"] > position["entry_date"]).sum()),
        "value": shares * last, "pnl": shares * (last - entry) - position["entry_fees"], "pnl_pct": last / entry - 1,
    }


def list_exit_alerts(market: Market, marks: dict, is_on_cards: bool) -> list:
    """Sell alerts for one position: configured stop rules plus the validated rebalance exit."""
    if marks.get("last") is None:
        return []
    cfg = market.positions.get("stop_rules") or {}
    decision = StopRules(cfg).check(entry_price=marks["entry"], current_price=marks["last"],
                                    peak_price=marks["peak"] if "high_drawdown_exit" in cfg else None)
    alerts = [{"rule": kind.value, "detail": EXIT_DETAILS[kind](marks, cfg)}
              for kind, _ in decision.reasons if kind in EXIT_DETAILS]
    rebalance_days = market.validation.get("rebalance_days", 40)
    if marks["sessions_held"] >= rebalance_days and not is_on_cards:
        alerts.append({"rule": "rebalance", "detail": f"held {marks['sessions_held']} sessions and no longer on the cards"})
    return alerts


def build_book(market: Market) -> dict:
    """Open positions with marks and exit alerts, closed positions with realized P&L, and totals."""
    cache = market_store.get_bar_cache(market)
    cards = market_track.get_latest_cards(market)
    on_cards = {c["code"] for c in (cards or {}).get("cards", [])}
    open_rows, closed_rows = [], []
    for position in list_positions(market):
        if position["status"] == "open":
            marks = build_marks(market, position, cache)
            open_rows.append({**position, "marks": marks, "alerts": list_exit_alerts(market, marks, position["code"] in on_cards)})
        else:
            shares = position["shares"] * compute_split_factor(market, position["code"], position["entry_date"],
                                                               position["exit_date"])
            proceeds = shares * position["exit_price"] - position["exit_fees"]
            cost = position["shares"] * position["entry_price"] + position["entry_fees"]
            closed_rows.append({**position, "realized": proceeds - cost, "realized_pct": proceeds / cost - 1})
    marked = [p for p in open_rows if p["marks"].get("last") is not None]
    return {
        "market": market.id, "currency": market.currency,
        "open": open_rows, "closed": closed_rows,
        "summary": {
            "open": len(open_rows), "slots": market.pitch.get("positions"),
            "cost": sum(p["shares"] * p["entry_price"] + p["entry_fees"] for p in open_rows),
            "value": sum(p["marks"]["value"] for p in marked),
            "unrealized": sum(p["marks"]["pnl"] for p in marked),
            "realized": sum(p["realized"] for p in closed_rows),
            "alerts": sum(len(p["alerts"]) for p in open_rows),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="US")
    book = build_book(get_market(parser.parse_args().market))
    s = book["summary"]
    print(f"{book['market']} · {s['open']}/{s['slots']} open · value {s['value']:,.2f} {book['currency']} · "
          f"unrealized {s['unrealized']:+,.2f} · realized {s['realized']:+,.2f} · alerts {s['alerts']}")
    for p in book["open"]:
        m = p["marks"]
        print(f"  {p['code']:<10} {m['shares']:>10g} @ {m['entry']:<9.4g} last {m.get('last')} "
              f"pnl {m.get('pnl_pct', 0):+.1%} {' | '.join(a['rule'] + ': ' + a['detail'] for a in p['alerts'])}")


if __name__ == "__main__":
    main()
