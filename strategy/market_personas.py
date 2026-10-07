# -*- coding: utf-8 -*-
"""AI personas for US / Bursa Malaysia: system prompt, market snapshot and decision recording.

Personas come from config/market_personas.yaml (falling back to the .example). They advise only on
today's cards and current holdings; the last line of a reply is JSON {"decisions": [...]}, which is
recorded and tracked (forward pool persona_select). A persona decision is never an order.

Usage (daily, after the cards):
  python strategy/market_personas.py --market US --consult
  python strategy/market_personas.py --market MY --consult --persona risk_officer
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from backtest.market_timing import get_timing_report  # noqa: E402
from backtest.market_validation import get_latest_report  # noqa: E402
from data.markets import Market, get_market  # noqa: E402
from data.providers.base import ProviderError, get_credential  # noqa: E402
from strategy import llm_client, market_positions, market_track  # noqa: E402

CONFIG_PATHS = (BASE / "config" / "market_personas.yaml", BASE / "config" / "market_personas.yaml.example")
ACTIONS = {"buy", "hold", "sell", "watch"}
PRIORITIES = {"high", "mid", "low"}
# earlier turns sent with a follow-up question (the snapshot itself is always fresh)
HISTORY_TURNS = 6


def get_persona_config(paths=CONFIG_PATHS) -> dict:
    path = next((p for p in paths if Path(p).exists()), None)
    return (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}) if path else {}


def list_personas(market: Market, paths=CONFIG_PATHS) -> list:
    personas = get_persona_config(paths).get("personas", [])
    return [p for p in personas if market.id in p.get("markets", [])]


def get_persona(market: Market, persona_id: str) -> dict:
    match = [p for p in list_personas(market) if p["id"] == persona_id]
    if not match:
        raise ValueError(f"no persona {persona_id!r} for {market.id}")
    return match[0]


def build_system_prompt(market: Market, persona: dict) -> str:
    buy_rule = ("You may recommend buy, hold, sell or watch." if persona.get("can_buy", True)
                else "You never recommend buying: use sell, hold or watch only.")
    cards = market_track.get_latest_cards(market) or {}
    example_code = (cards.get("cards") or [{"code": market.benchmark_symbol}])[0]["code"]
    shown = set((cards.get("cards") or [{}])[0].get("factors", {})) | set((get_latest_report(market.id) or {}).get("factors", {}))
    focus = [f for f in persona.get("focus") or [] if f in shown]
    example = json.dumps({"decisions": [{"code": example_code, "action": "watch", "priority": "mid",
                                         "reason": "at most twelve words"}]})
    return "\n".join([
        f"You are \"{persona['name']}\", an investing-style persona for {market.name} ({market.currency}). "
        "You are a simulated style, not a real person, and nothing you say is investment advice.",
        f"Style: {persona['style']}",
        f"Factors you weigh most (as shown in the snapshot): {', '.join(focus) or 'risk evidence and exit alerts'}.",
        "Rules:",
        "1. Decide only on the codes in the snapshot's cards and holdings; anything else is at most 'watch'.",
        f"2. {buy_rule} The quant system's discipline stands: honour stops and exit alerts, respect the data caveats.",
        "3. Your decisions are tracked to measure you; they are not orders. The human approves every trade.",
        "4. Be brief: conclusion first, then one line per name you act on (code, view, reason tied to your style). "
        "At most 250 words.",
        f"5. The LAST line must be one JSON object and nothing else, e.g. {example}. "
        "action: buy/hold/sell/watch; priority: high/mid/low. If you have no decision, output {\"decisions\": []}.",
        "6. Use no tools and no outside data; answer only from the snapshot.",
    ])


def build_snapshot(market: Market) -> str:
    """Text the persona reads: regime, cards with factor ranks and evidence, holdings and alerts."""
    cards = market_track.get_latest_cards(market) or {}
    book = market_positions.build_book(market)
    report = get_latest_report(market.id) or {}
    regime = cards.get("regime") or {}
    lines = [f"Market: {market.name} ({market.currency}). Signal date {cards.get('signal_date', 'n/a')}; "
             "trades execute at the next session open."]
    if regime.get("state"):
        gate = "informational only" if regime.get("is_gate") is False else "gates new positions"
        lines.append(f"L0 regime ({regime.get('benchmark')}): {regime['state']}, cash {regime.get('cash_ratio', 0):.0%}, "
                     f"{gate}; tested verdict: {regime.get('verdict') or 'n/a'}.")
    lines += [f"Caveat: {w}" for w in cards.get("warnings", [])]
    valid_windows = [f"{name} on the benchmark" for name, sides in
                     ((get_timing_report(market.id) or {}).get("calendar") or {}).items()
                     if sides["benchmark"]["grade"] == "valid"]
    if valid_windows:
        lines.append(f"Validated calendar effects: {', '.join(valid_windows)}.")
    prefers_low = [f["name"] for f in market.validation.get("factors") or [] if f.get("sign", 1) < 0]
    lines.append("Factor percentiles run 0-100 where 100 is best in the factor's tested direction"
                 + (f"; these prefer LOW raw values, so 100 = lowest: {', '.join(prefers_low)}." if prefers_low else "."))
    lines.append(f"Cards (rank · code · name · score · factor percentiles · close · stop · "
                 f"20-day median traded value in {market.currency}):")
    for c in cards.get("cards", []):
        factors = ", ".join(f"{k} {v:.0f}" for k, v in c["factors"].items())
        context = ", ".join(f"{k} {v:.0f}" for k, v in (c.get("context") or {}).items())
        lines.append(f"  #{c['rank']} {c['code']} {c.get('name') or ''} · score {c['score']} · {factors} · "
                     f"close {c['price']:g} · stop {c['stop']['price']:g} · traded {c.get('adv_20') or 0:,.0f}"
                     + (f" · also (not scored): {context}" if context else ""))
    lines.append("Holdings (code · shares · entry → last · P&L · sessions · exit alerts):")
    for p in book["open"] or []:
        m = p["marks"]
        alerts = "; ".join(f"{a['rule']}: {a['detail']}" for a in p["alerts"]) or "none"
        lines.append(f"  {p['code']} · {m['shares']:g} · {m['entry']:g} → {m.get('last')} · "
                     f"{(m.get('pnl_pct') or 0):+.1%} · {m.get('sessions_held')} · {alerts}")
    if not book["open"]:
        lines.append("  none")
    lines.append("Factor evidence (after costs; grades valid/watch/invalid):")
    for name, f in (report.get("factors") or {}).items():
        p = f["portfolio"]
        lines.append(f"  {name}: {f['grade']}, annual {p['annual_return']:+.1%}, Sharpe {p['sharpe']:.2f}, "
                     f"holdout {(f['holdout']['portfolio'].get('annual_return') or 0):+.1%}")
    return "\n".join(lines)


def parse_decisions(text: str) -> list:
    """Decisions from the last JSON object in a reply that has a "decisions" key."""
    decoder = json.JSONDecoder()
    for start in reversed([m.start() for m in re.finditer(r"\{", text or "")]):
        try:
            payload, _ = decoder.raw_decode(text[start:])
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("decisions"), list):
            return payload["decisions"]
    return []


def update_persona_decisions(market: Market, persona_id: str, text: str) -> dict:
    """Record a reply's decisions for codes on the cards or in holdings; buys enter persona_select."""
    persona = get_persona(market, persona_id)
    cards = market_track.get_latest_cards(market) or {}
    allowed = {c["code"] for c in cards.get("cards", [])} | {p["code"] for p in market_positions.list_positions(market)
                                                             if p["status"] == "open"}
    recorded, rejected = [], []
    for d in parse_decisions(text):
        code = market_positions.resolve_code(market, str(d.get("code", "")))
        action = str(d.get("action", "")).lower()
        if code not in allowed or action not in ACTIONS or (action == "buy" and not persona.get("can_buy", True)):
            rejected.append(d)
            continue
        recorded.append({"persona": persona_id, "code": code, "action": action,
                         "priority": d.get("priority") if d.get("priority") in PRIORITIES else "mid",
                         "reason": str(d.get("reason", ""))[:120], "card_date": cards.get("signal_date"),
                         "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
    path = market_track.get_desk_path(market, "persona_decisions")
    market_track.write_json(path, market_track.read_json(path, []) + recorded)
    buys = [r["code"] for r in recorded if r["action"] == "buy"]
    tracked = market_track.append_persona_track(market, persona_id, buys, cards.get("signal_date")) if buys else 0
    return {"recorded": len(recorded), "rejected": len(rejected), "tracked": tracked, "decisions": recorded}


def list_chat(market: Market, persona_id: str) -> list:
    return market_track.read_json(market_track.get_desk_path(market, "persona_chat"), {}).get(persona_id, [])


def handle_consult(market: Market, persona_id: str, question: str = None, settings: dict = None) -> dict:
    """Ask a persona (daily review when `question` is empty), record its decisions, keep the turn."""
    persona = get_persona(market, persona_id)
    settings = settings or get_persona_config().get("llm") or {}
    is_daily = not question
    question = question or settings.get("daily_question", "Review today's cards and holdings and give your decisions.")
    history = [] if is_daily else list_chat(market, persona_id)[-HISTORY_TURNS:]
    messages = [{"role": "system", "content": build_system_prompt(market, persona) + "\n\nSNAPSHOT\n" + build_snapshot(market)}]
    messages += [{"role": t["role"], "content": t["content"]} for t in history]
    messages.append({"role": "user", "content": question})
    reply = llm_client.create_completion(settings, messages)
    result = update_persona_decisions(market, persona_id, reply)
    path = market_track.get_desk_path(market, "persona_chat")
    chats = market_track.read_json(path, {})
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    chats[persona_id] = (chats.get(persona_id, []) + [{"role": "user", "content": question, "ts": now},
                                                      {"role": "assistant", "content": reply, "ts": now}])[-40:]
    market_track.write_json(path, chats)
    return {"persona": persona_id, "question": question, "reply": reply, **result}


def build_persona_desk(market: Market) -> dict:
    """What the US / MY tab shows: personas with prompts, last exchange, today's decisions and
    track record, plus the live snapshot and whether an LLM key is configured (never the key)."""
    settings = get_persona_config().get("llm") or {}
    cards = market_track.get_latest_cards(market) or {}
    decisions = [d for d in market_track.read_json(market_track.get_desk_path(market, "persona_decisions"), [])
                 if d.get("card_date") == cards.get("signal_date")]
    track = market_track.summarize_track(market)
    personas = [{**p, "system_prompt": build_system_prompt(market, p), "last_turns": list_chat(market, p["id"])[-2:],
                 "decisions": [d for d in decisions if d["persona"] == p["id"]],
                 "track": track.get(f"persona:{p['id']}")}
                for p in list_personas(market)]
    return {"market": market.id, "personas": personas, "snapshot": build_snapshot(market),
            "llm": {"model": settings.get("model"), "base_url": settings.get("base_url"),
                    "is_key_set": bool(get_credential(settings.get("api_key_env", "DEEPSEEK_API_KEY")))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", default="US")
    parser.add_argument("--consult", action="store_true", help="run the daily review for each persona")
    parser.add_argument("--persona", help="only this persona id")
    args = parser.parse_args()
    market = get_market(args.market)
    personas = [p for p in list_personas(market) if not args.persona or p["id"] == args.persona]
    if not args.consult:
        print("\n".join(f"{p['id']}: {p['name']} ({p['tag']})" for p in personas))
        return
    for persona in personas:
        try:
            result = handle_consult(market, persona["id"])
        except ProviderError as exc:
            # no key or provider down: skip quietly so the daily script carries on
            print(f"{market.id} personas skipped: {exc}")
            return
        calls = ", ".join(f"{d['code']} {d['action']}" for d in result["decisions"]) or "no decisions"
        print(f"{market.id} {persona['name']}: {calls} (rejected {result['rejected']})")


if __name__ == "__main__":
    main()
