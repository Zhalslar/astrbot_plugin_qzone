
from typing import Any

import aiohttp

from astrbot.api import logger

from ..config import PluginConfig
from .parser import QzoneParser
from .session import QzoneSession


class QzoneHttpClient:
    def __init__(self, session: QzoneSession, config: PluginConfig):
        self.cfg = config
        self.session = session
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.cfg.timeout)
        )

    async def close(self):
        await self._session.close()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        retry: int = 0,
    ) -> dict[str, Any]:
        ctx = await self.session.get_ctx()
        async with self._session.request(
            method,
            url,
            params=params,
            data=data,
            headers=headers or ctx.headers(),
            cookies=ctx.cookies(),
            timeout=timeout,
        ) as resp:
            text = await resp.text()

        parsed = QzoneParser.parse_response(text)
        parsed["_http_status"] = resp.status

        # 仅在明确登录失效时触发重登
        if resp.status == 401 or parsed.get("code") == -3000:
            if retry >= 2:
                raise RuntimeError("登录失效，重试失败")

            logger.warning("登录失效，重新登录中")
            await self.session.login()
            return await self.request(
                method,
                url,
                params=params,
                data=data,
                headers=headers,
                retry=retry + 1,
            )

        if resp.status == 403 and parsed.get("code") in (-1, None):
            parsed["code"] = 403
            parsed["message"] = "权限不足"

        return parsed
