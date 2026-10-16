"""与插件配置绑定的命令门禁过滤器。

⚠️ 为什么不能直接用 filter() 的 cfg 参数读插件配置：

AstrBot 在 waking_check 阶段调用过滤器时传进来的是**会话配置档案(abconf)**，
不是插件自己的配置——

    # astrbot/core/core_lifecycle.py
    PipelineContext(ab_config, self.plugin_manager, conf_id, self.db)
    # ab_config = astrbot_config_mgr.confs[conf_id]
    # astrbot/core/pipeline/waking_check/stage.py
    filter.filter(event, self.ctx.astrbot_config)

所以 `cfg.get("allowed_platform_ids")` 这类键在 abconf 里根本不存在，
读出来恒为 None → 门禁静默失效。本模块改为通过模块级弱引用取到插件实例，
直接读它自己的 PluginConfig。
"""

from __future__ import annotations

import weakref
from typing import Any

from astrbot.api import logger
from astrbot.core.config import AstrBotConfig
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.filter.custom_filter import CustomFilter

from .utils import get_event_platform_id

# 由 QzonePlugin.__init__ 通过 set_active_plugin() 登记；弱引用不阻止回收
_ACTIVE_PLUGIN_REF: weakref.ReferenceType | None = None


def set_active_plugin(plugin: Any) -> None:
    """
    登记当前插件实例，供过滤器读取插件自己的配置。

    用弱引用避免延长插件生命周期；万一对象不支持弱引用（带 __slots__ 的
    类型等），退化成一个持有强引用的取值函数，绝不让插件加载阶段抛错。
    """
    global _ACTIVE_PLUGIN_REF
    if plugin is None:
        _ACTIVE_PLUGIN_REF = None
        return
    try:
        _ACTIVE_PLUGIN_REF = weakref.ref(plugin)
    except TypeError:
        logger.warning(
            f"插件实例 {type(plugin).__name__} 不支持弱引用，门禁过滤器改用强引用"
        )
        _ACTIVE_PLUGIN_REF = lambda: plugin  # noqa: E731


def get_active_plugin() -> Any:
    if _ACTIVE_PLUGIN_REF is None:
        return None
    try:
        return _ACTIVE_PLUGIN_REF()
    except TypeError:
        return None


class _PluginConfigFilter(CustomFilter):
    """
    基类：把判定委托给「插件自己的配置」。

    子类实现 decide(cfg, event)，其中 cfg 是 PluginConfig。

    判定无法进行时（拿不到插件实例 / 配置）按 ``fail_closed`` 兜底：
    这两个门禁都是「限制功能外泄」用的，拦截比放行安全——放行会让
    白名单外的 Bot 也去操作自己的真实 QQ 空间，属于用户明确不想要的行为。
    """

    fail_closed: bool = True

    def decide(self, cfg: Any, event: AstrMessageEvent) -> bool:
        raise NotImplementedError

    def filter(self, event: AstrMessageEvent, cfg: AstrBotConfig) -> bool:
        # 注意：这里的 cfg 是 abconf，不是插件配置，故意不使用
        plugin_cfg = getattr(get_active_plugin(), "cfg", None)
        if plugin_cfg is None:
            logger.error(
                f"[{self.__class__.__name__}] 未取得插件配置，"
                f"本次按{'拦截' if self.fail_closed else '放行'}处理"
            )
            return not self.fail_closed
        try:
            return bool(self.decide(plugin_cfg, event))
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] 判定失败({type(e).__name__}: {e})，"
                f"本次按{'拦截' if self.fail_closed else '放行'}处理"
            )
            return not self.fail_closed


class PlatformAllowedFilter(_PluginConfigFilter):
    """
    只放行 `allowed_platform_ids` 中列出的平台适配器实例。

    同一个插件实例会被所有 aiocqhttp 平台适配器共享，若不做限制，
    多个 Bot 会同时响应同一批指令、并把事件交给同一个 QQ 空间账号处理。
    白名单为空时一律放行，保证单 Bot 场景行为与旧版本一致。
    """

    def decide(self, cfg: Any, event: AstrMessageEvent) -> bool:
        return cfg.is_platform_allowed(get_event_platform_id(event))


class CampusWallEnabledFilter(_PluginConfigFilter):
    """
    表白墙投稿入口开关（campus_wall_enable）。

    关闭后「投稿 / 匿名投稿 / 撤稿」对群友静默不响应；
    稿件审核命令「看稿 / 过稿 / 拒稿」不在此门禁内，仍可供「写说说」流程使用。
    """

    def decide(self, cfg: Any, event: AstrMessageEvent) -> bool:
        return bool(cfg.campus_wall_enable)
