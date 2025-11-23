# qzone_api.py

import base64
import datetime
import json
import re
import time
from http.cookies import SimpleCookie
from typing import Any

import aiohttp
import bs4
import json5
from aiocqhttp import CQHttp

from astrbot.api import logger

from .comment import Comment
from .post import Post
from .utils import normalize_images


class QzoneContext:
    """统一封装 Qzone 请求所需的所有动态参数"""

    def __init__(self, uin: int, skey: str, p_skey: str):
        self.uin = uin
        self.skey = skey
        self.p_skey = p_skey

    @property
    def gtk2(self) -> str:
        """动态计算 gtk2"""
        hash_val = 5381
        for ch in self.p_skey:
            hash_val += (hash_val << 5) + ord(ch)
        return str(hash_val & 0x7FFFFFFF)

    def cookies(self) -> dict[str, str]:
        return {
            "uin": f"o{self.uin}",
            "skey": self.skey,
            "p_skey": self.p_skey,
        }

    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "referer": f"https://user.qzone.qq.com/{self.uin}",
            "origin": "https://user.qzone.qq.com",
            "Host": "user.qzone.qq.com",
            "Connection": "keep-alive",
        }


class Qzone:
    """QQ 空间 HTTP API 封装"""

    BASE_URL = "https://user.qzone.qq.com"
    UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
    EMOTION_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    DOLIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    ZONE_LIST_URL = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"
    VISITOR_URL = "https://h5.qzone.qq.com/proxy/domain/g.qzone.qq.com/cgi-bin/friendshow/cgi_get_visitor_more"
    REPLY_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    DELETE_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_delete_v6"
    DETAIL_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6"

    def __init__(self, client: CQHttp) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, ssl=False),
            timeout=aiohttp.ClientTimeout(total=10),
        )
        self.client = client
        self.ctx: QzoneContext = None  # type: ignore

    async def login(self) -> bool:
        logger.info("正在登录QQ空间...")
        try:
            cookie_str = (
                await self.client.get_cookies(domain="user.qzone.qq.com")
            ).get("cookies", "")
            c = {k: v.value for k, v in SimpleCookie(cookie_str).items()}
            uin = int(c.get("uin", "0")[1:])
            if not uin:
                raise RuntimeError("Cookie 中缺少合法 uin")
            self.ctx = QzoneContext(
                uin=uin, skey=c.get("skey", ""), p_skey=c.get("p_skey", "")
            )
            logger.info(f"登录成功，uin={uin}")
            return True
        except Exception as e:
            logger.error(f"登录失败: {e}")
            return False

    async def ready(self):
        """准备好登录状态"""
        if not self.ctx:
            await self.login()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 10,
        retry_count: int = 0,
        debug: bool = False,
    ) -> tuple[bool, dict]:
        """aiohttp 包装"""
        if retry_count > 2:  # 限制递归深度
            raise RuntimeError("请求失败，重试次数过多")

        if method.upper() not in ["GET", "POST", "PUT", "DELETE"]:
            raise ValueError(f"无效的请求方法: {method}")

        # 发起请求
        async with self._session.request(
            method.upper(),
            url,
            params=params,
            data=data,
            headers=headers or self.ctx.headers(),
            cookies=self.ctx.cookies(),
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            # 状态码处理
            if resp.status not in [200, 401, 403]:
                raise RuntimeError(f"请求失败，状态码: {resp.status}")

            # 处理响应数据
            resp_text = await resp.text()
            if debug:
                logger.debug(f"响应数据: {resp_text}")

            # 尝试解析 JSON
            json_str = ""
            if m := re.search(
                r"callback\s*\(\s*([^{]*(\{.*\})[^)]*)\s*\)", resp_text, re.I | re.S
            ):
                json_str = m.group(2)
            else:
                json_str = resp_text[resp_text.find("{") : resp_text.rfind("}") + 1]
            json_str = json_str.replace("undefined", "null")
            try:
                parse_data = json5.loads(json_str.strip())
                if not isinstance(parse_data, dict):
                    raise RuntimeError("JSON 解析结果不是字典类型")
                if debug:
                    logger.debug(f"解析数据: {parse_data}")
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析错误: {e}")
                raise

            # 重登机制
            code = parse_data.get("code")
            if resp.status in [401, 403] or code == -3000:
                logger.warning(
                    f"请求失败: {resp.status}，解析数据: {parse_data}, 正在尝试重新登录QQ空间..."
                )
                if not await self.login():
                    raise RuntimeError("重新登录失败，无法继续请求")
                # ✅ 重新构造参数（此时 self.ctx 已更新）
                if params:
                    params["g_tk"] = self.ctx.gtk2
                    if "uin" in params:
                        params["uin"] = self.ctx.uin
                if data:
                    data["p_skey"] = self.ctx.p_skey
                    data["skey"] = self.ctx.skey
                    if "uin" in data:
                        data["uin"] = self.ctx.uin
                return await self._request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=headers or self.ctx.headers(),
                    timeout=timeout,
                    retry_count=retry_count + 1,
                )
            if code != 0:
                return False, {"code": code, "message": parse_data.get("message")}
            return True, parse_data

    async def _upload_image(self, image: bytes) -> dict:
        """上传单张图片"""
        await self.ready()
        succ, data = await self._request(
            method="POST",
            url=self.UPLOAD_IMAGE_URL,
            timeout=60,
            data={
                "filename": "filename",
                "uploadtype": "1",
                "albumtype": "7",
                "skey": self.ctx.skey,
                "uin": self.ctx.uin,
                "p_skey": self.ctx.p_skey,
                "output_type": "json",
                "base64": "1",
                "picfile": base64.b64encode(image).decode(),
            },
        )
        if not succ:
            raise RuntimeError("图片上传失败")
        return data

    async def get_visitor(self) -> str:
        """获取访客数"""
        await self.ready()
        succ, data = await self._request(
            method="GET",
            url=self.VISITOR_URL,
            params={
                "uin": self.ctx.uin,
                "mask": 7,
                "g_tk": self.ctx.gtk2,
                "page": 1,
                "fupdate": 1,
                "clear": 1,
            },
        )
        return self.parse_visitors(data) if succ else str(data)

    @staticmethod
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

    async def publish(self, post: Post) -> tuple[bool, dict]:
        """发表说说, 返回tid"""
        await self.ready()
        post_data: dict[str, Any] = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": post.text,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self.ctx.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"{self.BASE_URL}/{self.ctx.uin}",
        }
        if post.images:
            pic_bos, richvals = [], []
            imgs: list[bytes] = await normalize_images(post.images)
            for img in imgs:
                up_json = await self._upload_image(img)
                picbo, richval = self.parse_upload_result(up_json)
                pic_bos.append(picbo)
                richvals.append(richval)

            post_data.update(
                pic_bo=",".join(pic_bos),
                richtype="1",
                richval="\t".join(richvals),
            )

        return await self._request(
            method="POST",
            url=self.EMOTION_URL,
            params={"g_tk": self.ctx.gtk2, "uin": self.ctx.uin},
            data=post_data,
        )

    async def like(self, tid: str, target_id: str) -> tuple[bool, dict]:
        """
        点赞指定说说。

        Args:
            fid (str): 说说的动态ID。
            target_id (str): 目标QQ号。

        """
        await self.ready()
        return await self._request(
            method="POST",
            url=self.DOLIKE_URL,
            params={
                "g_tk": self.ctx.gtk2,
            },
            data={
                "qzreferrer": f"{self.BASE_URL}/{self.ctx.uin}",  # 来源
                "opuin": self.ctx.uin,  # 操作者QQ
                "unikey": f"{self.BASE_URL}/{target_id}/mood/{tid}",  # 动态唯一标识
                "curkey": f"{self.BASE_URL}/{target_id}/mood/{tid}",  # 要操作的动态对象
                "appid": 311,  # 应用ID(说说:311)
                "from": 1,  # 来源
                "typeid": 0,  # 类型ID
                "abstime": int(time.time()),  # 当前时间戳
                "fid": tid,  # 动态ID
                "active": 0,  # 活动ID
                "format": "json",  # 返回格式
                "fupdate": 1,  # 更新标记
            },
        )

    async def comment(
        self, fid: str, target_id: str, content: str
    ) -> tuple[bool, dict]:
        """
        评论指定说说。

        Args:
            fid (str): 说说的动态ID。
            target_id (str): 目标QQ号。
            content (str): 评论的文本内容。

        """
        await self.ready()
        return await self._request(
            "POST",
            url=self.COMMENT_URL,
            params={"g_tk": self.ctx.gtk2},
            data={
                "topicId": f"{target_id}_{fid}__1",  # 说说ID
                "uin": self.ctx.uin,  # botQQ
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
            },
        )

    async def delete(self, tid: str):
        """删除tid对应说说（接口暂时未接通）"""
        await self.ready()
        referer = f"https://user.qzone.qq.com/{self.ctx.uin}/mood/{tid}"
        return await self._request(
            "POST",
            url=self.DELETE_URL,
            params={"g_tk": self.ctx.gtk2},
            data={
                "tid": tid,
                "hostUin": self.ctx.uin,
                "qzreferrer": referer,
                "t1_source": 1,
                "code_version": 1,
                "format": "fs",
                "p_skey": self.ctx.p_skey,
            },
            headers={**self.ctx.headers(), "referer": referer},
        )

    async def get_feeds(
        self, target_id: str, pos: int = 1, num: int = 1
    ) -> tuple[bool, list[Post] | str]:
        """
        获取指定QQ号的好友说说列表

        Args:
            target_id (str): 目标QQ号。
            pos (int): 起始位置。
            num (int): 要获取的说说数量。
        """
        await self.ready()
        logger.info(f"正在获取 {target_id} 的说说列表...")
        succ, data = await self._request(
            method="GET",
            url=self.LIST_URL,
            params={
                "g_tk": self.ctx.gtk2,
                "uin": target_id,  # 目标QQ
                "ftype": 0,  # 全部说说
                "sort": 0,  # 最新在前
                "pos": pos,  # 起始位置
                "num": num,  # 获取条数
                "replynum": 100,  # 评论数
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "json",
                "need_comment": 1,
                "need_private_comment": 1,
            },
        )
        msglist = data.get("msglist", [])
        return succ, self.parse_feeds(msglist) if succ else data["message"]

    async def get_detail(self, post: Post) -> Post:
        """
        获取单条说说详情（含完整评论、转发、图片、视频等）

        Args:
            uin: 目标 QQ 号
            tid: 说说 id（对应 msglist 里的 tid）

        Returns:
            (True, Post) 或 (False, 错误信息)
        """
        await self.ready()
        succ, data = await self._request(
            "GET",
            self.DETAIL_URL,
            params={
                "uin": post.uin,
                "tid": post.tid,
                "format": "jsonp",
                "g_tk": self.ctx.gtk2,
            },
        )
        if succ:
            if posts := self.parse_feeds([data]):
                return posts[0]

        logger.warning(f"获取说说详情失败：{data}")
        return post

    async def get_recent_feeds(self, page: int = 1) -> tuple[bool, list[Post] | str]:
        """
        获取自己的好友说说列表，返回已读与未读的说说列表
        """
        page = 1  # 测试时发现暂时是无效配置，先设为1吧
        await self.ready()
        succ, data = await self._request(
            method="GET",
            url=self.ZONE_LIST_URL,
            params={
                "uin": self.ctx.uin,  # QQ号
                "scope": 0,  # 访问范围
                "view": 1,  # 查看权限
                "filter": "all",  # 全部动态
                "flag": 1,  # 标记
                "applist": "all",  # 所有应用
                "pagenum": page,  # 页码
                "aisortEndTime": 0,  # AI排序结束时间
                "aisortOffset": 0,  # AI排序偏移
                "aisortBeginTime": 0,  # AI排序开始时间
                "begintime": 0,  # 开始时间
                "format": "json",  # 返回格式
                "g_tk": self.ctx.gtk2,  # 令牌
                "useutf8": 1,  # 使用UTF8编码
                "outputhtmlfeed": 1,  # 输出HTML格式
            },
        )
        return succ, self.parse_recent_feeds(data) if succ else data["message"]

    @staticmethod
    def parse_visitors(data: dict) -> str:
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
            # qq = v.get("uin", "0")
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

    def parse_feeds(self, msglist: list[dict]) -> list[Post]:
        """解析说说列表"""
        try:
            posts = []
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
                comments = Comment.build_list(msg.get("commentlist") or [])
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

        except Exception as e:
            logger.error(f"解析说说列表失败: {e}")
            return []

    @staticmethod
    def parse_recent_feeds(data: dict) -> list[Post]:
        """解析最近说说列表"""
        feeds: list = data.get("data", {}).get("data", {})
        if not data:
            return []
        try:
            posts = []
            for feed in feeds:
                if not feed:
                    continue
                # 过滤广告类内容（appid=311）
                appid = str(feed.get("appid", ""))
                if appid != "311":
                    continue
                uin = feed.get("uin", "")
                tid = feed.get("key", "")
                if not uin or not tid:
                    logger.error(f"无效的说说数据: target_qq={uin}, tid={tid}")
                    continue
                create_time = feed.get("abstime", "")
                nickname = feed.get("nickname", "")
                html_content = feed.get("html", "")
                if not html_content:
                    logger.error(f"说说内容为空: UIN={uin}, TID={tid}")
                    continue

                soup = bs4.BeautifulSoup(html_content, "html.parser")

                # 提取文字内容
                text_div = soup.find("div", class_="f-info")
                text = text_div.get_text(strip=True) if text_div else ""
                # 提取转发内容
                rt_con = ""
                txt_box = soup.select_one("div.txt-box")
                if txt_box:
                    # 获取除昵称外的纯文本内容
                    rt_con = txt_box.get_text(strip=True)
                    # 分割掉昵称部分（从第一个冒号开始取内容）
                    if "：" in rt_con:
                        rt_con = rt_con.split("：", 1)[1].strip()
                # 提取图片URL
                image_urls = []
                # 查找所有图片容器
                if img_box := soup.find("div", class_="img-box"):
                    for img in img_box.find_all("img"):  # type: ignore
                        src = img.get("src")  # type: ignore
                        if src and not str(src).startswith(
                            "http://qzonestyle.gtimg.cn"
                        ):  # 过滤表情图标
                            image_urls.append(src)
                # TODO 临时视频处理办法（视频缩略图）
                img_tag = soup.select_one("div.video-img img")
                if img_tag and "src" in img_tag.attrs:
                    image_urls.append(img_tag["src"])
                # 获取视频url
                videos = []
                video_div = soup.select_one("div.img-box.f-video-wrap.play")
                if video_div and "url3" in video_div.attrs:
                    videos.append(video_div["url3"])
                # 获取评论内容
                comments: list[Comment] = []
                # 查找所有评论项（包括主评论和回复）
                comment_items = soup.select("li.comments-item.bor3")
                if comment_items:
                    for item in comment_items:
                        # 提取基本信息
                        data_uin = str(item.get("data-uin", ""))
                        comment_tid = str(item.get("data-tid", ""))
                        nickname = str(item.get("data-nick", ""))

                        # 查找评论内容
                        content_div = item.select_one("div.comments-content")
                        if content_div:
                            # 移除操作按钮（回复/删除）
                            for op in content_div.select("div.comments-op"):
                                op.decompose()
                            # 获取纯文本内容
                            content = content_div.get_text(" ", strip=True).split(
                                ":", 1
                            )[-1]
                        else:
                            content = ""

                        # 提取评论时间（直接使用相对时间字符串）
                        comment_time_span = item.select_one("span.state")
                        comment_time = (
                            comment_time_span.get_text(strip=True)
                            if comment_time_span
                            else ""
                        )

                        # 检查是否是回复
                        parent_tid = None
                        parent_div = item.find_parent("div", class_="mod-comments-sub")
                        if parent_div:
                            parent_li = parent_div.find_parent(
                                "li", class_="comments-item"
                            )
                            if parent_li:
                                parent_tid = str(parent_li.get("data-tid"))  # type: ignore

                        comments.append(
                            Comment(
                                uin=int(data_uin) if data_uin.isdigit() else 0,
                                nickname=nickname,
                                content=content,
                                create_time=0,
                                create_time_str=comment_time,
                                tid=int(comment_tid) if comment_tid.isdigit() else 0,
                                parent_tid=int(parent_tid)
                                if parent_tid and parent_tid.isdigit()
                                else None,
                            )
                        )
                # 构造Post对象
                post = Post(
                    tid=str(tid),
                    uin=int(uin),
                    name=str(nickname),
                    text=text,
                    images=list(set(image_urls)),
                    videos=videos,
                    create_time=create_time,
                    rt_con=rt_con,
                    comments=comments,
                )
                posts.append(post)

            logger.info(f"成功解析 {len(posts)} 条最新说说")
            return posts
        except Exception as e:
            logger.error(f"解析说说错误：{e}")
            return []

    async def terminate(self) -> None:
        await self._session.close()
