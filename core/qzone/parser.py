# parser.py
import datetime
import json
import re
from typing import Any

import bs4
import json5

from astrbot.api import logger

from ..model import Comment, Post


def _safe_cell(text: str, max_len: int = 30) -> str:
    """
    pillowmd-safe 的表格单元格：
    - 无换行
    - 无 |
    - 不为空
    - 长度受限
    """
    if not text:
        return "-"
    text = str(text)
    text = text.replace("\n", " ").replace("|", "｜").strip()
    if len(text) > max_len:
        text = text[:max_len] + "…"
    return text or "-"


class QzoneParser:
    """QQ 空间响应解析器"""

    @staticmethod
    def parse_response(text: str, *, debug: bool = False) -> dict[str, Any]:
        """
        解析 JSON / JSONP / 非标准 JSON
        """

        if debug:
            logger.debug(f"响应数据: {text}")

        if m := re.search(
            r"callback\s*\(\s*([^{]*(\{.*\})[^)]*)\s*\)",
            text,
            re.I | re.S,
        ):
            json_str = m.group(2)
        else:
            json_str = text[text.find("{") : text.rfind("}") + 1]

        json_str = json_str.replace("undefined", "null").strip()

        try:
            data = json5.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析错误: {e}")
            raise

        if not isinstance(data, dict):
            raise RuntimeError("JSON 解析结果不是 dict")

        if debug:
            logger.debug(f"解析后数据: {data}")

        return data

    @staticmethod
    def parse_upload_result(payload: dict[str, Any]) -> tuple[str, str]:
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

    @staticmethod
    def parse_visitors(data: dict[str, Any]) -> str:
        data = data.get("data") or {}
        items = data.get("items")

        if not isinstance(items, list) or not items:
            return "### 最近来访明细\n\n暂无访客记录"

        src_map: dict[int, str] = {
            0: "访问空间",
            13: "查看动态",
            32: "手机QQ",
            41: "国际版QQ/TIM",
        }

        lines: list[str] = []

        # ======================
        # 明细表格
        # ======================
        lines.append("\n### 最近来访明细\n")
        lines.append("| 时间 | 访客 | 来源 | 状态 | 带来了 |")
        lines.append("| --- | --- | --- | --- | --- |")

        for v in items:
            if not isinstance(v, dict):
                continue

            # 时间
            ts = v.get("time")
            ts_int = ts if isinstance(ts, int) else 0
            dt = datetime.datetime.fromtimestamp(ts_int).strftime("%m-%d %H:%M")

            # 访客
            name = v.get("name")
            visitor = _safe_cell(name if isinstance(name, str) else "匿名", 16)

            # 来源
            src_val = v.get("src")
            src_key = src_val if isinstance(src_val, int) else -1
            src = _safe_cell(src_map.get(src_key, f"未知({src_key})"), 12)

            # 状态
            status_parts: list[str] = []
            yellow = v.get("yellow")
            if isinstance(yellow, int) and yellow > 0:
                status_parts.append(f"LV{yellow}")
            if v.get("is_hide_visit"):
                status_parts.append("隐身")
            status = _safe_cell(" / ".join(status_parts), 12)

            # 备注（只允许一句）
            remark = "-"

            shuos = v.get("shuoshuoes")
            if isinstance(shuos, list):
                for s in shuos:
                    if isinstance(s, dict):
                        title = s.get("name")
                        if isinstance(title, str) and title.strip():
                            remark = _safe_cell(f"说说:{title}", 30)
                            break

            uins = v.get("uins")
            if remark == "-" and isinstance(uins, list):
                names = []
                for u in uins:
                    if isinstance(u, dict):
                        n = u.get("name")
                        if isinstance(n, str) and n.strip():
                            names.append(n)
                if names:
                    remark = _safe_cell("、".join(names), 30)

            lines.append(
                f"| {_safe_cell(dt, 16)} | {visitor} | {src} | {status} | {remark} |"
            )
        # ======================
        # 表格外统计（底下一行）
        # ======================
        today = data.get("todaycount", 0)
        total = data.get("totalcount", 0)
        lines.append(f"今日访客共 {today} 人， 最近30天访客共 {total} 人")

        return "\n".join(lines)

    @staticmethod
    def parse_feeds(msglist: list[dict]) -> list[Post]:
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
