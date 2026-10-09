# -*- coding: utf-8 -*-
"""US / Bursa Malaysia order desk: drafts → risk check → a person presses Send → moomoo → fills in the ledger.

Buy drafts come only from the buy list (approved cards, strategy/market_track.py) not yet held or ordered;
sell drafts only from exit alerts on held positions (strategy/market_positions.py). Nothing is sent
without a Send request, and preview mode (the default in config/broker.yaml) never sends.

output/market_desk/<market>_orders.json  every order sent or attempted, with its moomoo state
output/market_desk/<market>_broker.json  last account snapshot (cash, positions) used for checks and reconcile
logs/market_orders.log                   one JSON line per send, cancel, fill, sync and kill-switch change

Usage:
  python strategy/market_orders.py --market US          # drafts with risk checks
  python strategy/market_orders.py --market US --sync   # order states, fills, account, reconcile
"""
import argparse
import json
import math
import os
import sys
import time
import uuid
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from backtest.market_bt import compute_affordable_value  # noqa: E402
from backtest.market_rules import compute_limit_price, round_to_lot  # noqa: E402
from broker.moomoo_broker import CONFIG_PATHS, BrokerError, MoomooBroker, get_broker_config  # noqa: E402
from data.markets import Market, get_market, list_markets  # noqa: E402
from risk.risk_agent import Decision, RiskAgent  # noqa: E402
from strategy import market_positions, market_track  # noqa: E402

WORKING = ("sending", "working", "partial")
LOG_PATH = BASE / "logs" / "market_orders.log"
RISK_AUDIT_PATH = BASE / "logs" / "risk_audit.log"
# ledger accounts each moomoo environment owns: typed-in fills count as the real account
LEDGER_ACCOUNTS = {"paper": ("paper",), "live": ("live", "manual")}


def get_market_settings(settings: dict, market: Market) -> dict:
    return (settings.get("markets") or {}).get(market.id) or {}


def resolve_mode(settings: dict, market: Market) -> tuple:
    """(mode, note) for this market: preview, paper or live; paper where moomoo has none falls back to preview."""
    cfg = get_market_settings(settings, market)
    mode = cfg.get("mode") or settings.get("mode") or "preview"
    if mode not in ("preview", "paper", "live"):
        raise ValueError(f"mode must be preview, paper or live, not {mode!r} (config/broker.yaml)")
    if mode == "paper" and not cfg.get("has_paper", True):
        return "preview", f"moomoo has no paper trading for {market.id}: preview only until mode is live"
    return mode, ""


def resolve_env(mode: str) -> str:
    """moomoo environment a mode reads: preview syncs the real account read-only."""
    return "paper" if mode == "paper" else "live"


def get_kill_switch_path(settings: dict) -> Path:
    return BASE / settings.get("kill_switch_file", "output/market_desk/STOP_TRADING")


def is_kill_switch_on(settings: dict) -> bool:
    return get_kill_switch_path(settings).exists()


def append_log(market: Market, event: str, **fields):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "market": market.id, "event": event, **fields},
                           ensure_ascii=False) + "\n")


def list_orders(market: Market) -> list:
    return market_track.read_json(market_track.get_desk_path(market, "orders"), [])


def write_orders(market: Market, orders: list):
    market_track.write_json(market_track.get_desk_path(market, "orders"), orders)


def get_snapshot(market: Market, env: str):
    snapshot = market_track.read_json(market_track.get_desk_path(market, "broker"), None)
    return snapshot if snapshot and snapshot["env"] == env else None


def compute_order_qty(market: Market, shares: float) -> float:
    """Whole board lots, and whole shares where fractions are allowed (the API sends whole shares)."""
    return float(math.floor(round_to_lot(shares + 1e-9, market.rules.get("lot_size")) + 1e-9))


def build_draft(market: Market, cfg: dict, side: str, code: str, name, qty: float, ref_price: float, ref_date: str,
                source: str, reason: str = "", position_id: str = None) -> dict:
    limit = compute_limit_price(market.rules, ref_price, side, cfg.get("limit_buffer_pct", 0.01))
    return {"key": f"{side}:{code}", "side": side, "code": code, "name": name, "qty": qty, "ref_price": ref_price,
            "ref_date": ref_date, "limit_price": limit, "value": round(qty * limit, 2),
            "est_fees": market_positions.estimate_fees(market, side, qty * limit), "source": source,
            "reason": reason, "position_id": position_id}


