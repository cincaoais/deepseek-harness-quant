import pytest

from data.markets import get_last_closed_session, list_markets
from tests.conftest import EXAMPLE_CONFIG


@pytest.mark.parametrize("market_id, canonical, provider, provider_symbol", [
    ("US", "BRK.B.US", "yahoo", "BRK-B"),
    ("US", "AAPL.US", "eodhd", "AAPL.US"),
    ("US", "BRK.B.US", "nasdaqtrader", "BRK.B"),
    ("US", "SPY.US", "yahoo", "SPY"),
    ("MY", "0166.MY", "yahoo", "0166.KL"),
    ("MY", "1155.MY", "eodhd", "1155.KLSE"),
    ("MY", "KLCI.MY", "yahoo", "^KLSE"),
])
def test_symbol_round_trip(market_in, market_id, canonical, provider, provider_symbol):
    market = market_in(market_id)
    assert market.to_provider(canonical, provider) == provider_symbol
    assert market.from_provider(provider_symbol, provider) == canonical


def test_missing_markets_yaml_falls_back_to_template(tmp_path):
    markets = list_markets(paths=(tmp_path / "markets.yaml", EXAMPLE_CONFIG))
    assert sorted(markets) == ["MY", "US"]
    assert "0166" in markets["MY"].universe["seed"]


@pytest.mark.parametrize("now_utc, expected", [
    ("2026-10-06 03:00", "2026-10-05"),
    ("2026-10-06 10:00", "2026-10-06"),
])
def test_last_closed_session_waits_for_bursa_close(market_in, now_utc, expected):
    assert get_last_closed_session(market_in("MY"), now=now_utc) == expected
