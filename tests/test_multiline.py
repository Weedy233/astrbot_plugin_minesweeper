from __future__ import annotations


import asyncio

from core.command_handler import GameActionHandler
from core.game import GameManager
from core.model import SweepResult


class DummyEvent:
    def __init__(self, session_id: str = "s"):
        self.session_id = session_id
        self.sent: list[str] = []

    def plain_result(self, text: str) -> str:
        return text

    async def send(self, payload: str):
        self.sent.append(payload)


class DummyImageService:
    def __init__(self):
        self.saved = 0
        self.sent = 0

    def save_cache(self, event, img_bytes: bytes) -> str:
        self.saved += 1
        return "/tmp/x.png"

    async def send_with_replace(self, event, image_path: str):
        self.sent += 1


class DummyGame:
    is_over = False

    def draw(self) -> bytes:
        return b"img"


class DummyGameWithOver:
    """Game that becomes over after a certain action"""

    action_count = 0
    is_over = False

    def draw(self) -> bytes:
        return b"img"

    def mark(self, x, y):
        self.action_count += 1
        if self.action_count >= 2:
            self.is_over = True
        return None


async def _run(handler: GameActionHandler, event: DummyEvent, tokens, action, rh, **kw):
    return await handler.handle_positions(event, tokens, action, rh, **kw)


def test_handle_positions_changed_on_open_success():
    mgr = GameManager()
    mgr.create("s", DummyGame())
    img = DummyImageService()
    h = GameActionHandler(mgr, img, ban_time=0)
    event = DummyEvent("s")

    async def run():
        changed, game, msgs = await _run(
            h,
            event,
            ["a1"],
            lambda g, x, y: None,
            lambda res, pos: None,
            send_msgs=False,
            send_board=False,
        )
        assert changed is True
        assert game is not None
        assert msgs == []
        assert img.sent == 0

    asyncio.run(run())


def test_handle_positions_changed_on_sweep_success():
    mgr = GameManager()
    mgr.create("s", DummyGame())
    img = DummyImageService()
    h = GameActionHandler(mgr, img, ban_time=0)
    event = DummyEvent("s")

    async def run():
        changed, game, msgs = await _run(
            h,
            event,
            ["a1"],
            lambda g, x, y: SweepResult.SUCCESS,
            lambda res, pos: None,
            send_msgs=False,
            send_board=False,
        )
        assert changed is True
        assert game is not None
        assert msgs == []
        assert img.sent == 0

    asyncio.run(run())


def test_handle_positions_not_changed_on_sweep_condition_not_met():
    mgr = GameManager()
    mgr.create("s", DummyGame())
    img = DummyImageService()
    h = GameActionHandler(mgr, img, ban_time=0)
    event = DummyEvent("s")

    async def run():
        changed, game, msgs = await _run(
            h,
            event,
            ["a1"],
            lambda g, x, y: SweepResult.CONDITION_NOT_MET,
            lambda res, pos: f"{pos} 不满足清扫条件",
            send_msgs=False,
            send_board=False,
        )
        assert changed is False
        assert msgs == ["a1 不满足清扫条件"]
        assert img.sent == 0

    asyncio.run(run())


def test_python_regex_matches_multiline():
    import re

    multiline_re = re.compile(r"^[\s\S]*\n[\s\S]*$")
    assert multiline_re.match("a1\nb2")
    assert multiline_re.match("'a1\n#b2")
    assert not multiline_re.match("a1 b2")


def test_handle_positions_invalid_coordinate_expressions():
    mgr = GameManager()
    mgr.create("s", DummyGame())
    img = DummyImageService()
    h = GameActionHandler(mgr, img, ban_time=0)
    event = DummyEvent("s")

    async def run():
        changed, game, msgs = await _run(
            h,
            event,
            ["123", "xyz", "a", "1"],
            lambda g, x, y: SweepResult.SUCCESS,
            lambda res, pos: None,
            send_msgs=False,
            send_board=False,
        )
        assert changed is False
        assert game is not None
        assert msgs == ["不支持的坐标表达式：123 xyz a 1"]
        assert img.sent == 0

    asyncio.run(run())


def test_multiline_integration_batches_and_renders_once():
    mgr = GameManager()
    mgr.create("s", DummyGame())
    img = DummyImageService()
    h = GameActionHandler(mgr, img, ban_time=0)
    event = DummyEvent("s")

    async def run():
        changed, game, msgs = await _run(
            h,
            event,
            ["a1", "b2", "c3"],
            lambda g, x, y: None,
            lambda res, pos: None,
            send_msgs=True,
            send_board=True,
        )
        assert changed is True
        assert game is not None
        assert msgs == []
        assert img.sent == 1, "Should render board only once after batch processing"

    asyncio.run(run())


def test_multiline_stops_on_game_over():
    mgr = GameManager()
    game = DummyGameWithOver()
    mgr.create("s", game)
    img = DummyImageService()
    h = GameActionHandler(mgr, img, ban_time=0)
    event = DummyEvent("s")

    async def run():
        changed, game_result, msgs = await _run(
            h,
            event,
            ["a1", "b2", "c3", "d4"],
            lambda g, x, y: g.mark(x, y),
            lambda res, pos: None,
            send_msgs=True,
            send_board=True,
        )
        assert changed is True
        assert game_result is not None
        assert msgs == []
        assert game.action_count == 2, (
            "Should stop processing after 2 actions (when game becomes over)"
        )
        assert img.sent == 1, "Should render board only once after early termination"
        assert game.is_over is True

    asyncio.run(run())
