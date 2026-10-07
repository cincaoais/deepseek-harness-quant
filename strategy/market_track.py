# -*- coding: utf-8 -*-
"""US / Bursa Malaysia decisions and forward tracking, kept apart from the A-share holdings.

output/market_desk/<market>_decisions.json  buy / drop per code and card date
output/market_desk/<market>_track.json      forward pool: auto_pitch = every card, human_select = approved buys,
                                            persona_select = AI persona buys (with the persona id)

Forward return at horizon k = close k-1 sessions after entry / entry open - 1, where entry is the
open after the signal date (the T+1 fill a trader gets).
"""
import json
import time
from pathlib import Path

from data import market_store
from data.markets import Market

BASE = Path(__file__).resolve().parent.parent
DESK_DIR = BASE / "output" / "market_desk"
HORIZONS = (1, 5, 20, 60)


def get_desk_path(market: Market, name: str) -> Path:
    return DESK_DIR / f"{market.id.lower()}_{name}.json"


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


def list_decisions(market: Market) -> list:
    return read_json(get_desk_path(market, "decisions"), [])


def update_decision(market: Market, code: str, action: str, card_date: str, note: str = "") -> dict:
    """Record buy/drop for a card; "undo" removes the decision for that card date."""
    if action not in ("buy", "drop", "undo"):
        raise ValueError(f"action must be buy, drop or undo, not {action!r}")
    decisions = [d for d in list_decisions(market) if not (d["code"] == code and d["card_date"] == card_date)]
    if action != "undo":
        decisions.append({"code": code, "action": action, "card_date": card_date, "note": note,
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    write_json(get_desk_path(market, "decisions"), decisions)
    update_track_decision(market, code, card_date, None if action == "undo" else action)
    return {"code": code, "action": action, "card_date": card_date}


def build_buy_list(cards: dict, decisions: list) -> list:
    """Approved buys among the latest cards: the buy instruction comes from the cards and nowhere else."""
    approved = {d["code"] for d in decisions if d["action"] == "buy" and d["card_date"] == cards.get("signal_date")}
    return [c for c in cards.get("cards", []) if c["code"] in approved]


def append_track(market: Market, cards: dict) -> int:
    """Add every card to the forward pool as auto_pitch (once per code and signal date)."""
    track = read_json(get_desk_path(market, "track"), [])
    seen = {(e["code"], e["signal_date"]) for e in track}
    added = [{"code": c["code"], "name": c.get("name"), "signal_date": cards["signal_date"], "score": c["score"],
              "pool": "auto_pitch", "decided": None, "entry_date": None, "entry_open": None, "fwd": {}}
             for c in cards["cards"] if (c["code"], cards["signal_date"]) not in seen]
    write_json(get_desk_path(market, "track"), track + added)
    return len(added)


def append_persona_track(market: Market, persona: str, codes: list, signal_date: str) -> int:
    """Add a persona's buy calls to the forward pool (once per persona, code and signal date)."""
    track = read_json(get_desk_path(market, "track"), [])
    seen = {(e.get("persona"), e["code"], e["signal_date"]) for e in track}
    added = [{"code": c, "signal_date": signal_date, "pool": "persona_select", "persona": persona,
              "decided": None, "entry_date": None, "entry_open": None, "fwd": {}}
             for c in dict.fromkeys(codes) if (persona, c, signal_date) not in seen]
    write_json(get_desk_path(market, "track"), track + added)
    return len(added)


def update_track_decision(market: Market, code: str, signal_date: str, action):
    """An approved buy moves its pool entry to human_select; drop/undo moves it back to auto_pitch."""
    track = read_json(get_desk_path(market, "track"), [])
    for entry in track:
        if entry["code"] == code and entry["signal_date"] == signal_date and entry["pool"] != "persona_select":
            entry["decided"] = action
            entry["pool"] = "human_select" if action == "buy" else "auto_pitch"
    write_json(get_desk_path(market, "track"), track)


def compute_forward(bars, signal_date: str) -> tuple:
    """(entry_date, entry_open, {"t1": r, ...}) from qfq bars; horizons not yet reached are left out."""
    after = bars[bars["date"] > signal_date].reset_index(drop=True)
    if after.empty or not after.loc[0, "open"] > 0:
        return None, None, {}
    entry_open = float(after.loc[0, "open"])
    fwd = {f"t{k}": float(after.loc[k - 1, "close"] / entry_open - 1) for k in HORIZONS if len(after) >= k}
    return after.loc[0, "date"], entry_open, fwd


def update_forward(market: Market) -> int:
    """Fill forward returns for entries still missing a horizon."""
    track = read_json(get_desk_path(market, "track"), [])
    cache = market_store.get_bar_cache(market)
    updated = 0
    for entry in track:
        if len(entry.get("fwd") or {}) == len(HORIZONS):
            continue
        bars = cache.get_daily(entry["code"], start=entry["signal_date"], adjust="qfq")
        if bars is None:
            continue
        entry["entry_date"], entry["entry_open"], entry["fwd"] = compute_forward(bars, entry["signal_date"])
        updated += 1
    write_json(get_desk_path(market, "track"), track)
    return updated


def summarize_track(market: Market) -> dict:
    """Per pool (and per persona) and horizon: sample size, mean forward return and win rate."""
    track = read_json(get_desk_path(market, "track"), [])
    groups = {pool: [e for e in track if e["pool"] == pool] for pool in ("auto_pitch", "human_select", "persona_select")}
    for persona in sorted({e["persona"] for e in track if e.get("persona")}):
        groups[f"persona:{persona}"] = [e for e in track if e.get("persona") == persona]
    summary = {}
    for name, entries in groups.items():
        summary[name] = {"entries": len(entries)}
        for k in HORIZONS:
            values = [e["fwd"][f"t{k}"] for e in entries if f"t{k}" in (e.get("fwd") or {})]
            summary[name][f"t{k}"] = ({"n": len(values), "mean": sum(values) / len(values),
                                      "win_rate": sum(v > 0 for v in values) / len(values)} if values else None)
    return summary


def get_latest_cards(market: Market):
    return read_json(get_desk_path(market, "cards"), None)


def update_card_decision(market: Market, code: str, action: str, note: str = "") -> dict:
    """Approve or drop a code on the latest card set; only cards can be approved (single source)."""
    cards = get_latest_cards(market)
    if not cards:
        raise ValueError(f"no {market.id} cards yet: run strategy/market_pitch.py --market {market.id}")
    if action != "undo" and code not in {c["code"] for c in cards["cards"]}:
        raise ValueError(f"{code} is not on the {cards['signal_date']} {market.id} cards")
    return update_decision(market, code, action, cards["signal_date"], note)


def build_desk(market: Market) -> dict:
    """Everything the US / MY tab shows: latest cards, their decisions, the buy list and tracking."""
    cards = get_latest_cards(market)
    decisions = [d for d in list_decisions(market) if cards and d["card_date"] == cards["signal_date"]]
    return {
        "market": market.id, "currency": market.currency, "name": market.name,
        "cards": cards, "decisions": decisions,
        "buy_list": build_buy_list(cards, decisions) if cards else [],
        "track": summarize_track(market),
    }
