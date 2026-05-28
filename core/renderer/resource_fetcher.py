from __future__ import annotations

import asyncio
import hashlib
import socket
from pathlib import Path

import aiofiles
import aiohttp
import httpx

from astrbot.api import logger

from ..config import PluginConfig


def _avatar_urls(user_id: str) -> list[str]:
    return [
        f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640",
        f"https://thirdqq.qlogo.cn/g?b=qq&nk={user_id}&s=640",
    ]


async def get_avatar(user_id: str) -> bytes | None:
    """获取头像"""
    timeout = aiohttp.ClientTimeout(total=25, connect=10, sock_connect=10, sock_read=20)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }

    for avatar_url in _avatar_urls(user_id):
        try:
            connector = aiohttp.TCPConnector(family=socket.AF_INET, ttl_dns_cache=300)
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers=headers,
                trust_env=True,
            ) as session:
                async with session.get(avatar_url, allow_redirects=True) as response:
                    response.raise_for_status()
                    return await response.read()
        except Exception as exc:
            logger.warning(f"头像下载重试候选失败: {avatar_url} -> {exc}")

    try:
        async with httpx.AsyncClient(
            timeout=25,
            follow_redirects=True,
            headers=headers,
            trust_env=True,
        ) as client:
            for avatar_url in _avatar_urls(user_id):
                response = await client.get(avatar_url)
                response.raise_for_status()
                return response.content
    except Exception as exc:
        logger.error(f"下载头像失败: {exc}")
        return None

    return None


class ResourceFetcher:
    _RETRY_DELAYS = (0.5, 1.0)

    def __init__(self, config: PluginConfig):
        self.cfg = config
        self.resource_dir = self.cfg.temp_dir / "builtin_renderer"
        self.resource_dir.mkdir(parents=True, exist_ok=True)
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
        }

    async def fetch_url_to_cache(
        self,
        url: str | None,
        *,
        prefix: str,
        suffix: str = ".bin",
    ) -> Path | None:
        if not url:
            return None

        cache_path = self._cache_path(prefix=prefix, key=url, suffix=suffix)
        if cache_path.exists():
            return cache_path

        content = await self._download_bytes(url)
        if content is None:
            return None

        async with aiofiles.open(cache_path, "wb") as file:
            await file.write(content)
        return cache_path

    async def fetch_avatar_to_cache(self, user_id: int | str) -> Path | None:
        user_id_str = str(user_id)
        cache_path = self._cache_path(prefix="avatar", key=user_id_str, suffix=".jpg")
        if cache_path.exists():
            return cache_path

        content = await get_avatar(user_id_str)
        if content is None:
            return None

        async with aiofiles.open(cache_path, "wb") as file:
            await file.write(content)
        return cache_path

    def _cache_path(self, *, prefix: str, key: str, suffix: str) -> Path:
        digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).hexdigest()
        return self.resource_dir / f"{prefix}_{digest}{suffix}"

    async def _download_bytes(self, url: str) -> bytes | None:
        errors: list[str] = []

        for delay in (0.0, *self._RETRY_DELAYS):
            if delay > 0:
                await asyncio.sleep(delay)

            try:
                content = await self._download_with_aiohttp(url)
                if content is not None:
                    return content
            except Exception as exc:
                errors.append(f"aiohttp: {exc}")

        try:
            content = await self._download_with_httpx(url)
            if content is not None:
                return content
        except Exception as exc:
            errors.append(f"httpx: {exc}")

        logger.error(f"下载资源失败: {url} -> {' | '.join(errors)}")
        return None

    async def _download_with_aiohttp(self, url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(
            total=max(self.cfg.timeout, 25),
            connect=min(max(self.cfg.timeout, 10), 15),
            sock_connect=min(max(self.cfg.timeout, 10), 15),
            sock_read=max(self.cfg.timeout, 20),
        )
        connector = aiohttp.TCPConnector(family=socket.AF_INET, ttl_dns_cache=300)
        async with aiohttp.ClientSession(
            timeout=timeout,
            connector=connector,
            headers=self._headers,
            trust_env=True,
        ) as session:
            async with session.get(url, allow_redirects=True) as response:
                response.raise_for_status()
                return await response.read()

    async def _download_with_httpx(self, url: str) -> bytes:
        async with httpx.AsyncClient(
            timeout=max(self.cfg.timeout, 25),
            follow_redirects=True,
            headers=self._headers,
            trust_env=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content