def compute_sell_qty(market: Market, position: dict) -> float:
    """All of it, or half while the only alert is a take-profit below the upper band."""
    stop_cfg = market.positions.get("stop_rules") or {}
    marks, rules = position["marks"], {a["rule"] for a in position["alerts"]}
    full = compute_order_qty(market, marks["shares"])
    is_half = rules == {"take_profit"} and marks["pnl_pct"] < stop_cfg.get("profit_take_high", math.inf)
    return (compute_order_qty(market, marks["shares"] / 2) or full) if is_half else full


def build_drafts(market: Market, settings: dict, desk: dict, positions: list, orders: list) -> list:
    """Buy drafts for approved cards not held or ordered, sell drafts for held positions with exit alerts."""
    cfg = get_market_settings(settings, market)
    working = {(o["code"], o["side"]) for o in orders if o["status"] in WORKING}
    held = {p["code"] for p in positions}
    signal_date = (desk["cards"] or {}).get("signal_date")
    drafts = [build_draft(market, cfg, "buy", c["code"], c.get("name"), compute_order_qty(market, c["position"]["shares"]),
                          c["price"], signal_date, f"card {signal_date}")
              for c in desk["buy_list"] if c["code"] not in held and (c["code"], "buy") not in working]
    for p in positions:
        if p["alerts"] and p["marks"].get("last") is not None and (p["code"], "sell") not in working:
            reason = ", ".join(a["rule"] for a in p["alerts"])
            drafts.append(build_draft(market, cfg, "sell", p["code"], p.get("name"), compute_sell_qty(market, p),
                                      p["marks"]["last"], p["marks"]["last_date"], f"exit: {reason}", reason, p["id"]))
    return drafts


def build_risk_context(market: Market, settings: dict, env: str, snapshot, positions: list, orders: list) -> dict:
    """Equity, cash, holdings weights and today's sends: from the moomoo snapshot when there is one,
    else the pitch capital and the ledger marks."""
    account = (snapshot or {}).get("account") or {}
    equity = account.get("total_assets") or market.pitch.get("capital") or 0
    today = date.today().isoformat()
    sent = [o for o in orders if o["env"] == env and (o.get("sent_at") or "")[:10] == today and o["status"] != "failed"]
    return {
        "env": env, "today": today, "equity": equity, "power": account.get("power"),
        "equity_source": f"moomoo {env} account at {snapshot['as_of']}" if account.get("total_assets")
        else "pitch capital (no moomoo snapshot)",
        "sellable": {p["code"]: p["can_sell_qty"] for p in snapshot["positions"]} if snapshot else None,
        "weights": ({p["code"]: p["market_val"] / equity for p in snapshot["positions"]} if snapshot and equity else
                    {p["code"]: p["marks"]["value"] / equity for p in positions if p["marks"].get("value") and equity}),
        "orders_today": len(sent), "value_today": sum(o["qty"] * o["limit_price"] for o in sent if o["side"] == "buy"),
        "is_kill_switch_on": is_kill_switch_on(settings),
    }


