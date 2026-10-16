from astrbot.core.config import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.filter.custom_filter import CustomFilter

from .utils import get_event_platform_id


class PlatformAllowedFilter(CustomFilter):
    """
    只放行 `allowed_platform_ids` 中列出的平台适配器实例。

    同一个插件实例会被所有 aiocqhttp 平台适配器共享，若不做限制，
    多个 Bot 会同时响应同一批指令、并把事件交给同一个 QQ 空间账号处理。
    白名单为空时一律放行，保证单 Bot 场景行为与旧版本一致。
    """

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        allowed = [
            str(item).strip()
            for item in (cfg.get("allowed_platform_ids") or [])
            if str(item).strip()
        ]
        if not allowed:
            return True
        return get_event_platform_id(event) in allowed
