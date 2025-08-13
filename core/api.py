import asyncio
import base64
import json
from dataclasses import dataclass
from http.cookies import SimpleCookie
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import aiohttp
from aiocqhttp import CQHttp
from astrbot.api import logger

from .utils import normalize_images

BytesOrStr = Union[str, bytes]

# ---------- 工具函数 ----------
def _generate_gtk(skey: str) -> str:
    """生成 QQ 空间 gtk"""
    hash_val = 5381
    for ch in skey:
        hash_val += (hash_val << 5) + ord(ch)
    return str(hash_val & 0x7FFFFFFF)


def _parse_upload_result(payload: dict[str, Any]) -> Tuple[str, str]:
    """从上传返回体里提取 picbo 与 richval"""
    if payload.get("ret") != 0:
        raise RuntimeError("图片上传失败")

    data = payload["data"]
    picbo = data["url"].split("&bo=", 1)[1]

    richval = ",{},{},{},{},{},{},,{},{}".format(
        data["albumid"],
        data["lloc"],
        data["sloc"],
        data["type"],
        data["height"],
        data["width"],
        data["height"],
        data["width"],
    )
    return picbo, richval

class _QzoneURL:
    BASE = "https://user.qzone.qq.com"
    H5_BASE = "https://h5.qzone.qq.com"
    UPLOAD = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
    EMOTION = f"{BASE}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    VISITOR = f"{H5_BASE}/proxy/domain/g.qzone.qq.com/cgi-bin/friendshow/cgi_get_visitor_more"

    # LIKE = f"{H5_BASE}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    # FEED_LIST = f"{H5_BASE}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_msglist_v6"


# ---------- 登录态 ----------
@dataclass(slots=True)
class _Auth:
    uin: int
    skey: str
    p_skey: str
    gtk2: str


