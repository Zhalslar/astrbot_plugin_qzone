import time

from astrbot.api import logger

from .db import PostDB
from .llm_action import LLMAction
from .model import Comment, Post
from .qzone import QzoneAPI, QzoneParser, QzoneSession


class PostService:
    """
    Application Service 层
    """

    def __init__(
        self,
        qzone: QzoneAPI,
        session: QzoneSession,
        db: PostDB,
        llm: LLMAction,
    ):
        self.qzone = qzone
        self.session = session
        self.db = db
        self.llm = llm

    # ============================================================
    # 业务接口
    # ============================================================

    async def query_feeds(
        self,
        *,
        target_id: str | None = None,
        pos: int = 0,
        num: int = 1,
        with_detail: bool = False,
        no_self: bool = False,
        no_commented: bool = False,
    ) -> list[Post]:
        if target_id:
            resp = await self.qzone.get_feeds(target_id, pos=pos, num=num)
            if not resp.ok:
                raise RuntimeError(self._map_feed_error(resp, target_id=target_id))
            msglist = resp.data.get("msglist") or []
            if not msglist:
                raise RuntimeError(f"QQ {target_id} 暂无可见说说")
            posts: list[Post] = QzoneParser.parse_feeds(msglist)

        else:
            resp = await self.qzone.get_recent_feeds()
            if not resp.ok:
                raise RuntimeError(self._map_feed_error(resp))
            posts: list[Post] = QzoneParser.parse_recent_feeds(resp.data)[
                pos : pos + num
            ]
            if not posts:
                raise RuntimeError("动态流暂无可见说说")

        if no_self:
            uin = await self.session.get_uin()
            posts = [p for p in posts if p.uin != uin]

        if with_detail:
            posts = await self._fill_post_detail(posts)
            if not posts:
                raise RuntimeError("获取详情后无有效说说")

        if no_commented:
            posts = await self._filter_not_commented(posts)

        for post in posts:
            await self.db.save(post)

        return posts

    @staticmethod
    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(k in text for k in keywords)

    def _map_feed_error(self, resp, *, target_id: str | None = None) -> str:
        message = str(resp.message or "").strip()
        lower_message = message.lower()
        code = resp.code

        permission_keywords = (
            "无权限",
            "权限",
            "私密",
            "不可见",
            "拒绝访问",
            "受限",
            "forbidden",
            "permission denied",
            "access denied",
        )
        login_keywords = ("登录", "失效", "skey", "g_tk", "cookie", "expired")

        if code == -3000 or self._contains_any(lower_message, login_keywords):
            return "登录状态失效，请重新登录后重试"

        if code in (403, -403) or self._contains_any(lower_message, permission_keywords):
            if target_id:
                return f"无权限查看 QQ {target_id} 的说说"
            return "无权限访问动态流"

        if code == -1 and message == "empty response":
            if target_id:
                return f"无权限查看 QQ {target_id} 的说说（接口返回空响应）"
            return "动态接口返回空响应，请稍后重试"

        if code == -1 and message in ("invalid response", "json parse error"):
            return "接口响应格式异常，请稍后重试"

        if message:
            return f"查询说说失败：{message}"
        return f"查询说说失败：code={code}"

    async def _fill_post_detail(self, posts: list[Post]) -> list[Post]:
        result: list[Post] = []

        for post in posts:
            resp = await self.qzone.get_detail(post)
            if not resp.ok or not resp.data:
                logger.warning(f"获取详情失败：{resp.data}")
                continue

            parsed = QzoneParser.parse_feeds([resp.data])
            if not parsed:
                logger.warning(f"解析详情失败：{resp.data}")
                continue

            result.append(parsed[0])

        return result

    async def _filter_not_commented(self, posts: list[Post]) -> list[Post]:
        result: list[Post] = []
        uin = await self.session.get_uin()

        for post in posts:
            # 如果已经有 comments，说明是 detail post
            if not post.comments:
                resp = await self.qzone.get_detail(post)
                if not resp.ok or not resp.data:
                    continue
                parsed = QzoneParser.parse_feeds([resp.data])
                if not parsed:
                    continue
                post = parsed[0]

            if any(c.uin == uin for c in post.comments):
                continue

            result.append(post)

        return result

    # ==================== 对外接口 ========================

    async def view_visitor(self) -> str:
        """查看访客"""
        resp = await self.qzone.get_visitor()
        if not resp.ok:
            raise RuntimeError(f"获取访客异常：{resp.data}")
        if not resp.data:
            raise RuntimeError("无访客记录")
        return QzoneParser.parse_visitors(resp.data)

    async def like_posts(self, post: Post):
        """点赞帖子"""
        if not post.tid:
            raise ValueError("帖子 tid 为空")
        await self.qzone.like(post)
        logger.info(f"已点赞 → {post.name}")


    async def comment_posts(self, post: Post):
        """评论帖子"""
        if not post.tid:
            raise ValueError("帖子 tid 为空")

        content = await self.llm.generate_comment(post)
        if not content:
            raise ValueError("生成评论内容为空")

        await self.qzone.comment(post, content)

        uin = await self.session.get_uin()
        name = await self.session.get_nickname()
        post.comments.append(
            Comment(
                uin=uin,
                nickname=name,
                content=content,
                create_time=int(time.time()),
                tid=0,
                parent_tid=None,
            )
        )
        await self.db.save(post)
        logger.info(f"评论 → {post.name}")

    async def reply_comment(self, post: Post, index: int):
        """回复评论（自动排除自己的评论）"""

        if not post.tid:
            raise ValueError("帖子 tid 为空")

        uin = await self.session.get_uin()

        # 排除自己的评论
        other_comments = [c for c in post.comments if c.uin != uin]
        n = len(other_comments)

        if n == 0:
            raise ValueError("没有可回复的评论")

        # 校验索引（基于过滤后的列表）
        if not (-n <= index < n):
            raise ValueError(f"索引越界, 当前仅有 {n} 条可回复评论")

        comment = other_comments[index]

        # 生成回复
        content = await self.llm.generate_reply(post, comment)
        if not content:
            raise ValueError("生成回复内容为空")

        # 发回复
        resp = await self.qzone.reply(post, comment, content)
        if not resp.ok:
            raise RuntimeError(resp.message)

        # 本地回填
        name = await self.session.get_nickname()
        post.comments.append(
            Comment(
                uin=uin,
                nickname=name,
                content=content,
                create_time=int(time.time()),
                parent_tid=comment.tid,
            )
        )
        await self.db.save(post)

    async def publish_post(
        self,
        *,
        post: Post | None = None,
        text: str | None = None,
        images: list | None = None,
    ) -> Post:
        """发表帖子（支持 Post / text / images，但不能为空）"""

        # 参数校验
        if post is None and not text and not images:
            raise ValueError("post、text、images 不能同时为空")

        # 如果没传 post，就自动构造一个
        if post is None:
            uin = await self.session.get_uin()
            name = await self.session.get_nickname()
            post = Post(
                uin=uin,
                name=name,
                text=text or "",
                images=images or [],
            )

        # 发布
        resp = await self.qzone.publish(post)
        if not resp.ok:
            raise RuntimeError(f"发布说说失败：{resp.data}")

        # 回填发布结果
        post.tid = resp.data.get("tid")
        post.status = "approved"
        post.create_time = resp.data.get("now", post.create_time)

        # 持久化
        await self.db.save(post)
        return post

    async def delete_post(self, post: Post):
        """删除帖子"""
        if not post.tid:
            raise ValueError("帖子 tid 为空")
        await self.qzone.delete(post.tid)
        if post.id:
            await self.db.delete(post.id)