def compute_risk(market: Market, settings: dict, draft: dict, context: dict, audit_log: Path = Path(os.devnull)) -> dict:
    """Blocking checks, then size caps that reduce the quantity, then risk/risk_agent.py on what is left.
    Order and daily caps limit buys; exits are only held to the shares moomoo says are sellable."""
    cfg = get_market_settings(settings, market)
    price, qty, side, cur = draft["limit_price"], draft["qty"], draft["side"], market.currency
    checks = []

    def add(name, is_ok, detail, effect="block"):
        checks.append({"name": name, "is_ok": bool(is_ok), "detail": detail, "effect": effect})

    add("kill switch", not context["is_kill_switch_on"], "on: nothing is sent" if context["is_kill_switch_on"] else "off")
    age, max_age = (date.fromisoformat(context["today"]) - date.fromisoformat(draft["ref_date"])).days, cfg.get(
        "max_price_age_days", 5)
    add("price age", age <= max_age, f"reference close {draft['ref_date']} is {age} days old (limit {max_age})")
    max_orders = cfg.get("max_orders_per_day")
    if max_orders:
        add("orders today", context["orders_today"] < max_orders, f"{context['orders_today']} of {max_orders} sent today")
    caps = []
    if side == "buy" and cfg.get("max_order_qty"):
        caps.append(("order size", cfg["max_order_qty"], f"at most {cfg['max_order_qty']:g} shares per order"))
    if side == "buy" and cfg.get("max_order_value"):
        caps.append(("order value", cfg["max_order_value"] / price, f"at most {cfg['max_order_value']:,.0f} {cur} per order"))
    if side == "buy" and cfg.get("max_daily_value"):
        left = max(cfg["max_daily_value"] - context["value_today"], 0)
        caps.append(("daily value", left / price, f"{left:,.0f} of {cfg['max_daily_value']:,.0f} {cur} left today"))
    if side == "buy" and context["power"] is not None:
        caps.append(("buying power", compute_affordable_value(market.costs, context["power"]) / price,
                     f"{context['power']:,.2f} {cur} buying power"))
    if side == "sell" and context["sellable"] is not None:
        sellable = context["sellable"].get(draft["code"], 0)
        caps.append(("sellable", sellable, f"{sellable:g} shares sellable at moomoo"))
    for name, cap, detail in caps:
        cap = compute_order_qty(market, cap)
        add(name, cap >= qty, detail, "reduce")
        qty = min(qty, cap)
    if side == "buy" and qty > 0 and context["equity"]:
        size = qty * price / context["equity"]
        result = RiskAgent(settings.get("risk") or {}, audit_log=audit_log).check_order(
            draft["code"], size, context["weights"], 0.0)
        if result.decision is Decision.REJECT:
            add("portfolio", False, f"{size:.1%} of equity refused by risk/risk_agent.py: {result.reason}")
        elif result.decision is Decision.REDUCE:
            add("portfolio", False, f"{size:.1%} of equity cut to {result.adjusted_size:.1%}: {result.reason}", "reduce")
            qty = min(qty, compute_order_qty(market, result.adjusted_size * context["equity"] / price))
        else:
            add("portfolio", True, f"{size:.1%} of equity ({context['equity_source']})")
    unit = market.rules.get("lot_size") or 1
    add("size", qty >= unit, f"{qty:g} shares, {qty * price:,.2f} {cur}" + ("" if qty >= unit else f" (below {unit:g})"))
    is_blocked = any(not c["is_ok"] for c in checks if c["effect"] == "block")
    decision = "reject" if is_blocked else "reduce" if qty < draft["qty"] else "approve"
    return {"decision": decision, "qty": qty, "value": round(qty * price, 2), "checks": checks}


def build_session(market: Market, settings: dict, env: str, orders: list, snapshot) -> tuple:
    """Ledger positions this environment owns, drafts and the risk context they are checked against."""
    book = market_positions.build_book(market)
    positions = [p for p in book["open"] if p.get("account", "manual") in LEDGER_ACCOUNTS[env]]
    drafts = build_drafts(market, settings, market_track.build_desk(market), positions, orders)
    return positions, drafts, build_risk_context(market, settings, env, snapshot, positions, orders)


def build_reconcile(market: Market, env: str, positions: list, broker_positions: list) -> list:
    """Shares per code in the ledger (this environment's accounts) against moomoo; mismatches first."""
    ledger, broker = {}, {p["code"]: p["qty"] for p in broker_positions}
    for p in positions:
        ledger[p["code"]] = ledger.get(p["code"], 0) + p["marks"]["shares"]
    rows = [{"code": code, "ledger": ledger.get(code, 0.0), "broker": broker.get(code, 0.0),
             "diff": broker.get(code, 0.0) - ledger.get(code, 0.0)} for code in sorted(set(ledger) | set(broker))]
    for row in rows:
        row["is_match"] = abs(row["diff"]) < 1e-6
    return sorted(rows, key=lambda r: r["is_match"])


def build_order_desk(market: Market, settings: dict = None) -> dict:
    """Everything the orders section shows; reads files only, never contacts moomoo."""
    settings = settings if settings is not None else get_broker_config()
    mode, note = resolve_mode(settings, market)
    env = resolve_env(mode)
    orders, snapshot = list_orders(market), get_snapshot(market, env)
    positions, drafts, context = build_session(market, settings, env, orders, snapshot)
    return {
        "market": market.id, "currency": market.currency, "provider": settings.get("provider", "moomoo"),
        "mode": mode, "env": env, "note": note, "has_config": CONFIG_PATHS[0].exists(),
        "is_kill_switch_on": context["is_kill_switch_on"], "limits": get_market_settings(settings, market),
        "context": {k: v for k, v in context.items() if k not in ("weights", "sellable")},
        "drafts": [{**d, "risk": compute_risk(market, settings, d, context)} for d in drafts],
        "orders": list(reversed(orders))[:50], "snapshot": snapshot,
        "reconcile": build_reconcile(market, env, positions, snapshot["positions"]) if snapshot else [],
    }


