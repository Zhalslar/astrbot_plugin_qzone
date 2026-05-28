from __future__ import annotations

from pathlib import Path

from astrbot.api import logger

from ..config import PluginConfig
from ..model import Post


class PillowmdMessageRenderer:
    def __init__(self, config: PluginConfig):
        self.cfg = config
        self.style = None
        self._load_renderer()

    def _load_renderer(self) -> None:
        try:
            import pillowmd

            self.style = pillowmd.LoadMarkdownStyles(self.cfg.style_dir)
        except Exception as exc:
            logger.error(f"无法加载 pillowmd 样式：{exc}")

    async def render_post(self, post: Post) -> Path | None:
        if not self.style:
            return None

        img = await self.style.AioRender(text=post.to_str(), useImageUrl=True)
        return img.Save(self.cfg.temp_dir)

    async def render_text(self, text: str) -> Path | None:
        if not self.style:
            return None

        img = await self.style.AioRender(text=text, useImageUrl=True)
        return img.Save(self.cfg.temp_dir)
