import asyncio
import re
import shutil

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig

from .core.command_handler import CommandHandler
from .core.config import PluginConfig
from .core.game import GameManager
from .core.gui_launcher import GuiLauncher
from .core.image_service import ImageService
from .core.skin import SkinManager


class MinesweeperPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.cfg = PluginConfig(config, context)
        self.skin_mgr = SkinManager(self.cfg)
        self.game_mgr = GameManager()

        self._cmd_handler: CommandHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._mark_regex = None

    async def initialize(self):
        """插件加载时"""
        self._loop = asyncio.get_running_loop()
        await self.skin_mgr.initialize()

        image_service = ImageService(self.cfg.astrbot_config, self.cfg.cache_dir)
        gui_launcher = GuiLauncher(self.cfg.use_gui)

        self._cmd_handler = CommandHandler(
            cfg=self.cfg,
            skin_mgr=self.skin_mgr,
            game_mgr=self.game_mgr,
            image_service=image_service,
            gui_launcher=gui_launcher,
            loop=self._loop,
        )

        self._build_mark_regex()
        logger.info("[扫雷] 插件已加载")

    def _build_mark_regex(self):
        """根据配置构建标雷正则"""
        prefix = self.cfg.mark_pattern
        self._mark_regex = re.compile(rf"^{prefix}\s*[a-zA-Z]")

    async def terminate(self):
        """插件卸载时"""
        if self.cfg.cache_dir.exists():
            shutil.rmtree(self.cfg.cache_dir)

    @filter.command("扫雷", alias={"开始扫雷"})
    async def start_minesweeper(
        self,
        event,
        arg1: str = "",
        arg2: str = "",
        arg3: str = "",
        arg4: str = "",
        arg5: str = "",
    ):
        if not self._cmd_handler:
            logger.error("[扫雷] 命令处理器未初始化")
            return
        args = [arg for arg in [arg1, arg2, arg3, arg4, arg5] if arg]
        logger.debug(f"[扫雷] 开始游戏命令，参数：{args}")
        async for result in self._cmd_handler.start_game(event, args):
            yield result

    @filter.command("结束扫雷")
    async def stop_minesweeper(self, event):
        if not self._cmd_handler:
            return
        logger.debug(f"[扫雷] 结束游戏命令，用户：{event.get_sender_id()}")
        result = self._cmd_handler.stop_game(event)
        yield event.plain_result(result)

    @filter.regex(r"^雷盘$")
    async def show_minesweeper(self, event):
        if not self._cmd_handler:
            return
        logger.debug(f"[扫雷] 查看棋盘命令，用户：{event.get_sender_id()}")
        result = await self._cmd_handler.show_board(event)
        if result:
            yield result

    @filter.regex(r"^[a-zA-Z].*$")
    async def open_minesweeper(self, event):
        if not self._cmd_handler or not self._mark_regex:
            return
        text = event.message_str.strip()
        if self._mark_regex.match(text):
            return
        tokens = self._extract_positions(text, allow_mark=False)
        if not tokens:
            return
        logger.debug(f"[扫雷] 挖开命令，原始消息：{event.message_str}")
        await self._cmd_handler.open_positions(event, tokens)

    @filter.regex(r"^.*$")
    async def mark_minesweeper(self, event):
        if not self._cmd_handler or not self._mark_regex:
            return
        text = event.message_str.strip()
        if not self._mark_regex.match(text):
            return
        prefix = self._get_mark_prefix(text)
        tokens = self._extract_positions(text, allow_mark=True, prefix=prefix)
        logger.debug(f"[扫雷] 标雷命令，原始消息：{event.message_str}, 前缀：{prefix}")
        await self._cmd_handler.mark_positions(event, tokens)

    def _get_mark_prefix(self, text: str) -> str | None:
        """获取标雷前缀，支持自定义快捷键"""
        for shortcut in self.cfg.mark_shortcuts:
            if text.startswith(shortcut):
                return shortcut
        if text.startswith("标雷"):
            return "标雷"
        return None

    @staticmethod
    def _extract_positions(
        text: str, allow_mark: bool = False, prefix: str | None = None
    ) -> list[str]:
        from .core.utils import tokenize_positions

        text = text.strip()
        if allow_mark and prefix:
            text = text[len(prefix) :]
        return tokenize_positions(text.strip())
