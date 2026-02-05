# qzone_api.py

import asyncio
from http.cookies import SimpleCookie

from astrbot.api import logger

from ..config import PluginConfig
from .model import QzoneContext


class QzoneSession:
    """QQ 登录上下文"""

    DOMAIN = "user.qzone.qq.com"

    def __init__(self, config: PluginConfig):
        self.cfg = config
        self._ctx: QzoneContext | None = None
        self._lock = asyncio.Lock()

    async def get_ctx(self) -> QzoneContext:
        async with self._lock:
            if not self._ctx:
                self._ctx = await self.login(self.cfg.cookies_str)
            return self._ctx

    async def get_uin(self) -> int:
        ctx = await self.get_ctx()
        return ctx.uin

    async def get_nickname(self) -> str:
        ctx = await self.get_ctx()
        uin = str(ctx.uin)
        if not self.cfg.client:
            return uin
        try:
            info = await self.cfg.client.get_login_info()
            return info.get("nickname") or uin
        except Exception:
            return uin

    async def invalidate(self) -> None:
        async with self._lock:
            self._ctx = None

    async def login(self, cookies_str: str | None = None) -> QzoneContext:
        logger.info("正在登录 QQ 空间")

        if not cookies_str:
            if not self.cfg.client:
                raise RuntimeError("CQHttp 实例不存在")
            cookies_str = (await self.cfg.client.get_cookies(domain=self.DOMAIN)).get(
                "cookies"
            )
            if not cookies_str:
                raise RuntimeError("获取 Cookie 失败")

            self.cfg.update_cookies(cookies_str)

        c = {k: v.value for k, v in SimpleCookie(cookies_str).items()}
        uin = int(c.get("uin", "0")[1:])
        if not uin:
            raise RuntimeError("Cookie 中缺少合法 uin")

        self._ctx = QzoneContext(
            uin=uin,
            skey=c.get("skey", ""),
            p_skey=c.get("p_skey", ""),
        )

        logger.info(f"登录成功，uin={uin}")
        return self._ctx

