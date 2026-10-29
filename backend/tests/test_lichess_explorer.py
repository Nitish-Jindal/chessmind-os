"""Lichess Masters explorer adapter: parsing, TTL cache, sampler mirror, fallback.

Upstream is replaced by an httpx transport replaying the JSON fixtures in
tests/fixtures/ (explorer.lichess.ovh is unreachable from CI); everything
downstream — python-chess validation, Postgres cache, master_move_stat
rows, the /openings endpoints — is real.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import chess
import httpx
import pytest
from sqlalchemy import select

from chessmind.config import get_settings
from chessmind.models import MasterExplorerCache, MasterMoveStat, Position
from chessmind.services import lichess_explorer as lx
from chessmind.services.canonical_fen import canonical_key

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name: str) -> dict:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def italian_board() -> chess.Board:
    b = chess.Board()
    for san in ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]:
        b.push_san(san)
    return b


class Upstream:
    """Counts requests and replays a fixture (or fails) like the real endpoint would."""

    def __init__(self, payload: dict | None = None, *, status: int = 200, fail: bool = False,
                 headers: dict | None = None) -> None:
        self.payload, self.status, self.fail, self.headers = payload, status, fail, headers or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("Name or service not known", request=request)
        return httpx.Response(self.status, json=self.payload or {}, headers=self.headers)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


@pytest.fixture(autouse=True)
def _explorer_on():
    s = get_settings()
    prev = (s.lichess_explorer_enabled, s.lichess_explorer_ttl_hours)
    s.lichess_explorer_enabled = True
    lx.reset_backoff()
    yield
    s.lichess_explorer_enabled, s.lichess_explorer_ttl_hours = prev
    lx.reset_backoff()


def test_parse_validates_moves_through_python_chess():
    board = chess.Board()
    snap = lx.parse_masters_response(board, load("lichess_masters_startpos.json"))
    sans = [m.san for m in snap.moves]
    assert "Qh5" not in sans  # illegal from the start position: dropped, not served
    assert snap.dropped == [{"san": "Qh5", "uci": "d1h5"}]
    for m in snap.moves:
        assert chess.Move.from_uci(m.uci) in board.legal_moves
        assert m.games == m.white + m.draws + m.black
    assert [m.games for m in snap.moves] == sorted((m.games for m in snap.moves), reverse=True)
    assert snap.total_games == snap.white + snap.draws + snap.black
    assert snap.canonical_key == canonical_key(board)


def test_parse_normalizes_lichess_castling_uci():
    snap = lx.parse_masters_response(italian_board(), load("lichess_masters_italian_castle.json"))
    castle = next(m for m in snap.moves if m.san == "O-O")
    assert castle.uci == "e1g1"  # payload said e1h1 (king takes rook)
    assert (snap.opening_eco, snap.opening_name) == ("C50", "Italian Game: Giuoco Piano")


def test_parse_rejects_non_object():
    with pytest.raises(ValueError):
        lx.parse_masters_response(chess.Board(), ["not", "an", "object"])  # type: ignore[arg-type]


async def test_live_fetch_then_cache_hit_and_sampler_rows(db):
    board = chess.Board()
    up = Upstream(load("lichess_masters_startpos.json"))
    async with up.client() as client:
        first = await lx.get_masters(db, board, client=client)
        second = await lx.get_masters(db, board, client=client)

    assert first.status == "live"
    assert second.status == "cached"
    assert len(up.requests) == 1  # cache served the second call
    req = up.requests[0]
    assert req.url.path == "/masters"
    assert req.url.params["fen"] == board.fen()
    assert second.snapshot.to_json() == first.snapshot.to_json()

    row = (await db.execute(select(MasterExplorerCache))).scalar_one()
    assert row.canonical_key == canonical_key(board)
    assert row.total_games == first.snapshot.total_games

    pos = (await db.execute(select(Position).where(Position.canonical_key == canonical_key(board)))).scalar_one()
    stats = (await db.execute(select(MasterMoveStat).where(MasterMoveStat.position_id == pos.id))).scalars().all()
    by_uci = {s.move_uci: s for s in stats}
    assert set(by_uci) == {m.uci for m in first.snapshot.moves}
    e4 = next(m for m in first.snapshot.moves if m.san == "e4")
    assert (by_uci["e2e4"].white_wins, by_uci["e2e4"].draws, by_uci["e2e4"].black_wins) == (e4.white, e4.draws, e4.black)
    assert all(s.source == "lichess_masters" for s in stats)


async def test_ttl_expiry_refetches_and_replaces_rows(db):
    board = italian_board()
    up = Upstream(load("lichess_masters_italian_castle.json"))
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    ttl = timedelta(hours=get_settings().lichess_explorer_ttl_hours)
    async with up.client() as client:
        assert (await lx.get_masters(db, board, client=client, now=t0)).status == "live"
        assert (await lx.get_masters(db, board, client=client, now=t0 + ttl - timedelta(minutes=1))).status == "cached"
        assert (await lx.get_masters(db, board, client=client, now=t0 + ttl + timedelta(minutes=1))).status == "live"
    assert len(up.requests) == 2
    stats = (await db.execute(select(MasterMoveStat))).scalars().all()
    assert len(stats) == 3  # refreshed in place, not duplicated


async def test_unreachable_without_cache_reports_unavailable_and_backs_off(db):
    board = chess.Board()
    up = Upstream(fail=True)
    async with up.client() as client:
        r1 = await lx.get_masters(db, board, client=client)
        r2 = await lx.get_masters(db, board, client=client)
    assert r1.status == "unavailable" and r1.snapshot is None and "ConnectError" in r1.error
    assert r2.status == "unavailable"
    assert len(up.requests) == 1  # backoff: no second upstream call
    assert (await db.execute(select(MasterExplorerCache))).first() is None


async def test_unreachable_with_expired_cache_serves_stale(db):
    board = chess.Board()
    t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    async with Upstream(load("lichess_masters_startpos.json")).client() as ok:
        live = await lx.get_masters(db, board, client=ok, now=t0)
    async with Upstream(fail=True).client() as down:
        stale = await lx.get_masters(db, board, client=down, now=t0 + timedelta(days=30))
    assert stale.status == "stale"
    assert stale.fetched_at == t0
    assert stale.snapshot.to_json() == live.snapshot.to_json()


async def test_rate_limit_is_a_graceful_fallback(db):
    up = Upstream({}, status=429, headers={"retry-after": "120"})
    async with up.client() as client:
        r = await lx.get_masters(db, chess.Board(), client=client)
    assert r.status == "unavailable" and "429" in r.error


async def test_disabled_never_calls_upstream(db):
    get_settings().lichess_explorer_enabled = False
    up = Upstream(load("lichess_masters_startpos.json"))
    async with up.client() as client:
        r = await lx.get_masters(db, chess.Board(), client=client)
    assert r.status == "disabled" and not up.requests


async def test_tree_endpoint_separates_masters_from_local_counts(client, db):
    from chessmind.services.opening_graph import upsert_position

    board = chess.Board()
    await upsert_position(db, board)
    async with Upstream(load("lichess_masters_startpos.json")).client() as up:
        await lx.get_masters(db, board, client=up)
    await db.flush()

    body = (await client.get("/openings/tree")).json()
    m = body["masters"]
    assert m["status"] == "cached"
    assert m["source"] == "lichess_masters"
    assert "Lichess" in m["attribution"]
    assert m["explorer_url"].startswith("https://lichess.org/analysis/")
    assert m["total_games"] == m["white"] + m["draws"] + m["black"]
    assert [x["san"] for x in m["moves"]][:2] == ["d4", "e4"]
    assert "assigned" in body["local_stats_note"]

    off = (await client.get("/openings/tree", params={"masters": "false"})).json()
    assert off["masters"] is None


async def test_masters_endpoint_any_fen_and_validation(client):
    get_settings().lichess_explorer_enabled = False
    fen = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
    r = await client.get("/openings/masters", params={"fen": fen})
    assert r.status_code == 200
    assert r.json()["status"] == "disabled" and r.json()["moves"] == []
    assert (await client.get("/openings/masters", params={"fen": "not a fen"})).status_code == 400