def handle_send(market: Market, key: str, qty: float = None, settings: dict = None) -> dict:
    """Re-draft from the cards and alerts, re-check against a fresh moomoo account, then place a DAY limit order."""
    settings = settings if settings is not None else get_broker_config()
    mode, note = resolve_mode(settings, market)
    if mode == "preview":
        raise ValueError(note or "preview mode: set mode: paper or live in config/broker.yaml to send orders")
    if is_kill_switch_on(settings):
        raise ValueError("kill switch is on: turn it off on the orders section to send")
    orders = list_orders(market)
    with MoomooBroker(market, settings, mode) as broker:
        snapshot = {"env": mode, "as_of": time.strftime("%Y-%m-%d %H:%M:%S"), "account": broker.get_account(),
                    "positions": broker.list_positions()}
        market_track.write_json(market_track.get_desk_path(market, "broker"), snapshot)
        _, drafts, context = build_session(market, settings, mode, orders, snapshot)
        draft = next((d for d in drafts if d["key"] == key), None)
        if draft is None:
            raise ValueError(f"no {key} draft: the card is not approved, the code is held, or an order is working")
        if qty is not None:
            if not 0 < float(qty) <= draft["qty"] or compute_order_qty(market, float(qty)) != float(qty):
                raise ValueError(f"quantity must be whole lots/shares between 1 and {draft['qty']:g}")
            draft = build_draft(market, get_market_settings(settings, market), draft["side"], draft["code"],
                                draft["name"], float(qty), draft["ref_price"], draft["ref_date"], draft["source"],
                                draft["reason"], draft["position_id"])
        risk = compute_risk(market, settings, draft, context, audit_log=RISK_AUDIT_PATH)
        if risk["decision"] == "reject":
            raise ValueError("risk check refused: " + "; ".join(c["detail"] for c in risk["checks"] if not c["is_ok"]))
        order = {**draft, "qty": risk["qty"], "value": risk["value"], "id": uuid.uuid4().hex[:12], "env": mode,
                 "status": "sending", "risk": risk, "sent_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "broker_order_id": None, "dealt_qty": 0.0, "dealt_avg_price": None, "message": ""}
        write_orders(market, orders + [order])
        try:
            order.update(broker_order_id=broker.place_order(order["code"], order["side"], order["qty"],
                                                            order["limit_price"], f"dsq {order['id']}"), status="working")
        except BrokerError as e:
            order.update(status="failed", message=str(e))
        write_orders(market, orders + [order])
    append_log(market, "send", order=order["id"], env=mode, code=order["code"], side=order["side"], qty=order["qty"],
               limit=order["limit_price"], status=order["status"], broker_order_id=order["broker_order_id"],
               message=order["message"])
    return order


def record_fill(market: Market, order: dict, fees):
    """Book a final order's fill in the holdings ledger once; sells close (part of) the drafted position."""
    trade_date = (order.get("updated_time") or "")[:10] or date.today().isoformat()
    try:
        if order["side"] == "buy":
            position = market_positions.create_position(
                market, order["code"], order["dealt_qty"], order["dealt_avg_price"], trade_date, fees,
                f"moomoo {order['env']} order {order['broker_order_id']}", account=order["env"])
        else:
            position = market_positions.close_position(market, order["position_id"], order["dealt_avg_price"],
                                                       trade_date, fees, order["reason"], shares=order["dealt_qty"])
        order["fill_position_id"] = position["id"]
    except ValueError as e:
        order["fill_error"] = str(e)
    append_log(market, "fill", order=order["id"], code=order["code"], side=order["side"], qty=order["dealt_qty"],
               price=order["dealt_avg_price"], position=order.get("fill_position_id"), error=order.get("fill_error"))


def update_order_state(market: Market, order: dict, state: dict, broker: MoomooBroker) -> bool:
    """Apply moomoo's state to the order; True when it just ended with a fill, which is then booked."""
    order.update({"broker_order_id": state["order_id"], **{k: v for k, v in state.items() if k != "order_id"}})
    if order["status"] in ("filled", "cancelled") and order["dealt_qty"] > 0:
        record_fill(market, order, broker.get_order_fees(order["broker_order_id"]))
        return True
    return False


def handle_sync(market: Market, settings: dict = None) -> dict:
    """Refresh working orders, book final fills, snapshot the account and reconcile it with the ledger."""
    settings = settings if settings is not None else get_broker_config()
    env = resolve_env(resolve_mode(settings, market)[0])
    orders, changed, fills = list_orders(market), [], []
    with MoomooBroker(market, settings, env) as broker:
        for order in orders:
            if order["env"] != env or order["status"] not in WORKING:
                continue
            state = broker.get_order(order["broker_order_id"], remark=f"dsq {order['id']}")
            if state is None:
                if order["status"] == "sending":
                    order.update(status="failed", message="moomoo has no order with this remark")
                    changed.append(order["id"])
                continue
            previous = (order["status"], order["dealt_qty"])
            if update_order_state(market, order, state, broker):
                fills.append(order["id"])
            if (order["status"], order["dealt_qty"]) != previous:
                changed.append(order["id"])
        snapshot = {"env": env, "as_of": time.strftime("%Y-%m-%d %H:%M:%S"), "account": broker.get_account(),
                    "positions": broker.list_positions()}
    write_orders(market, orders)
    market_track.write_json(market_track.get_desk_path(market, "broker"), snapshot)
    book = market_positions.build_book(market)
    positions = [p for p in book["open"] if p.get("account", "manual") in LEDGER_ACCOUNTS[env]]
    reconcile = build_reconcile(market, env, positions, snapshot["positions"])
    append_log(market, "sync", env=env, changed=changed, fills=fills,
               mismatches=[r["code"] for r in reconcile if not r["is_match"]])
    return {"env": env, "changed": changed, "fills": fills, "snapshot": snapshot, "reconcile": reconcile}


def handle_cancel(market: Market, order_id: str, settings: dict = None) -> dict:
    settings = settings if settings is not None else get_broker_config()
    orders = list_orders(market)
    order = next((o for o in orders if o["id"] == order_id), None)
    if order is None or order["status"] not in ("working", "partial"):
        raise ValueError(f"no working order {order_id}")
    with MoomooBroker(market, settings, order["env"]) as broker:
        broker.cancel_order(order["broker_order_id"])
        state = broker.get_order(order["broker_order_id"])
        if state:
            update_order_state(market, order, state, broker)
    write_orders(market, orders)
    append_log(market, "cancel", order=order_id, status=order["status"])
    return order


def update_kill_switch(is_on: bool, settings: dict = None) -> dict:
    """Engage or release the stop file; engaging also cancels this desk's working orders in every market."""
    settings = settings if settings is not None else get_broker_config()
    path, cancelled, errors = get_kill_switch_path(settings), [], []
    if is_on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"engaged {time.strftime('%Y-%m-%d %H:%M:%S')} from the orders section\n", encoding="utf-8")
        for market in list_markets().values():
            for order in [o for o in list_orders(market) if o["status"] in ("working", "partial")]:
                try:
                    cancelled.append(handle_cancel(market, order["id"], settings)["id"])
                except (BrokerError, ValueError) as e:
                    errors.append(f"{market.id} {order['code']}: {e}")
    else:
        path.unlink(missing_ok=True)
    for market in list_markets().values():
        append_log(market, "kill_switch", is_on=is_on, cancelled=cancelled, errors=errors)
    return {"is_kill_switch_on": is_on, "cancelled": cancelled, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="US")
    parser.add_argument("--sync", action="store_true", help="sync orders and the account (skipped in preview mode)")
    args = parser.parse_args()
    market = get_market(args.market)
    if args.sync:
        mode = resolve_mode(get_broker_config(), market)[0]
        if mode == "preview":
            print(f"{market.id}: preview mode, no moomoo sync")
            return
        try:
            result = handle_sync(market)
        except BrokerError as e:
            print(f"{market.id}: sync failed: {e}")
            return
        print(f"{market.id} {result['env']}: {len(result['changed'])} orders changed, {len(result['fills'])} fills booked")
        for row in result["reconcile"]:
            if not row["is_match"]:
                print(f"  mismatch {row['code']}: ledger {row['ledger']:g}, moomoo {row['broker']:g}")
        return
    desk = build_order_desk(market)
    print(f"{market.id} · mode {desk['mode']}{' (' + desk['note'] + ')' if desk['note'] else ''} · "
          f"kill switch {'ON' if desk['is_kill_switch_on'] else 'off'} · {len(desk['drafts'])} drafts")
    for d in desk["drafts"]:
        risk = d["risk"]
        print(f"  {d['side']:<4} {d['code']:<10} {risk['qty']:>8g} @ {d['limit_price']:<9g} {risk['decision']:<7} "
              f"{d['source']} | " + "; ".join(c["detail"] for c in risk["checks"] if not c["is_ok"]))


if __name__ == "__main__":
    main()
