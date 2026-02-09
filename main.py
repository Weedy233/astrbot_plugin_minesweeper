import asyncio
import re
import shutil
import threading

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import Image, Plain
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.game import GameManager, MineSweeper
from .core.model import MarkResult, OpenResult
from .core.renderer import MineSweeperRenderer
from .core.skin import SkinManager
from .core.config import PluginConfig
from .core.utils import detect_desktop, parse_position, set_group_ban
from .sender import MessageSender


class MinesweeperPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config, context)
        self.skin_mgr = SkinManager(self.cfg)
        self.game_mgr = GameManager()
        self.sender = MessageSender(config)

        self._cleanup_task: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    async def initialize(self):
        """插件加载时"""
        self.loop = asyncio.get_running_loop()
        await self.skin_mgr.initialize()
        logger.info("[扫雷] 插件已加载")

    async def terminate(self):
        """插件卸载时"""
        if self.cfg.cache_dir.exists():
            shutil.rmtree(self.cfg.cache_dir)

    def _save_img_bytes(self, event: AstrMessageEvent, img_bytes: bytes) -> str:
        """把图片 bytes 落盘，返回绝对路径"""
        sid = event.session_id
        uid = event.get_sender_id()
        fname = f"{sid}_{uid}.png"
        fpath = self.cfg.cache_dir / fname
        fpath.write_bytes(img_bytes)
        return str(fpath.absolute())

    @filter.command("扫雷", alias={"开始扫雷"})
    async def start_minesweeper(
        self,
        event: AstrMessageEvent,
        level_name: str = "",
        skin_index: int | None = None,
    ):
        sid = event.session_id

        if self.game_mgr.is_running(sid):
            yield event.plain_result("你已经在进行扫雷游戏了")
            return

        if self.cfg.is_supported_level(level_name):
            yield event.plain_result(f"难度仅支持：{self.cfg.level_keys}")
            return
        spec = self.cfg.get_spec(level_name)

        skin_name = (
            self.skin_mgr.get_skin_by_index(skin_index - 1)
            if skin_index
            else self.cfg.default_skin
        )
        skin = self.skin_mgr.load(skin_name, spec)

        renderer = MineSweeperRenderer(
            spec=spec,
            skin=skin,
            font_path=str(self.cfg.font_path),
        )

        game = MineSweeper(spec, renderer)
        self.game_mgr.create(sid, game)

        def send_board():
            img_bytes = game.draw()
            img_path = self._save_img_bytes(event, img_bytes)
            asyncio.run_coroutine_threadsafe(
                self.sender.send_img_replace_last(event, img_path),
                self.loop,  # type: ignore
            )

        game.on_send_board(send_board)

        if self.cfg.use_gui and detect_desktop():
            from .core.gui import start_gui

            threading.Thread(
                target=start_gui,
                args=(game,),
                daemon=True,
            ).start()

        yield event.chain_result(
            [
                Plain("扫雷游戏开始！"),
                Image.fromBytes(game.draw()),
                Plain(
                    "a1b2c3 —— 挖开格子\n"
                    "标雷 c4 —— 标记地雷\n"
                    "雷盘 —— 查看棋盘\n"
                    "结束扫雷 —— 结束游戏"
                ),
            ]
        )

    @filter.command("结束扫雷")
    async def stop_minesweeper(self, event: AstrMessageEvent):
        if not self.game_mgr.is_running(event.session_id):
            yield event.plain_result("当前没有进行中的扫雷游戏")
            return
        self.game_mgr.stop(event.session_id)
        yield event.plain_result("已结束扫雷游戏")

    @filter.regex(r"^雷盘$")
    async def show_minesweeper(self, event: AstrMessageEvent):
        game = self.game_mgr.get(event.session_id)
        if not game:
            return

        yield event.chain_result([Image.fromBytes(game.draw())])

    @filter.regex(r"^([a-zA-Z][0-9]+)(\s*[a-zA-Z][0-9]+)*$")
    async def open_minesweeper(self, event: AstrMessageEvent):
        game = self.game_mgr.get(event.session_id)
        if not game:
            return

        positions = re.findall(r"[a-zA-Z][0-9]+", event.message_str)
        msgs = []

        for pos in positions:
            xy = parse_position(pos)
            if not xy:
                msgs.append(f"位置 {pos} 不合法")
                continue

            res = game.open(*xy)

            if res == OpenResult.OUT:
                msgs.append(f"{pos} 超出边界")
            elif res == OpenResult.FAIL:
                msgs.append("很遗憾，游戏失败")
            elif res == OpenResult.WIN:
                msgs.append("恭喜你获得游戏胜利！")

            if game.is_over:
                self.game_mgr.stop(event.session_id)
                break

        if msgs:
            yield event.plain_result("\n".join(msgs))

        img_path = self._save_img_bytes(event, game.draw())
        await self.sender.send_img_replace_last(event, img_path)

        if (
            game.is_fail
            and isinstance(event, AiocqhttpMessageEvent)
            and self.cfg.ban_time > 0
        ):
            await set_group_ban(event, ban_time=self.cfg.ban_time)

    @filter.regex(r"^标雷(\s*[a-zA-Z][0-9]+)+$")
    async def mark_minesweeper(self, event: AstrMessageEvent):
        game = self.game_mgr.get(event.session_id)
        if not game:
            return

        positions = re.findall(r"[a-zA-Z][0-9]+", event.message_str)
        msgs = []

        for pos in positions:
            xy = parse_position(pos)
            if not xy:
                msgs.append(f"{pos} 不合法")
                continue

            res = game.mark(*xy)

            if res == MarkResult.OUT:
                msgs.append(f"{pos} 超出边界")
            elif res == MarkResult.OPENED:
                msgs.append(f"{pos} 已挖开，不能标记")
            elif res == MarkResult.WIN:
                msgs.append("恭喜你获得游戏胜利！")
                self.game_mgr.stop(event.session_id)
                break

        if msgs:
            yield event.plain_result("\n".join(msgs))

        img_path = self._save_img_bytes(event, game.draw())
        await self.sender.send_img_replace_last(event, img_path)
