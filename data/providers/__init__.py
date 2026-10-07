# -*- coding: utf-8 -*-
"""Market data provider adapters for US / Bursa Malaysia, chosen per market in config/markets.yaml.

Bar providers return {provider symbol: frame[date, open, high, low, close, adj_close, volume]},
with close/volume split-adjusted and adj_close also dividend-adjusted (Yahoo's convention).
"""
from importlib import import_module

PROVIDERS = ("yahoo", "eodhd", "nasdaqtrader", "sec_edgar", "fred", "bnm", "opendosm")


def get_provider(name: str):
    if name not in PROVIDERS:
        raise ValueError(f"unknown provider {name!r}; expected one of {PROVIDERS}")
    return import_module(f"data.providers.{name}")
