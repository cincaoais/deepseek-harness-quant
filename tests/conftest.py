import dataclasses
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from data.markets import list_markets  # noqa: E402

EXAMPLE_CONFIG = BASE / "config" / "markets.yaml.example"


@pytest.fixture
def market_in(tmp_path):
    """A market from the shipped template whose data directory is a temp folder."""
    def build(market_id):
        return dataclasses.replace(list_markets(paths=(EXAMPLE_CONFIG,))[market_id], data_dir=tmp_path)
    return build
