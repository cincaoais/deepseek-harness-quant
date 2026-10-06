# -*- coding: utf-8 -*-
"""One-line-per-market overview for the portal and report pages: data freshness, L0 regime,
cards, buy list, holdings and validation grades."""
from collections import Counter

from backtest.market_validation import get_latest_report
from data.market_store import build_status
from data.markets import Market
from strategy import market_positions, market_track


def build_overview(market: Market) -> dict:
    status = build_status(market)
    desk = market_track.build_desk(market)
    cards = desk["cards"] or {}
    report = get_latest_report(market.id) or {}
    return {
        "id": market.id, "name": market.name, "currency": market.currency,
        "data": {"last_date": status["bars"]["last_date"], "is_current": status["bars"]["is_current"],
                 "symbols": status["bars"]["symbols"]},
        "regime": cards.get("regime"),
        "cards": {"signal_date": cards.get("signal_date"), "count": len(cards.get("cards", []))},
        "buy_list": len(desk["buy_list"]),
        "book": market_positions.build_book(market)["summary"],
        "validation": {"generated_at": report.get("generated_at"),
                       "grades": dict(Counter(f["grade"] for f in report.get("factors", {}).values()))},
    }
