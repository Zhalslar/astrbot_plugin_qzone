# qzone_api.py

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
    EMOTION_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    VISITOR_URL = "https://h5.qzone.qq.com/proxy/domain/g.qzone.qq.com/cgi-bin/friendshow/cgi_get_visitor_more"
    DOLIKE_URL = "https://h5.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    ZONE_LIST_URL = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"

    def __init__(self, client: CQHttp) -> None:
        self._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, ssl=False),
            timeout=aiohttp.ClientTimeout(total=10),
        )
        self.client = client
        self.skey = ""
        self.p_skey = ""
        self.uin = 0
        self.gtk2 = ""
        self.raw_cookies = {}
        self.headers = {}

    async def login(self) -> bool:
        """登录QQ空间"""
        try:
            cookie_str = (
                await self.client.get_cookies(domain="user.qzone.qq.com")
            ).get("cookies", "")
            cookies = {k: v.value for k, v in SimpleCookie(cookie_str).items()}
            self.skey = cookies.get("skey", "")
            self.p_skey = cookies.get("p_skey", "")
            self.uin = int(cookies.get("uin", "0")[1:])
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
            logger.info(f"Qzone 登录成功: {cookies}")
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
        retry_count: int = 0,
    ) -> dict:
        """aiohttp 包装"""
        if retry_count > 3:  # 限制递归深度
            raise RuntimeError("请求失败，重试次数过多")

        if method.upper() not in ["GET", "POST", "PUT", "DELETE"]:
            raise ValueError(f"无效的请求方法: {method}")

        async with self._session.request(
            method.upper(),
            url,
            params=params,
            data=data,
            headers=headers or self.headers,
            cookies=self.raw_cookies,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status not in [200, 401, 403]:
                raise RuntimeError(f"请求失败，状态码: {resp.status}")
            resp_text = await resp.text()
            logger.debug(f"原始数据: {resp_text}")
            json_str = ""
            if m := re.search(
                r"callback\s*\(\s*([^{]*(\{.*\})[^)]*)\s*\)", resp_text, re.I | re.S
            ):
                json_str = m.group(2)
            else:
                json_str = resp_text[resp_text.find("{") : resp_text.rfind("}") + 1]

            try:
                parse_data = json.loads(json_str.strip() or resp_text)
                code = parse_data.get("code")
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析错误: {e}")
                raise
            # 重登机制
            if resp.status in [401, 403] or code == -3000:
                logger.warning("请求失败，状态码: -3000，正在尝试重新登录QQ空间...")
                if not await self.login():
                    raise RuntimeError("重新登录失败，无法继续请求")
                return await self._request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=headers or self.headers,
                    timeout=timeout,
                    retry_count=retry_count + 1,
                )
            if code != 0:
                return {"error": parse_data.get("message") or f"请求失败[{code}]"}
            return parse_data


    async def _upload_image(self, image: bytes) -> dict:
        """上传单张图片"""
        return await self._request(
            method="POST",
            url=self.UPLOAD_IMAGE_URL,
            timeout=60,
            data={
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
        )

    async def get_visitor(self) -> dict:
        """获取今日/总访客数"""
        return await self._request(
            method="GET",
            url=self.VISITOR_URL,
            params={
                "uin": self.uin,
                "mask": 7,
                "g_tk": self.gtk2,
                "page": 1,
                "fupdate": 1,
                "clear": 1,
            }
        )

    def parse_visitors(self, data: dict) -> str:
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

    async def publish(self, post: Post) -> dict:
        """发表说说, 返回tid"""
        post_data: dict[str, Any] = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": post.text,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"{self.BASE_URL}/{self.uin}",
        }
        if post.images:
            pic_bos, richvals = [], []
            imgs: list[bytes] = await normalize_images(post.images)
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

        return await self._request(
            method="POST",
            url=self.EMOTION_URL,
            params={"g_tk": self.gtk2, "uin": self.uin},
            data=post_data,
        )

    async def like(self, fid: str, target_id: str) -> dict:
        """
        点赞指定说说。

        Args:
            fid (str): 说说的动态ID。
            target_id (str): 目标QQ号。

        """
        return await self._request(
            method="POST",
            url=self.DOLIKE_URL,
            params={
                "g_tk": self.gtk2,
            },
            data={
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
        )

    async def comment(self, fid: str, target_id: str, content: str) -> dict:
        """
        评论指定说说。

        Args:
            fid (str): 说说的动态ID。
            target_id (str): 目标QQ号。
            content (str): 评论的文本内容。

        """
        return await self._request(
            "POST",
            url=self.COMMENT_URL,
            params={"g_tk": self.gtk2},
            data={
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
        )

    def _get_comments(self, msg: dict) -> list[dict]:
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
        return comments[::-1]

    async def get_posts(self, target_id: str, pos: int = 1, num: int = 1) -> dict:
        """
        获取指定QQ号的好友说说列表

        Args:
            target_id (str): 目标QQ号。
            pos (int): 起始位置。
            num (int): 要获取的说说数量。
        """
        logger.info(f"正在获取 {target_id} 的说说列表...")
        return await self._request(
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
                "format": "json",
                "need_comment": 1,
                "need_private_comment": 1,
            }
        )

    def parse_posts(self, data: dict) -> list[Post]:
        """解析说说列表"""
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

    # async def delete(self, tid: str):
    #     """删除tid对应说说"""

    #     DELETE_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_delete_v6"
    #     return await self._request(
    #         "POST",
    #         url=DELETE_URL,
    #         params={"g_tk": self.gtk2},
    #         data={
    #             "tid": tid,
    #             "hostUin": self.uin,
    #             "qzreferrer": f"{self.BASE_URL}/{self.uin}",
    #             "t1_source": 1,
    #             "code_version": 1,
    #             "format": "fs",
    #         },
    #     )



    # async def monitor_get_qzones(self, self_readnum: int) -> list[dict[str, Any]]:
    #     """
    #     获取自己的好友说说列表，返回已读与未读的说说列表。
    #     Args:
    #         self_readnum: 需要获取完整评论的自己的最新说说数量

    #     """
    #     res = await self._request(
    #         method="GET",
    #         url=self.ZONE_LIST_URL,
    #         params={
    #             "uin": self.uin,  # QQ号
    #             "scope": 0,  # 访问范围
    #             "view": 1,  # 查看权限
    #             "filter": "all",  # 全部动态
    #             "flag": 1,  # 标记
    #             "applist": "all",  # 所有应用
    #             "pagenum": 1,  # 页码
    #             "aisortEndTime": 0,  # AI排序结束时间
    #             "aisortOffset": 0,  # AI排序偏移
    #             "aisortBeginTime": 0,  # AI排序开始时间
    #             "begintime": 0,  # 开始时间
    #             "format": "json",  # 返回格式
    #             "g_tk": self.gtk2,  # 令牌
    #             "useutf8": 1,  # 使用UTF8编码
    #             "outputhtmlfeed": 1,  # 输出HTML格式
    #         }
    #     )

    #     if res.get("code") != 0:
    #         raise Exception(f"说说获取失败: {res}")

    #     #return self.parse_qzone_list(res)
    #     print(res)
    #     try:
    #         feeds_list = []
    #         num_self = 0  # 记录自己的说说数量
    #         for feed in res:
    #             if not feed:  # 跳过None值
    #                 continue
    #             # 过滤广告类内容（appid=311）
    #             appid = str(feed.get("appid", ""))
    #             if appid != "311":
    #                 continue
    #             target_qq = feed.get("uin", "")
    #             if target_qq == str(self.uin):
    #                 num_self += 1  # 统计自己的说说数量
    #             tid = feed.get("key", "")
    #             if not target_qq or not tid:
    #                 logger.error(f"无效的说说数据: target_qq={target_qq}, tid={tid}")
    #                 continue
    #             # print(feed)

    #             html_content = feed.get("html", "")
    #             if not html_content:
    #                 logger.error(f"说说内容为空: UIN={target_qq}, TID={tid}")
    #                 continue

    #             soup = bs4.BeautifulSoup(html_content, "html.parser")

    #             # 解析说说时间 - 相对时间，如'昨天17:50'
    #             created_time = feed.get("feedstime", "").strip()

    #             # 提取文字内容
    #             text_div = soup.find("div", class_="f-info")
    #             text = text_div.get_text(strip=True) if text_div else ""
    #             # 提取转发内容
    #             rt_con = ""
    #             txt_box = soup.select_one("div.txt-box")
    #             if txt_box:
    #                 # 获取除昵称外的纯文本内容
    #                 rt_con = txt_box.get_text(strip=True)
    #                 # 分割掉昵称部分（从第一个冒号开始取内容）
    #                 if "：" in rt_con:
    #                     rt_con = rt_con.split("：", 1)[1].strip()
    #             # 提取图片URL
    #             image_urls = []
    #             # 查找所有图片容器
    #             img_box = soup.find("div", class_="img-box")
    #             if img_box:
    #                 for img in img_box.find_all("img"):
    #                     src = img.get("src")
    #                     if src and not src.startswith(
    #                         "http://qzonestyle.gtimg.cn"
    #                     ):  # 过滤表情图标
    #                         image_urls.append(src)
    #             # TODO 临时视频处理办法（视频缩略图）
    #             images = []
    #             img_tag = soup.select_one("div.video-img img")
    #             if img_tag and "src" in img_tag.attrs:
    #                 if img_tag["src"] not in images:
    #                     images.append(img_tag["src"])

    #             # 获取视频url
    #             videos = []
    #             video_div = soup.select_one("div.img-box.f-video-wrap.play")
    #             if video_div and "url3" in video_div.attrs:
    #                 videos.append(video_div["url3"])
    #             # 获取评论内容
    #             comments_list = []
    #             # 查找所有评论项（包括主评论和回复）
    #             comment_items = soup.select("li.comments-item.bor3")
    #             if comment_items:
    #                 for item in comment_items:
    #                     # 提取基本信息
    #                     qq_account = item.get("data-uin", "")
    #                     comment_tid = item.get("data-tid", "")
    #                     nickname = item.get("data-nick", "")

    #                     # 查找评论内容
    #                     content_div = item.select_one("div.comments-content")
    #                     if content_div:
    #                         # 移除操作按钮（回复/删除）
    #                         for op in content_div.select("div.comments-op"):
    #                             op.decompose()
    #                         # 获取纯文本内容
    #                         content = content_div.get_text(" ", strip=True)
    #                     else:
    #                         content = ""

    #                     # 提取评论时间（直接使用相对时间字符串）
    #                     comment_time_span = item.select_one("span.state")
    #                     comment_time = (
    #                         comment_time_span.get_text(strip=True)
    #                         if comment_time_span
    #                         else ""
    #                     )

    #                     # 检查是否是回复
    #                     parent_tid = None
    #                     parent_div = item.find_parent("div", class_="mod-comments-sub")
    #                     if parent_div:
    #                         parent_li = parent_div.find_parent(
    #                             "li", class_="comments-item"
    #                         )
    #                         if parent_li:
    #                             parent_tid = parent_li.get("data-tid")

    #                     comments_list.append(
    #                         {
    #                             "qq_account": str(qq_account),
    #                             "nickname": nickname,
    #                             "comment_tid": int(comment_tid)
    #                             if comment_tid.isdigit()
    #                             else 0,
    #                             "content": content,
    #                             "created_time": comment_time,  # 直接使用相对时间字符串
    #                             "parent_tid": int(parent_tid)
    #                             if parent_tid and parent_tid.isdigit()
    #                             else None,
    #                         }
    #                     )

    #             feeds_list.append(
    #                 {
    #                     "target_qq": str(target_qq),
    #                     "tid": str(tid),
    #                     "created_time": created_time,  # 相对时间字符串
    #                     "content": text,
    #                     "images": images,
    #                     "videos": videos,
    #                     "rt_con": rt_con,
    #                     "comments": comments_list,
    #                 }
    #             )

    #         logger.info(
    #             f"成功解析 {len(feeds_list)} 条最新说说，其中自己的说说有 {num_self} 条"
    #         )
    #         # 获取自己说说下的完整评论内容
    #         feeds_list = [
    #             item for item in feeds_list if item.get("target_qq") != str(self.uin)
    #         ]  # 去除自己的说说
    #         self_feeds = await self.get_qzones(str(self.uin), self_readnum)
    #         feeds_list.extend(self_feeds)
    #         return feeds_list
    #     except Exception as e:
    #         logger.error(f"解析说说错误：{e}")
    #         return []

    async def terminate(self) -> None:
        await self._session.close()
