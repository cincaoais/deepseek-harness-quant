import pandas as pd
import pytest

from strategy import market_pitch, market_track


def test_composite_score_ranks_only_tradable_stocks_with_every_factor():
    panel = {"turn": pd.DataFrame({"A": [1.0] * 20, "B": [3.0] * 20, "C": [9.0] * 20, "D": [None] * 20})}
    mask_today = pd.Series({"A": True, "B": True, "C": False, "D": True})
    factors = [{"name": "turn_20", "sign": 1, "weight": 1}]
    scores = market_pitch.compute_scores(panel, mask_today, factors)
    assert scores["score"].round(1).to_dict() == {"B": 100.0, "A": 50.0}


def test_factors_graded_invalid_are_dropped(market_in):
    report = {"factors": {
        "near_high_250": {"grade": "watch", "portfolio": {"annual_return": 0.2}, "ic": {"mean": 0.1},
                          "holdout": {"portfolio": {"annual_return": 0.1}}},
        "mom_12_1": {"grade": "invalid", "portfolio": {"annual_return": -0.1}, "ic": {"mean": -0.1},
                     "holdout": {"portfolio": {"annual_return": -0.1}}},
    }}
    used, skipped = market_pitch.resolve_factors(market_in("MY"), report)
    assert [f["name"] for f in used] == ["near_high_250", "lowvol_60"]
    assert [f["name"] for f in skipped] == ["mom_12_1"]


def test_position_is_lot_rounded_within_the_slice(market_in):
    position = market_pitch.build_position(market_in("MY"), 1.96)
    assert position["shares"] == 2500
    assert position["value"] + position["est_cost"] <= 50000 / 10


@pytest.fixture
def desk(market_in, tmp_path, monkeypatch):
    monkeypatch.setattr(market_track, "DESK_DIR", tmp_path / "desk")
    market = market_in("MY")
    cards = {"signal_date": "2026-10-05", "cards": [{"code": "6114.MY", "score": 93.9}, {"code": "7854.MY", "score": 91.1}]}
    market_track.write_json(market_track.get_desk_path(market, "cards"), cards)
    market_track.append_track(market, cards)
    return market


def test_buy_list_is_the_approved_cards_and_undo_reverts_it(desk):
    market_track.update_card_decision(desk, "6114.MY", "buy")
    market_track.update_card_decision(desk, "7854.MY", "drop")
    state = market_track.build_desk(desk)
    assert [c["code"] for c in state["buy_list"]] == ["6114.MY"]
    assert state["track"]["human_select"]["entries"] == 1
    market_track.update_card_decision(desk, "6114.MY", "undo")
    assert market_track.build_desk(desk)["buy_list"] == []


def test_only_codes_on_the_latest_cards_can_be_approved(desk):
    with pytest.raises(ValueError, match="not on the 2026-10-05"):
        market_track.update_card_decision(desk, "1155.MY", "buy")


def test_forward_returns_start_at_the_open_after_the_signal():
    bars = pd.DataFrame({"date": ["2026-10-05", "2026-10-06", "2026-10-07"],
                         "open": [9.0, 10.0, 10.5], "close": [9.5, 10.2, 11.0]})
    entry_date, entry_open, fwd = market_track.compute_forward(bars, "2026-10-05")
    assert (entry_date, entry_open) == ("2026-10-06", 10.0)
    assert fwd == pytest.approx({"t1": 0.02})
