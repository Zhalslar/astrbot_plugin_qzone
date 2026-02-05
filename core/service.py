# core/operate.py
import asyncio
import time

from astrbot.api import logger

from .db import PostDB
from .llm_action import LLMAction
from .model import Comment, Post
from .qzone import QzoneAPI, QzoneParser, QzoneSession


class PostService:
    """
    Application Service 层（兼容版）：
    - 新接口：纯业务、无 event
    - 旧接口：仅作为 wrapper，保证不崩
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
        no_self: bool = False,
        no_commented: bool = False,
    ) -> list[Post]:
        if target_id:
            resp = await self.qzone.get_feeds(target_id, pos=pos, num=num)
            if not resp.ok:
                raise RuntimeError(resp.message)
            msglist = resp.data.get("msglist") or []
            if not msglist:
                raise RuntimeError("查询结果为空")
            posts: list[Post] = QzoneParser.parse_feeds(msglist)

        else:
            resp = await self.qzone.get_recent_feeds()
            if not resp.ok:
                raise RuntimeError(resp.message)
            posts: list[Post] = QzoneParser.parse_recent_feeds(resp.data)[
                pos : pos + num
            ]
            if not posts:
                raise RuntimeError("查询结果为空")

        if no_self:
            uin = await self.session.get_uin()
            posts = [p for p in posts if p.uin != uin]

        if no_commented:
            posts = await self._filter_not_commented(posts)

        for post in posts:
            await self.db.save(post)

        return posts

    async def _filter_not_commented(self, posts: list[Post]) -> list[Post]:
        result: list[Post] = []
        for post in posts:
            resp = await self.qzone.get_detail(post)
            if not resp.ok:
                logger.warning(f"获取详情异常：{resp.data}")
                continue
            if not resp.data:
                continue
            posts = QzoneParser.parse_feeds([resp.data])
            if not posts:
                logger.warning(f"解析详情异常：{resp.data}")
                continue
            post = posts[0]
            uin = await self.session.get_uin()
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
            logger.warning("帖子 tid 为空")
            return
        try:
            await self.qzone.like(post)
            logger.info(f"已点赞 → {post.name}")
        except Exception as e:
            logger.warning(f"点赞异常：{e}")

    async def comment_posts(self, post: Post):
        """评论帖子"""
        if not post.tid:
            logger.warning("帖子 tid 为空")
            return
        try:
            content = await self.llm.generate_comment(post)
            if not content:
                logger.warning("生成评论内容为空")
                return

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

        except Exception as e:
            logger.warning(f"评论异常：{e}")

    async def reply_comment(self, post: Post, index: int):
        """回复评论"""
        comments = post.comments
        n = len(comments)

        if not (-n <= index < n):
            raise ValueError(f"评论索引越界, 当前仅有 {n} 条评论")

        comment = comments[index]

        if not post.tid:
            raise ValueError("帖子 tid 为空")

        try:
            content = await self.llm.generate_reply(post, comment)
            if not content:
                raise ValueError("生成回复内容为空")

            resp = await self.qzone.reply(post, comment, content)
            if not resp.ok:
                raise RuntimeError(resp.message)
            uin = await self.session.get_uin()
            name = await self.session.get_nickname()
            post.comments.append(
                Comment(
                    uin=uin,
                    nickname=name,
                    content=content,
                    create_time=int(time.time()),
                    tid=int(post.tid),
                    parent_tid=comment.tid,
                )
            )
            await self.db.save(post)
        except Exception as e:
            raise RuntimeError(e)

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
            return
        await self.qzone.delete(post.tid)
        if post.id:
            await self.db.delete(post.id)
