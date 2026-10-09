# -*- coding: utf-8 -*-
"""moomoo OpenAPI connection for US / Bursa Malaysia orders, through OpenD on this PC.

OpenD (moomoo's gateway app) holds the login; this module talks to it with the moomoo-api SDK.
Settings come from config/broker.yaml (template: config/broker.yaml.example).
The SDK reply shape is (RET_OK, DataFrame); any other return code raises BrokerError.
"""
import socket
from pathlib import Path

import yaml

from data.markets import Market
from data.providers.base import get_credential

BASE = Path(__file__).resolve().parent.parent
CONFIG_PATHS = (BASE / "config" / "broker.yaml", BASE / "config" / "broker.yaml.example")
TRD_ENVS = {"paper": "SIMULATE", "live": "REAL"}
# moomoo order states → desk states; working orders keep being synced, the rest are final
ORDER_STATES = {
    "UNSUBMITTED": "working", "WAITING_SUBMIT": "working", "SUBMITTING": "working", "SUBMITTED": "working",
    "CANCELLING_PART": "working", "CANCELLING_ALL": "working", "FILLED_PART": "partial",
    "FILLED_ALL": "filled", "CANCELLED_PART": "cancelled", "CANCELLED_ALL": "cancelled", "DELETED": "cancelled",
    "FILL_CANCELLED": "cancelled", "SUBMIT_FAILED": "failed", "FAILED": "failed", "TIMEOUT": "failed",
    "DISABLED": "failed",
}


class BrokerError(Exception):
    pass


def get_broker_config(paths=CONFIG_PATHS) -> dict:
    path = next((p for p in paths if Path(p).exists()), None)
    return (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}) if path else {}


def to_broker_code(market: Market, code: str) -> str:
    """AAPL.US → US.AAPL, BRK.B.US → US.BRK.B, 1155.MY → MY.1155."""
    return f"{market.id}.{code[: -len(market.id) - 1]}"


def from_broker_code(market: Market, code: str) -> str:
    return f"{code.split('.', 1)[1]}.{market.id}"


def create_context(market: Market, opend: dict):
    """Open an SDK trade context; OpenD is probed first because the SDK retries a dead port forever."""
    host, port = opend.get("host", "127.0.0.1"), int(opend.get("port", 11111))
    try:
        socket.create_connection((host, port), timeout=opend.get("connect_timeout_s", 3)).close()
    except OSError as e:
        raise BrokerError(f"OpenD is not reachable at {host}:{port} ({e}); start OpenD and log in") from e
    from moomoo import OpenSecTradeContext

    return OpenSecTradeContext(filter_trdmarket=market.id, host=host, port=port, is_encrypt=opend.get("is_encrypt"),
                               security_firm=opend.get("security_firm", "FUTUMY"))


def call(fn, *args, **kwargs):
    ret, data = fn(*args, **kwargs)
    if ret != 0:
        raise BrokerError(f"{fn.__name__}: {data}")
    return data


class MoomooBroker:
    """One market and one environment (paper or live) of the moomoo account; use as a context manager."""

    def __init__(self, market: Market, settings: dict, env: str):
        if env not in TRD_ENVS:
            raise BrokerError(f"environment must be paper or live, not {env!r}")
        self.market, self.settings, self.env = market, settings, env
        self.trd_env = TRD_ENVS[env]
        self.ctx, self.acc_id = None, 0

    def __enter__(self):
        self.ctx = create_context(self.market, self.settings.get("opend") or {})
        try:
            self.acc_id = self.resolve_account()
        except Exception:
            self.ctx.close()
            raise
        return self

    def __exit__(self, *exc):
        self.ctx.close()

    def query(self, name: str, *args, **kwargs):
        return call(getattr(self.ctx, name), *args, trd_env=self.trd_env, acc_id=self.acc_id, **kwargs)

    def resolve_account(self) -> int:
        """The configured acc_id, else the first active account of this environment cleared for the market."""
        pinned = (((self.settings.get("markets") or {}).get(self.market.id) or {}).get("acc_id") or {}).get(self.env)
        accounts = call(self.ctx.get_acc_list)
        rows = accounts[(accounts["trd_env"] == self.trd_env)
                        & accounts["trdmarket_auth"].apply(lambda auth: self.market.id in auth)]
        if "acc_status" in rows:
            rows = rows[rows["acc_status"] != "DISABLED"]
        if pinned:
            rows = rows[rows["acc_id"] == int(pinned)]
        if rows.empty:
            raise BrokerError(f"no {self.env} moomoo account cleared for {self.market.id}"
                              + (f" with acc_id {pinned}" if pinned else ""))
        return int(rows["acc_id"].iloc[0])

    def unlock(self):
        """Unlock live trading with the configured password; without one OpenD must already be unlocked."""
        if self.env != "live":
            return
        password = get_credential(self.settings.get("password_env", "MOOMOO_TRADE_PASSWORD"))
        if password:
            call(self.ctx.unlock_trade, password=password)

    def get_account(self) -> dict:
        row = self.query("accinfo_query", refresh_cache=True, currency=self.market.currency).iloc[0]
        return {k: float(row[k]) for k in ("total_assets", "cash", "power", "market_val", "frozen_cash") if k in row} | {
            "currency": self.market.currency}

    def list_positions(self) -> list:
        rows = self.query("position_list_query", refresh_cache=True, position_market=self.market.id,
                          currency=self.market.currency)
        return [{"code": from_broker_code(self.market, r["code"]), "name": r.get("stock_name"), "qty": float(r["qty"]),
                 "can_sell_qty": float(r["can_sell_qty"]), "cost_price": float(r["cost_price"]),
                 "market_val": float(r["market_val"])}
                for _, r in rows.iterrows() if float(r["qty"]) != 0]

    def place_order(self, code: str, side: str, qty: float, price: float, remark: str) -> str:
        self.unlock()
        rows = self.query("place_order", price=price, qty=qty, code=to_broker_code(self.market, code),
                          trd_side=side.upper(), order_type="NORMAL", remark=remark, time_in_force="DAY")
        return str(rows["order_id"].iloc[0])

    def get_order(self, order_id: str, remark: str = None) -> dict:
        """Today's order by id (or by remark when the id never came back), else from order history."""
        today = self.query("order_list_query", refresh_cache=True, order_market=self.market.id)
        rows = today[today["remark"] == remark] if order_id is None else today[today["order_id"].astype(str) == order_id]
        if rows.empty and order_id is not None:
            history = self.query("history_order_list_query", order_market=self.market.id)
            rows = history[history["order_id"].astype(str) == order_id]
        if rows.empty:
            return None
        r = rows.iloc[0]
        return {"order_id": str(r["order_id"]), "broker_status": r["order_status"],
                "status": ORDER_STATES.get(r["order_status"], "working"), "dealt_qty": float(r["dealt_qty"]),
                "dealt_avg_price": float(r["dealt_avg_price"]), "updated_time": str(r["updated_time"]),
                "message": r.get("last_err_msg") or ""}

    def get_order_fees(self, order_id: str):
        """Fees moomoo charged for the order, or None when the account does not report them (paper)."""
        try:
            rows = self.query("order_fee_query", order_id_list=[order_id])
        except BrokerError:
            return None
        return float(rows["fee_amount"].iloc[0]) if len(rows) else None

    def cancel_order(self, order_id: str):
        self.unlock()
        self.query("modify_order", modify_order_op="CANCEL", order_id=order_id, qty=0, price=0)
