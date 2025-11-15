# qzone_api.py

import asyncio
import base64
import datetime
import json
import re
import time
from http.cookies import SimpleCookie
from typing import Any

import aiohttp
from aiocqhttp import CQHttp

from astrbot.api import logger

from .post import Post
from .utils import normalize_images


# ---------- 工具函数 ----------
def generate_gtk(skey: str) -> str:
    """生成 QQ 空间 gtk"""
    hash_val = 5381
    for ch in skey:
        hash_val += (hash_val << 5) + ord(ch)
    return str(hash_val & 0x7FFFFFFF)


def parse_upload_result(payload: dict[str, Any]) -> tuple[str, str]:
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


class Qzone:
    """QQ 空间 HTTP API 封装"""

    BASE_URL = "https://user.qzone.qq.com"
    H5_BASE_URL = "https://h5.qzone.qq.com"
    UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
    EMOTION_URL = (
        f"{BASE_URL}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    )
    VISITOR_URL = f"{H5_BASE_URL}/proxy/domain/g.qzone.qq.com/cgi-bin/friendshow/cgi_get_visitor_more"
    DOLIKE_URL = (
        f"{H5_BASE_URL}/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    )
    LIST_URL = f"{BASE_URL}/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    COMMENT_URL = (
        f"{BASE_URL}/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    )

    def __init__(self) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, ssl=False),
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def login(self, client: CQHttp) -> bool:
        """登录QQ空间"""
        try:
            cookie_str = (await client.get_cookies(domain="user.qzone.qq.com")).get(
                "cookies", ""
            )
            self.cookies = {k: v.value for k, v in SimpleCookie(cookie_str).items()}
            self.skey = self.cookies.get("skey", "")
            self.p_skey = self.cookies.get("p_skey", "")
            self.uin = int(self.cookies.get("uin", "0")[1:])
            self.gtk2 = generate_gtk(self.p_skey)
            self.raw_cookies = {
                "uin": f"o{self.uin}",
                "skey": self.skey,
                "p_skey": self.p_skey,
            }
            self.headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                "referer": f"{self.BASE_URL}/{self.uin}",
                "origin": f"{self.BASE_URL}",
            }
            logger.info(f"Qzone 登录成功: {self.cookies}")
            return True
        except Exception as e:
            logger.error(f"Qzone 登录失败: {e}")
            return False

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 10,
    ) -> dict[str, Any]:
        """aiohttp 包装"""
        async with self._session.request(
            method.upper(),
            url,
            params=params,
            data=data,
            headers=headers or self.headers,
            cookies=self.raw_cookies,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                raise RuntimeError("请求失败")
            text = await resp.text()
            if m := re.search(
                r"callback\s*\(\s*([^{]*(\{.*\})[^)]*)\s*\)", text, re.I | re.S
            ):
                json_str = m.group(2)
            else:
                json_str = text[text.find("{") : text.rfind("}") + 1]
            return json.loads(json_str.strip() or text)

    async def token_valid(self, max_retry: int = 3, backoff: float = 1.0) -> bool:
        """验证当前登录态是否可用"""
        for attempt in range(max_retry):
            try:
                await self.get_visitor()
                return True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Token 校验失败(第 {attempt + 1} 次): {exc!r}")
                if attempt < max_retry - 1:
                    await asyncio.sleep(backoff * (2**attempt))
        return False

    async def _upload_image(self, image: bytes) -> dict[str, Any]:
        """上传单张图片"""
        data = {
            "filename": "filename",
            "uploadtype": "1",
            "albumtype": "7",
            "skey": self.skey,
            "uin": self.uin,
            "p_skey": self.p_skey,
            "output_type": "json",
            "base64": "1",
            "picfile": base64.b64encode(image).decode(),
        }
        headers = {
            "referer": f"{self.BASE_URL}/{self.uin}",
            "origin": self.BASE_URL,
        }
        res = await self._request(
            "POST", url=self.UPLOAD_IMAGE_URL, data=data, headers=headers, timeout=60
        )
        return res

    async def get_visitor(self) -> dict:
        """获取今日/总访客数"""
        params = {
            "uin": self.uin,
            "mask": 7,
            "g_tk": self.gtk2,
            "page": 1,
            "fupdate": 1,
            "clear": 1,
        }
        res = await self._request("GET", url=self.VISITOR_URL, params=params)
        return res

    def parse_qzone_visitors(self, data: dict) -> str:
        """
        把 QQ 空间访客接口的数据解析成易读文本。
        """
        lines = []

        # 1. 统计摘要
        lines.append(f"📊 今日访客：{data.get('todaycount', 0)} 人")
        lines.append(f"📈 最近 30 天访客：{data.get('totalcount', 0)} 人")
        lines.append("")

        # 2. 逐条访客
        items = data.get("items", [])
        if not items:
            lines.append("暂无访客记录")
            return "\n".join(lines)

        lines.append("👀 最近来访明细：")
        for idx, v in enumerate(items, 1):
            # 基本信息
            name = v.get("name", "匿名")
            qq = v.get("uin", "0")
            ts = v.get("time", 0)
            dt = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")

            # 渠道
            src_map = {
                0: "访问空间",
                13: "查看动态",
                32: "手机QQ",
                41: "国际版QQ/TIM",
            }
            src = src_map.get(v.get("src"), f"未知({v.get('src')})")

            # 黄钻
            yellow = v.get("yellow", -1)
            vip_info = f"(LV{yellow})" if yellow > 0 else ""

            # 隐身
            hide = " (隐身)" if v.get("is_hide_visit") else ""

            lines.append(f"\n·{dt}\n{name}{vip_info}{hide}{src}")

            # 说说快照
            shuos = v.get("shuoshuoes", [])
            if shuos:
                title = shuos[0].get("name", "")
                lines.append(f"   └─ 说说：{title}")

            # 带来的人
            brought = v.get("uins", [])
            if brought:
                names = ",".join(u.get("name", "") for u in brought)
                lines.append(f"   └─ 带来了{names}")

        return "\n".join(lines)

    async def publish_emotion(self, text: str, images: list[str] | None = None) -> str:
        """发表说说, 返回tid"""
        post_data: dict[str, Any] = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": text,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"{self.BASE_URL}/{self.uin}",
        }
        if images:
            pic_bos, richvals = [], []
            imgs: list[bytes] = await normalize_images(images)
            for img in imgs:
                up_json = await self._upload_image(img)
                picbo, richval = parse_upload_result(up_json)
                pic_bos.append(picbo)
                richvals.append(richval)

            post_data.update(
                pic_bo=",".join(pic_bos),
                richtype="1",
                richval="\t".join(richvals),
            )

        params = {"g_tk": self.gtk2, "uin": self.uin}

        res = await self._request(
            "POST", url=self.EMOTION_URL, params=params, data=post_data
        )
        return res.get("tid", "")

    async def like(self, fid: str, target_id: str):
        """
        点赞指定说说。

        Args:
            fid (str): 说说的动态ID。
            target_id (str): 目标QQ号。

        """
        post_data = {
            "qzreferrer": f"{self.BASE_URL}/{self.uin}",  # 来源
            "opuin": self.uin,  # 操作者QQ
            "unikey": f"{self.BASE_URL}/{target_id}/mood/{fid}",  # 动态唯一标识
            "curkey": f"{self.BASE_URL}/{target_id}/mood/{fid}",  # 要操作的动态对象
            "appid": 311,  # 应用ID(说说:311)
            "from": 1,  # 来源
            "typeid": 0,  # 类型ID
            "abstime": int(time.time()),  # 当前时间戳
            "fid": fid,  # 动态ID
            "active": 0,  # 活动ID
            "format": "json",  # 返回格式
            "fupdate": 1,  # 更新标记
        }
        res = await self._request(
            method="POST",
            url=self.DOLIKE_URL,
            params={
                "g_tk": self.gtk2,
            },
            data=post_data,
        )
        return res

    async def comment(self, fid: str, target_id: str, content: str):
        """
        评论指定说说。

        Args:
            fid (str): 说说的动态ID。
            target_id (str): 目标QQ号。
            content (str): 评论的文本内容。

        """
        post_data = {
            "topicId": f"{target_id}_{fid}__1",  # 说说ID
            "uin": self.uin,  # botQQ
            "hostUin": target_id,  # 目标QQ
            "feedsType": 100,  # 说说类型
            "inCharset": "utf-8",  # 字符集
            "outCharset": "utf-8",  # 字符集
            "plat": "qzone",  # 平台
            "source": "ic",  # 来源
            "platformid": 52,  # 平台id
            "format": "fs",  # 返回格式
            "ref": "feeds",  # 引用
            "content": content,  # 评论内容
        }
        res = await self._request(
            method="POST",
            url=self.COMMENT_URL,
            params={
                "g_tk": self.gtk2,
            },
            data=post_data,
        )
        return res

    def _get_comments(self, msg: dict):
        comments = []
        for comment in msg.get("commentlist") or []:
            comment_time = comment.get("createTime", "") or comment.get(
                "createTime2", ""
            )

            for sub_comment in comment.get("list_3") or []:
                sub_content = sub_comment.get("content", "")
                sub_nickname = sub_comment.get("name", "")
                sub_uin = sub_comment.get("uin", "")
                sub_tid_value = sub_comment.get("tid")
                sub_time = sub_comment.get("createTime", "") or comment.get(
                    "createTime2", ""
                )
                comments.append(
                    {
                        "content": sub_content,
                        "qq_account": str(sub_uin),
                        "nickname": sub_nickname,
                        "comment_tid": sub_tid_value,
                        "created_time": sub_time,
                        "parent_tid": comment.get("tid"),
                    }
                )

            comments.append(
                {
                    "content": comment.get("content", ""),
                    "qq_account": comment.get("uin", ""),
                    "nickname": comment.get("name", ""),
                    "comment_tid": comment.get("tid"),
                    "created_time": comment_time,
                    "parent_tid": None,
                }
            )
        return comments

    async def get_qzones(
        self, target_id: str, pos: int = 1, num: int = 1
    ) -> list[Post]:
        """
        获取指定QQ号的好友说说列表，返回转化后的 Post 列表。

        Args:
            target_id (str): 目标QQ号。
            num (int): 要获取的说说数量。

        Returns:
            list[dict[str, Any]]: 包含说说信息的字典列表，每条字典包含说说的ID（tid）、发布时间（created_time）、内容（content）、图片描述（images）、视频url（videos）及转发内容（rt_con）。
        """
        logger.info(f"正在获取 {target_id} 的说说列表...")
        data = await self._request(
            method="GET",
            url=self.LIST_URL,
            params={
                "g_tk": self.gtk2,
                "uin": target_id,  # 目标QQ
                "ftype": 0,  # 全部说说
                "sort": 0,  # 最新在前
                "pos": pos,  # 起始位置
                "num": num,  # 获取条数
                "replynum": 100,  # 评论数
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "jsonp",
                "need_comment": 1,
                "need_private_comment": 1,
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.124 Safari/537.36",
                "Referer": f"https://user.qzone.qq.com/{target_id}",
                "Host": "user.qzone.qq.com",
                "Connection": "keep-alive",
            },
        )
        if data.get("code") != 0:
            raise Exception(f"说说获取失败: {data}")

        posts = []
        msglist = data.get("msglist") or []
        for msg in msglist:
            logger.debug(msg)
            # 提取图片信息
            image_urls = []
            for img_data in msg.get("pic", []):
                for key in ("url2", "url3", "url1", "smallurl"):
                    if raw := img_data.get(key):
                        image_urls.append(raw)
                        break
            # 读取视频封面（按图片处理）
            for video in msg.get("video") or []:
                video_image_url = video.get("url1") or video.get("pic_url")
                image_urls.append(video_image_url)
            # 提取视频播放地址
            video_urls = []
            for video in msg.get("video") or []:
                url = video.get("url3")
                if url:
                    video_urls.append(url)
            # 提取转发内容
            rt_con = msg.get("rt_con", {}).get("content", "")
            # 提取评论
            comments = self._get_comments(msg)
            # 构造Post对象
            post = Post(
                tid=msg.get("tid", 0),
                uin=msg.get("uin", 0),
                name=msg.get("name", ""),
                gin=0,
                text=msg.get("content", "").strip(),
                images=image_urls,
                videos=video_urls,
                anon=False,
                status="approved",
                create_time=msg.get("created_time", 0),
                rt_con=rt_con,
                comments=comments,
                extra_text=msg.get("source_name"),
            )
            posts.append(post)

        return posts

    async def terminate(self) -> None:
        await self._session.close()
