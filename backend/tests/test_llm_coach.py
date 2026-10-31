"""Optional LLM explanation layer: off by default, validated against grounded evidence, falls back.

No API key exists in CI, so the provider side is a real HTTP server on a local socket that speaks
the OpenAI-compatible (xAI/OpenAI) and Anthropic wire formats and returns canned replies. That
exercises the adapter's request/response handling, the validation, and every fallback path. The
chess evidence is real: python-chess boards, and in the API test, Stockfish via the session.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import chess
import pytest

from chessmind.config import Settings
from chessmind.models import Player
from chessmind.security import create_access_token, hash_password
from chessmind.services import llm_coach

# 1.e4 e5 2.Nf3 Nc6, White plays 3.g4?: evidence as the session would build it.
FEN = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3"
EVIDENCE = llm_coach.Evidence(
    fen_before=FEN, player_color="white", played_san="g4", classification="mistake",
    eval_swing_cp=187, eval_before_cp_white=40, eval_after_cp_white=-147,
    best_san_list=["Bb5", "d4", "Bc4"], best_pv_san=["Bb5", "a6", "Ba4", "Nf6"],
    board_features={"new_hanging_squares": ["g4"], "lost_castling_kingside": False},
    template_text="g4 is a mistake — a +1.87-pawn swing against you. Better was Bb5, planning Bb5 a6 Ba4 Nf6.",
)


class _Provider(BaseHTTPRequestHandler):
    reply: str = ""
    delay: float = 0.0
    status: int = 200
    requests: list[dict] = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        time.sleep(type(self).delay)
        if self.path.endswith("/messages"):
            payload = {"content": [{"type": "text", "text": type(self).reply}]}
        else:
            payload = {"choices": [{"message": {"content": type(self).reply}}]}
        data = json.dumps(payload).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture
def provider():
    _Provider.reply, _Provider.delay, _Provider.status, _Provider.requests = "", 0.0, 200, []
    srv = HTTPServer(("127.0.0.1", 0), _Provider)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield _Provider, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _settings(base: str, **kw) -> Settings:
    return Settings(llm_explanations_enabled=True, llm_provider="xai", llm_api_key="test-key",
                    llm_model="grok-test", llm_base_url=base, llm_timeout_s=2.0, **kw)


GOOD = json.dumps({"text": "g4 loosens your kingside and the pawn on g4 hangs. Stockfish preferred Bb5, "
                           "heading for Bb5 a6 Ba4 Nf6; this cost about +1.87.",
                   "cited_facts": ["F1", "F2", "F4", "F6", "F7"]})


async def test_disabled_without_flag_or_key_uses_template(provider):
    p, base = provider
    for s in [Settings(), Settings(llm_explanations_enabled=True, llm_model="m"),
              Settings(llm_explanations_enabled=True, llm_api_key="k")]:
        out = await llm_coach.explain(EVIDENCE, settings=s)
        assert (out.text, out.source) == (EVIDENCE.template_text, "template")
    assert p.requests == []  # nothing was sent anywhere


async def test_valid_reply_is_used(provider):
    p, base = provider
    p.reply = GOOD
    out = await llm_coach.explain(EVIDENCE, settings=_settings(base))
    assert out.source == "llm:xai:grok-test" and out.text.startswith("g4 loosens")
    req = p.requests[0]
    assert req["path"] == "/chat/completions" and req["headers"]["Authorization"] == "Bearer test-key"
    sent = req["body"]["messages"][1]["content"]
    assert "Stockfish's best move was Bb5." in sent and "F7:" in sent


@pytest.mark.parametrize("reply, reason", [
    (json.dumps({"text": "Qh5 was the move, hitting f7.", "cited_facts": ["F1"]}), "move Qh5"),
    (json.dumps({"text": "You are now -3.20 down.", "cited_facts": ["F3"]}), "evaluation -3.20"),
    (json.dumps({"text": "Play Bb5.", "cited_facts": ["F99"]}), "cited facts"),
    (json.dumps({"text": "Play Bb5.", "cited_facts": []}), "cited facts"),
    (json.dumps({"text": "", "cited_facts": ["F1"]}), "empty"),
    ("I think Bb5 is good.", "provider error"),
])
async def test_ungrounded_replies_fall_back(provider, reply, reason):
    p, base = provider
    p.reply = reply
    out = await llm_coach.explain(EVIDENCE, settings=_settings(base))
    assert out.source == "template" and out.text == EVIDENCE.template_text
    assert reason in out.rejected_reason


async def test_timeout_and_http_error_fall_back(provider):
    p, base = provider
    p.reply, p.delay = GOOD, 3.0
    out = await llm_coach.explain(EVIDENCE, settings=_settings(base))
    assert out.source == "template" and "provider error" in out.rejected_reason
    p.delay, p.status = 0.0, 500
    out = await llm_coach.explain(EVIDENCE, settings=_settings(base))
    assert out.source == "template"


async def test_anthropic_wire_format(provider):
    p, base = provider
    p.reply = GOOD
    s = Settings(llm_explanations_enabled=True, llm_provider="anthropic", llm_api_key="k2",
                 llm_model="claude-test", llm_base_url=base, llm_timeout_s=2.0)
    out = await llm_coach.explain(EVIDENCE, settings=s)
    assert out.source == "llm:anthropic:claude-test"
    req = p.requests[0]
    assert req["path"] == "/messages" and req["headers"]["x-api-key"] == "k2"
    assert req["body"]["system"] == llm_coach.SYSTEM_PROMPT


def test_validator_replays_evidence_with_python_chess():
    bad = llm_coach.Evidence(**{**EVIDENCE.__dict__, "best_pv_san": ["Bb5", "Bb5"]})
    assert "not legal" in llm_coach.validate(bad, "Bb5 is best.", ["F4"])
    assert llm_coach.validate(EVIDENCE, "Bb5 is best, then Ba4.", ["F4", "F6"]) is None
    assert llm_coach.validate(EVIDENCE, "The pawn on g4 hangs.", ["F7"]) is None  # squares are fine


async def test_session_move_uses_llm_when_enabled_and_never_in_quiet_mode(client, db, provider, monkeypatch):
    p, base = provider
    p.reply = json.dumps({"text": "Good, that is the engine's top choice.", "cited_facts": ["F1", "F2"]})
    monkeypatch.setattr(llm_coach, "get_settings", lambda: _settings(base))
    pl = Player(email="llm@example.com", password_hash=hash_password("passwordpassword"), display_name="L",
                preferred_color="white", settings_json={})
    db.add(pl)
    await db.commit()
    hdr = {"Authorization": f"Bearer {create_access_token(pl.id)}"}

    sid = (await client.post("/sessions", json={"mode": "guided", "sampler_mode": "mainline", "seed": 1},
                             headers=hdr)).json()["id"]
    r = (await client.post(f"/sessions/{sid}/move", json={"san": "e4"}, headers=hdr)).json()
    assert r["coaching"]["explanation_source"] == "llm:xai:grok-test"
    assert r["coaching"]["spoken_summary"] == "Good, that is the engine's top choice."
    assert "llm_fact:F1" in r["coaching"]["evidence_refs"]
    # The deterministic parts are untouched: classification and evals come from Stockfish.
    assert r["classification"] in {"best", "playable"} and r["eval_before_cp_white"] is not None
    sent = p.requests[0]["body"]["messages"][1]["content"]
    assert "The player (white) played e4." in sent

    calls = len(p.requests)
    qid = (await client.post("/sessions", json={"mode": "guided", "sampler_mode": "mainline", "seed": 1,
                                                "quiet": True}, headers=hdr)).json()["id"]
    q = (await client.post(f"/sessions/{qid}/move", json={"san": "e4"}, headers=hdr)).json()
    assert q["coaching"]["spoken_summary"] == "" and len(p.requests) == calls