# ---------- 主 API ----------
class QzoneAPI:
    """QQ 空间 HTTP API 封装"""

    def __init__(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, ssl=False),
            timeout=aiohttp.ClientTimeout(total=10),
        )
        self._auth: Optional[_Auth] = None

    # ---------------- 统一请求封装 ----------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Dict[str, Any] | None = None,
        data: Dict[str, Any] | None = None,
        headers: Dict[str, str] | None = None,
        timeout: int = 10,
    ) -> aiohttp.ClientResponse:
        """aiohttp 包装"""
        async with self._session.request(
            method.upper(),
            url,
            params=params,
            data=data,
            headers=headers,
            cookies=self._raw_cookies,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            await resp.read()
            return resp

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: Dict[str, Any] | None = None,
        data: Dict[str, Any] | None = None,
        headers: Dict[str, str] | None = None,
        timeout: int = 10,
        strip_callback: bool = True,
    ) -> Dict[str, Any]:
        """自动把响应体反序列化成 dict"""
        resp = await self._request(
            method, url, params=params, data=data, headers=headers, timeout=timeout
        )
        text = await resp.text()
        if strip_callback and text.startswith("_Callback("):
            text = text[10:-2]
        try:
            data, _ = json.JSONDecoder().raw_decode(text.strip())
            if not data:
                raise RuntimeError("返回数据为空")
            return data
        except json.JSONDecodeError as e:
            raise RuntimeError(f"返回不是合法 JSON: {text}") from e

    async def _request_text(self, method: str, url: str, **kw) -> str:
        resp = await self._request(method, url, **kw)
        return await resp.text()

    # ---------------- 登录 ----------------
    async def login(self, client: CQHttp) -> None:
        if self._auth is not None:
            return

        cookie_str = (await client.get_cookies(domain="user.qzone.qq.com")).get(
            "cookies", ""
        )
        cookies = {k: v.value for k, v in SimpleCookie(cookie_str).items()}

        skey = cookies.get("skey", "")
        p_skey = cookies.get("p_skey", "")
        uin = int(cookies.get("uin", "0")[1:])

        if not all((skey, p_skey, uin)):
            raise RuntimeError("QQ 空间 Cookie 缺失")

        self._auth = _Auth(
            uin=uin,
            skey=skey,
            p_skey=p_skey,
            gtk2=_generate_gtk(p_skey),
        )
        logger.info(f"QQ 空间登录成功: {uin}")

    @property
    def _raw_cookies(self) -> Dict[str, str]:
        if self._auth is None:
            return {}
        return {
            "uin": f"o{self._auth.uin}",
            "skey": self._auth.skey,
            "p_skey": self._auth.p_skey,
        }

    # ---------------- 业务方法 ----------------
    async def token_valid(self, client: CQHttp, max_retry: int = 3, backoff: float = 1.0) -> bool:
        """验证当前登录态是否可用"""
        for attempt in range(max_retry):
            try:
                await self.get_visitor(client)
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Token 校验失败(第 {attempt + 1} 次): {exc!r}")
                if attempt < max_retry - 1:
                    await asyncio.sleep(backoff * (2**attempt))
        return False

    async def get_visitor(self, client: CQHttp) -> dict:
        """获取今日/总访客数"""
        await self.login(client)
        assert self._auth is not None
        params = {
            "uin": self._auth.uin,
            "mask": 7,
            "g_tk": self._auth.gtk2,
            "page": 1,
            "fupdate": 1,
            "clear": 1,
        }
        payload = await self._request_json("GET", url=_QzoneURL.VISITOR, params=params)
        return payload

    async def _upload_image(self, image: bytes) -> Dict[str, Any]:
        """上传单张图片，返回原始 JSON"""
        assert self._auth is not None
        data = {
            "filename": "filename",
            "uploadtype": "1",
            "albumtype": "7",
            "skey": self._auth.skey,
            "uin": self._auth.uin,
            "p_skey": self._auth.p_skey,
            "output_type": "json",
            "base64": "1",
            "picfile": base64.b64encode(image).decode(),
        }
        headers = {
            "referer": f"{_QzoneURL.BASE}/{self._auth.uin}",
            "origin": _QzoneURL.BASE,
        }
        resp = await self._request(
            "POST", url=_QzoneURL.UPLOAD, data=data, headers=headers, timeout=60
        )
        if resp.status != 200:
            raise RuntimeError("图片上传请求失败")
        text = await resp.text()
        try:
            return json.loads(text[text.find("{") : text.rfind("}") + 1])
        except Exception as e:
            raise RuntimeError(f"图片上传结果解析失败: {text}") from e

    async def publish_emotion(
        self,
        client: CQHttp,
        content: str,
        images: Sequence[BytesOrStr] | None = None,
    ) -> str:
        """发表说说，返回说说 tid"""
        await self.login(client)
        assert self._auth is not None

        imgs: List[bytes] = await normalize_images(images or [])
        post_data: Dict[str, Any] = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self._auth.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"{_QzoneURL.BASE}/{self._auth.uin}",
        }

        if imgs:
            pic_bos, richvals = [], []
            for img in imgs:
                up_json = await self._upload_image(img)
                picbo, richval = _parse_upload_result(up_json)
                pic_bos.append(picbo)
                richvals.append(richval)

            post_data.update(
                pic_bo=",".join(pic_bos),
                richtype="1",
                richval="\t".join(richvals),
            )

        params = {"g_tk": self._auth.gtk2, "uin": self._auth.uin}
        headers = {
            "referer": f"{_QzoneURL.BASE}/{self._auth.uin}",
            "origin": _QzoneURL.BASE,
        }
        payload = await self._request_json(
            "POST",
            url=_QzoneURL.EMOTION,
            params=params,
            data=post_data,
            headers=headers,
        )
        if payload.get("code") == 0:
            return str(payload["tid"])
        raise RuntimeError(f"发表说说失败: {payload.get('message', payload)}")

    # async def like(self, unikey, curkey, dataid, qztoken):
    #     """给说说点赞"""
    #     assert self._auth is not None
    #     params = {
    #         "g_tk": self._auth.gtk2,
    #         "qzonetoken": qztoken,
    #     }
    #     data = {
    #         "qzreferrer": f"{_QzoneURL.BASE}/{self._auth.uin}",
    #         "opuin": self._auth.uin,
    #         "unikey": str(unikey),
    #         "curkey": str(curkey),
    #         "from": "1",
    #         "appid": "311",
    #         "typeid": "0",
    #         "abstime": str(time.time()),
    #         "fid": str(dataid),
    #         "active": "0",
    #         "fupdate": "1",
    #     }
    #     headers = {
    #         "referer": f"{_QzoneURL.BASE}/{self._auth.uin}",
    #         "origin": _QzoneURL.BASE,
    #     }

    #     payload = await self._request_json(
    #         "POST", url=_QzoneURL.LIKE, params=params, data=data, headers=headers
    #     )
    #     if payload.get("code") != 0:
    #         raise RuntimeError(
    #             f"点赞失败: unikey={unikey}; curkey={curkey}; fid={dataid}"
    #         )

    async def terminate(self) -> None:
        await self._session.close()
