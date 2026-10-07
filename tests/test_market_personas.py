import pytest

from strategy import market_personas, market_track


def build_card(rank, code):
    return {"rank": rank, "code": code, "name": code, "score": 90 - rank, "factors": {"near_high_250": 95.0},
            "price": 100.0, "stop": {"pct": 0.15, "price": 85.0}}


@pytest.fixture
def market(market_in, tmp_path, monkeypatch):
    monkeypatch.setattr(market_track, "DESK_DIR", tmp_path / "desk")
    monkeypatch.setattr(market_personas, "get_latest_report", lambda market_id: None)
    monkeypatch.setattr(market_personas, "get_timing_report", lambda market_id: None)
    market = market_in("US")
    cards = {"signal_date": "2026-10-05", "cards": [build_card(1, "AAPL.US"), build_card(2, "JPM.US")]}
    market_track.write_json(market_track.get_desk_path(market, "cards"), cards)
    return market


def test_reply_json_on_the_last_line_is_parsed():
    reply = 'AAPL looks strong {not json}.\n{"decisions": [{"code": "AAPL", "action": "buy", "priority": "high"}]}'
    assert market_personas.parse_decisions(reply) == [{"code": "AAPL", "action": "buy", "priority": "high"}]


def test_only_card_codes_are_recorded_and_buys_are_tracked_per_persona(market):
    reply = ('{"decisions": [{"code": "AAPL", "action": "buy", "reason": "trend"}, '
             '{"code": "TSLA", "action": "buy"}, {"code": "JPM.US", "action": "watch"}]}')
    result = market_personas.update_persona_decisions(market, "trend_rider", reply)
    assert (result["recorded"], result["rejected"], result["tracked"]) == (2, 1, 1)
    track = market_track.summarize_track(market)
    assert track["persona:trend_rider"]["entries"] == 1


def test_a_persona_that_cannot_buy_has_buys_rejected(market):
    result = market_personas.update_persona_decisions(market, "risk_officer", '{"decisions": [{"code": "AAPL", "action": "buy"}]}')
    assert (result["recorded"], result["rejected"]) == (0, 1)


def test_personas_are_filtered_by_market(market_in):
    assert "value_contrarian" not in [p["id"] for p in market_personas.list_personas(market_in("MY"))]


@pytest.fixture
def llm_server():
    """Local stand-in for an OpenAI-compatible /chat/completions endpoint; records each request."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": body})
            reply = 'AAPL keeps trending.\n{"decisions": [{"code": "AAPL", "action": "buy", "priority": "high"}]}'
            payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": reply}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", seen
    server.shutdown()


def test_consult_sends_prompt_and_snapshot_and_records_the_reply(market, llm_server, monkeypatch):
    url, seen = llm_server
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    settings = {"base_url": url, "model": "test-model", "api_key_env": "TEST_LLM_KEY"}
    result = market_personas.handle_consult(market, "trend_rider", settings=settings)
    system = seen[0]["body"]["messages"][0]["content"]
    assert (seen[0]["path"], seen[0]["auth"], seen[0]["body"]["model"]) == ("/chat/completions", "Bearer sk-test", "test-model")
    assert "Trend Rider" in system and "SNAPSHOT" in system and "AAPL.US" in system
    assert (result["recorded"], result["tracked"]) == (1, 1)
    assert [t["role"] for t in market_personas.list_chat(market, "trend_rider")] == ["user", "assistant"]


def test_consult_without_a_key_names_the_missing_setting(market, monkeypatch, tmp_path):
    from data.providers import base
    monkeypatch.setattr(base, "PARAMS_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(base, "HARNESS_CREDENTIALS_PATH", tmp_path / "none2.yaml")
    with pytest.raises(base.ProviderError, match="MISSING_KEY is not set"):
        market_personas.handle_consult(market, "trend_rider", settings={"base_url": "http://x", "model": "m",
                                                                        "api_key_env": "MISSING_KEY"})


def test_prompt_example_is_valid_json_and_lists_only_shown_factors(market):
    import json
    prompt = market_personas.build_system_prompt(market, market_personas.get_persona(market, "trend_rider"))
    example = prompt.split("e.g. ", 1)[1].split(". action:", 1)[0]
    assert json.loads(example)["decisions"][0]["code"] == "AAPL.US"
    assert "near_high_250" in prompt and "mom_12_1" not in prompt
