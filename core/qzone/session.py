import asyncio
from http.cookies import SimpleCookie
from time import monotonic

from aiocqhttp import CQHttp

from astrbot.api import logger

from ..config import PluginConfig
from .model import QzoneContext


class QzoneSession:
    """QQ 登录上下文

    每个平台适配器实例各持有一份，cookie 与登录态互不影响；
    多 Bot 场景下不会出现 A 的登录态被 B 使用的情况。
    """

    DOMAIN = "user.qzone.qq.com"

    def __init__(self, config: PluginConfig, platform_id: str = ""):
        self.cfg = config
        self.platform_id = str(platform_id or "")
        self._ctx: QzoneContext | None = None
        self._last_refresh_at: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def label(self) -> str:
        """日志用的平台标识"""
        return self.platform_id or "default"

    def client(self) -> CQHttp:
        """该平台自己的 OneBot 客户端"""
        client = self.cfg.resolve_client(self.platform_id)
        if client is None:
            raise RuntimeError(f"平台 [{self.label}] 的 CQHttp 客户端不可用")
        return client

    async def get_ctx(self) -> QzoneContext:
        async with self._lock:
            if not self._ctx or self._is_cookie_expired():
                self._ctx = await self._refresh_ctx_locked()
            return self._ctx

    async def get_uin(self) -> int:
        ctx = await self.get_ctx()
        return ctx.uin

    async def get_nickname(self) -> str:
        ctx = await self.get_ctx()
        uin = str(ctx.uin)
        try:
            info = await self.client().get_login_info()
            return info.get("nickname") or uin
        except Exception:
            return uin

    async def invalidate(self) -> None:
        async with self._lock:
            self._ctx = None
            self._last_refresh_at = 0.0

    async def login(self) -> QzoneContext:
        logger.info(f"[{self.label}] 正在登录 QQ 空间")
        async with self._lock:
            self._ctx = await self._refresh_ctx_locked()
            logger.info(f"[{self.label}] 登录成功，uin={self._ctx.uin}")
            return self._ctx

    async def _refresh_ctx_locked(self) -> QzoneContext:
        payload = await self.client().get_cookies(domain=self.DOMAIN)
        cookies_str = ""
        if isinstance(payload, dict):
            cookies_str = str(payload.get("cookies") or "").strip()
        if not cookies_str:
            raise RuntimeError(f"平台 [{self.label}] 的 get_cookies 未返回可用 Cookie")

        c = {k: v.value for k, v in SimpleCookie(cookies_str).items()}
        uin_text = c.get("uin", "0")
        uin_raw = uin_text[1:] if uin_text[:1].lower() == "o" else uin_text
        uin = int(uin_raw) if uin_raw.isdigit() else 0
        if not uin:
            raise RuntimeError(f"平台 [{self.label}] 的 Cookie 中缺少合法 uin")

        self._last_refresh_at = monotonic()
        return QzoneContext(
            uin=uin,
            skey=c.get("skey", ""),
            p_skey=c.get("p_skey", "") or c.get("skey", ""),
        )

    def _is_cookie_expired(self) -> bool:
        ttl = max(int(self.cfg.cookie_ttl), 0)
        if ttl <= 0:
            return False
        if self._last_refresh_at <= 0:
            return True
        return monotonic() - self._last_refresh_at >= ttl
