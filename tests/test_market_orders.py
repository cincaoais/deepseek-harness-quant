import socket
from datetime import date, timedelta

import pandas as pd
import pytest

from backtest.market_rules import compute_limit_price
from broker import moomoo_broker
from broker.moomoo_broker import BrokerError, from_broker_code, get_broker_config, to_broker_code
from data import market_store
from strategy import market_orders, market_positions, market_track

BASE = market_orders.BASE
ORDER_COLUMNS = ["order_id", "code", "trd_side", "qty", "price", "order_status", "dealt_qty", "dealt_avg_price",
                 "remark", "updated_time", "last_err_msg"]


class FakeTradeContext:
    """Stand-in for moomoo's OpenSecTradeContext: SDK method names and columns, (RET_OK, DataFrame) replies."""

    def __init__(self, positions=()):
        self.accounts = pd.DataFrame([
            {"acc_id": 111, "trd_env": "REAL", "trdmarket_auth": ["US", "MY"], "acc_status": "ACTIVE"},
            {"acc_id": 222, "trd_env": "SIMULATE", "trdmarket_auth": ["US"], "acc_status": "ACTIVE"}])
        self.positions, self.orders, self.placed = list(positions), [], []

    def get_acc_list(self):
        return 0, self.accounts

    def accinfo_query(self, trd_env, acc_id, refresh_cache=False, currency="HKD"):
        return 0, pd.DataFrame([{"total_assets": 100000.0, "cash": 50000.0, "power": 50000.0, "market_val": 0.0,
                                 "frozen_cash": 0.0, "currency": currency}])

    def position_list_query(self, trd_env, acc_id, refresh_cache=False, position_market="N/A", currency="USD"):
        return 0, pd.DataFrame(self.positions, columns=["code", "stock_name", "qty", "can_sell_qty", "cost_price",
                                                        "market_val"])

    def place_order(self, price, qty, code, trd_side, order_type, trd_env, acc_id, remark, time_in_force):
        self.placed.append((code, trd_side, qty, price, trd_env, acc_id))
        self.orders.append({"order_id": str(9000 + len(self.orders)), "code": code, "trd_side": trd_side, "qty": qty,
                            "price": price, "order_status": "SUBMITTED", "dealt_qty": 0.0, "dealt_avg_price": 0.0,
                            "remark": remark, "updated_time": "2026-10-07 09:31:00.000", "last_err_msg": ""})
        return 0, pd.DataFrame(self.orders[-1:])

    def order_list_query(self, trd_env, acc_id, refresh_cache=False, order_market="N/A"):
        return 0, pd.DataFrame(self.orders, columns=ORDER_COLUMNS)

    def history_order_list_query(self, trd_env, acc_id, order_market="N/A"):
        return 0, pd.DataFrame(columns=ORDER_COLUMNS)

    def order_fee_query(self, order_id_list, trd_env, acc_id):
        return 0, pd.DataFrame([{"order_id": order_id_list[0], "fee_amount": 1.5}])

    def modify_order(self, modify_order_op, order_id, qty, price, trd_env, acc_id):
        order = next(o for o in self.orders if o["order_id"] == order_id)
        order["order_status"] = "CANCELLED_PART" if order["dealt_qty"] else "CANCELLED_ALL"
        return 0, pd.DataFrame([order])

    def close(self):
        pass

    def fill(self, qty, price, status="FILLED_ALL"):
        self.orders[-1].update(dealt_qty=qty, dealt_avg_price=price, order_status=status)


@pytest.fixture
def desk(market_in, tmp_path, monkeypatch):
    """Paper-mode settings from the shipped template and one fake moomoo connection per market."""
    monkeypatch.setattr(market_track, "DESK_DIR", tmp_path / "desk")
    monkeypatch.setattr(market_orders, "LOG_PATH", tmp_path / "orders.log")
    monkeypatch.setattr(market_orders, "RISK_AUDIT_PATH", tmp_path / "risk.log")
    settings = get_broker_config(paths=(BASE / "config" / "broker.yaml.example",))
    settings.update(mode="paper", kill_switch_file=str(tmp_path / "STOP"))
    fakes = {}
    monkeypatch.setattr(moomoo_broker, "create_context", lambda market, opend: fakes.setdefault(market.id, FakeTradeContext()))
    return {"market_in": market_in, "settings": settings, "fakes": fakes}


