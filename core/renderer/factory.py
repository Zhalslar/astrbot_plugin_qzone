from __future__ import annotations

from astrbot.api import logger

from ..config import PluginConfig
from .builtin_renderer import BuiltinQzoneCardRenderer
from .pillowmd_renderer import PillowmdMessageRenderer
from .protocol import MessageRenderer


def create_message_renderer(config: PluginConfig) -> MessageRenderer:
    if config.use_builtin_renderer:
        try:
            return BuiltinQzoneCardRenderer(config)
        except Exception as exc:
            logger.error(f"初始化内置渲染器失败，回退到 pillowmd：{exc}")

    return PillowmdMessageRenderer(config)