def approve_card(market, code, price, shares, age_days=1):
    signal_date = (date.today() - timedelta(days=age_days)).isoformat()
    market_track.write_json(market_track.get_desk_path(market, "cards"), {
        "signal_date": signal_date, "cards": [{"code": code, "name": "Test", "price": price, "position": {"shares": shares}}]})
    market_track.update_decision(market, code, "buy", signal_date)


def hold(market, code, shares, entry, last, account="paper"):
    days = [(date.today() - timedelta(days=n)).isoformat() for n in (3, 2)]
    bars = pd.DataFrame({"date": days, "open": [entry, last], "high": [entry, last], "low": [entry, last],
                         "close": [entry, last], "preclose": None, "volume": 100.0, "amount": None, "turn": None,
                         "pct_chg": None})
    market_store.update_bars(market, code, bars, "split", "test")
    return market_positions.create_position(market, code, shares, entry, days[0], fees=0, account=account)


@pytest.mark.parametrize("rules, ref, side, expected", [
    ({"tick_sizes": [[0, 0.005], [1.0, 0.01], [10.0, 0.02], [100.0, 0.10]]}, 0.993, "buy", 1.00),
    ({"tick_sizes": [[0, 0.005], [1.0, 0.01], [10.0, 0.02], [100.0, 0.10]]}, 1.003, "sell", 0.995),
    ({"tick_sizes": [[0, 0.0001], [1.0, 0.01]]}, 0.5, "buy", 0.505),
    ({"tick_sizes": [[0, 0.0001], [1.0, 0.01]]}, 250.0, "buy", 252.5),
])
def test_limit_price_stays_inside_the_buffer_on_the_tick_grid(rules, ref, side, expected):
    assert compute_limit_price(rules, ref, side, 0.01) == pytest.approx(expected)


@pytest.mark.parametrize("code, broker_code", [("AAPL.US", "US.AAPL"), ("BRK.B.US", "US.BRK.B"), ("1155.MY", "MY.1155")])
def test_codes_map_to_moomoo_and_back(market_in, code, broker_code):
    market = market_in(code.rsplit(".", 1)[1])
    assert to_broker_code(market, code) == broker_code
    assert from_broker_code(market, broker_code) == code


@pytest.mark.parametrize("market_id, price, shares, qty, limit", [
    ("US", 250.0, 39.6, 7, 252.5),      # whole shares, cut to the 2,000 USD order cap
    ("MY", 4.0, 1200, 100, 4.04),       # one 100-share lot until the flow is proven
])
def test_buy_drafts_are_whole_and_capped(desk, market_id, price, shares, qty, limit):
    market = desk["market_in"](market_id)
    approve_card(market, f"TEST.{market_id}", price, shares)
    draft = market_orders.build_order_desk(market, desk["settings"])["drafts"][0]
    assert (draft["limit_price"], draft["risk"]["decision"], draft["risk"]["qty"]) == (pytest.approx(limit), "reduce", qty)


def test_paper_buy_is_sent_and_its_fill_booked_in_the_ledger(desk):
    market = desk["market_in"]("US")
    approve_card(market, "AAPL.US", 250.0, 39.6)
    order = market_orders.handle_send(market, "buy:AAPL.US", settings=desk["settings"])
    fake = desk["fakes"]["US"]
    assert fake.placed == [("US.AAPL", "BUY", 7, 252.5, "SIMULATE", 222)]
    fake.fill(7, 251.0)
    assert market_orders.handle_sync(market, desk["settings"])["fills"] == [order["id"]]
    position = market_positions.list_positions(market)[0]
    assert (position["shares"], position["entry_price"], position["entry_fees"], position["account"]) == (7, 251.0, 1.5, "paper")
    assert market_orders.build_order_desk(market, desk["settings"])["drafts"] == []


def test_stop_exit_sells_past_the_buy_caps_and_a_cancelled_partial_closes_that_part(desk):
    market = desk["market_in"]("US")
    held = hold(market, "AAPL.US", 50, 100.0, 80.0)
    desk["fakes"]["US"] = FakeTradeContext(positions=[{"code": "US.AAPL", "stock_name": "Apple", "qty": 50,
                                                       "can_sell_qty": 50, "cost_price": 100.0, "market_val": 4000.0}])
    order = market_orders.handle_send(market, "sell:AAPL.US", settings=desk["settings"])
    assert (order["qty"], order["limit_price"], order["reason"]) == (50, pytest.approx(79.2), "hard_loss")
    desk["fakes"]["US"].fill(20, 79.5, "FILLED_PART")
    assert market_orders.handle_cancel(market, order["id"], desk["settings"])["status"] == "cancelled"
    rows = {p["status"]: p for p in market_positions.list_positions(market)}
    assert (rows["closed"]["shares"], rows["closed"]["exit_price"], rows["closed"]["exit_reason"]) == (20, 79.5, "hard_loss")
    assert (rows["open"]["id"], rows["open"]["shares"]) == (held["id"], 30)


@pytest.mark.parametrize("market_id, mode, is_kill_on, card_age, message", [
    ("US", "preview", False, 1, "preview mode"),
    ("MY", "paper", False, 1, "no paper trading for MY"),
    ("US", "paper", True, 1, "kill switch is on"),
    ("US", "paper", False, 9, "9 days old"),
])
def test_send_is_refused(desk, market_id, mode, is_kill_on, card_age, message):
    market = desk["market_in"](market_id)
    desk["settings"]["mode"] = mode
    if is_kill_on:
        market_orders.update_kill_switch(True, desk["settings"])
    approve_card(market, f"TEST.{market_id}", 10.0, 100, age_days=card_age)
    with pytest.raises(ValueError, match=message):
        market_orders.handle_send(market, f"buy:TEST.{market_id}", settings=desk["settings"])
    assert all(not fake.placed for fake in desk["fakes"].values())


def test_kill_switch_cancels_working_orders_and_blocks_drafts(desk, monkeypatch):
    market = desk["market_in"]("US")
    monkeypatch.setattr(market_orders, "list_markets", lambda: {"US": market})
    approve_card(market, "AAPL.US", 250.0, 39.6)
    order = market_orders.handle_send(market, "buy:AAPL.US", settings=desk["settings"])
    assert market_orders.update_kill_switch(True, desk["settings"])["cancelled"] == [order["id"]]
    assert desk["fakes"]["US"].orders[0]["order_status"] == "CANCELLED_ALL"
    draft = market_orders.build_order_desk(market, desk["settings"])["drafts"][0]
    assert draft["risk"]["decision"] == "reject"


def test_sync_reconciles_the_ledger_with_moomoo(desk):
    market = desk["market_in"]("US")
    hold(market, "AAPL.US", 10, 100.0, 120.0)
    hold(market, "MSFT.US", 3, 400.0, 410.0, account="manual")
    desk["fakes"]["US"] = FakeTradeContext(positions=[
        {"code": "US.AAPL", "stock_name": "Apple", "qty": 10, "can_sell_qty": 10, "cost_price": 100.0, "market_val": 1200.0},
        {"code": "US.NVDA", "stock_name": "NVIDIA", "qty": 5, "can_sell_qty": 5, "cost_price": 180.0, "market_val": 900.0}])
    rows = market_orders.handle_sync(market, desk["settings"])["reconcile"]
    assert [(r["code"], r["diff"], r["is_match"]) for r in rows] == [("NVDA.US", 5.0, False), ("AAPL.US", 0.0, True)]


def test_unreachable_opend_raises_instead_of_retrying(market_in):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(BrokerError, match="OpenD is not reachable"):
        moomoo_broker.create_context(market_in("US"), {"port": port, "connect_timeout_s": 1})
