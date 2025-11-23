import time

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .comment import Comment
from .llm_action import LLMAction
from .post import Post, PostDB
from .qzone_api import Qzone
from .utils import get_ats, get_nickname


class PostOperator:
    def __init__(
        self, config: AstrBotConfig, qzone: Qzone, db: PostDB, llm: LLMAction, style
    ):
        self.config = config
        self.qzone = qzone
        self.db = db
        self.llm = llm
        self.style = style
        self.uin = 0
        self.name = "我"

    # ------------------------ 公共 pipeline ------------------------ #
    async def _pipeline(
        self,
        event: AiocqhttpMessageEvent | None,
        get_recent: bool = False,
        get_sender: bool = False,
        no_self: bool = False,
        no_commented: bool = False,
    ) -> list[Post]:
        """
        管道：取说说 → 解析参数 → 过滤 → 补详情 → 落库
        """
        # 解析目标用户
        target_id = ""
        if event:
            if at_ids := get_ats(event):
                target_id = at_ids[0]
            else:
                target_id = event.get_sender_id() if get_sender else event.get_self_id()
        else:
            target_id = str(self.qzone.ctx.uin)

        if not target_id:
            logger.error("获取不到用户ID")
            return []

        if target_id in self.config["ignore_users"]:  # 忽略用户
            logger.warning(f"已忽略用户（{target_id}）的QQ空间")
            return []

        posts: list[Post] = []

        # 解析范围参数
        end_parm = event.message_str.split(" ")[-1] if event else ""
        if "~" in end_parm:
            start_index, end_index = map(int, end_parm.split("~"))
            index = start_index
            num = end_index - start_index + 1
        elif end_parm.isdigit():
            index = int(end_parm)
            num = 1
        else:
            index = 1
            num = 1

        if get_recent:
            # 获取最新说说
            succ, data = await self.qzone.get_recent_feeds()
        else:
            # pos为开始位置， num为获取数量
            succ, data = await self.qzone.get_feeds(
                target_id=target_id, pos=index, num=num
            )

        # 处理错误
        if not succ:
            logger.error(f"获取说说失败：{data}")
            if event and isinstance(data, dict):
                if code := data.get("code"):
                    if code in [-10031]:
                        self.config["ignore_users"].append(target_id)
                        logger.warning(f"已将用户（{target_id}）添加到忽略列表，下次不再处理该用户的空间")
                        self.config.save_config()
                await event.send(
                    event.plain_result(data.get("message") or "获取不到说说")
                )
                event.stop_event()
            return []

        posts = data[index - 1 : index - 1 + num] if get_recent else data  # type: ignore

        # 过滤自己的说说
        self.uin = str(self.qzone.ctx.uin)
        if no_self:
            posts = [post for post in posts if str(post.uin) != self.uin]

        # 过滤已评论过的说说
        final_posts: list[Post] = []
        for post in posts:
            if no_commented:
                detail = await self.qzone.get_detail(post)
                if any(str(c.uin) == self.uin for c in detail.comments):
                    continue
                final_posts.append(detail)
            else:
                final_posts.append(post)

        # 如果只剩一条且不是详情对象，再补一次详情
        if len(final_posts) == 1 and not final_posts[0].comments:
            final_posts[0] = await self.qzone.get_detail(final_posts[0])

        # 存到数据库
        for post in posts:
            await post.save(self.db)

        return posts

    async def view_feed(self, event: AiocqhttpMessageEvent, get_recent: bool = True):
        """
        查看说说 <序号/范围>
        Args:
            event (AiocqhttpMessageEvent): 事件对象
            get_recent (bool, optional): 是否获取最新说说. Defaults to True.
        """
        posts: list[Post] = await self._pipeline(event, get_recent=get_recent)
        for post in posts:
            img_path = await post.to_image(self.style)
            await event.send(event.image_result(img_path))

    async def read_feed(
        self,
        event: AiocqhttpMessageEvent | None = None,
        get_recent: bool = True,
        get_sender: bool = False,
        no_self=True,
        no_commented=True,
    ):
        """
        读说说 <序号/范围> 即点赞+评论说说
        Args:
            event (AiocqhttpMessageEvent): 事件对象
            get_recent (bool, optional): 是否获取最新说说. Defaults to True.
            send_msg (bool, optional): 是否发送消息. Defaults to True.
        """
        posts: list[Post] = await self._pipeline(
            event, get_recent, get_sender, no_self, no_commented
        )
        bot_name = (
            await get_nickname(event, event.get_self_id()) if event else self.name
        )

        logger.info(f"开始执行读说说任务, 共 {len(posts)} 条")

        like_succ = comment_succ = 0

        for idx, post in enumerate(posts, 1):
            if not post.tid:
                continue
            # -------------- 点赞 --------------
            try:
                like_ok, _ = await self.qzone.like(
                    tid=post.tid, target_id=str(post.uin)
                )
            except Exception as e:
                logger.warning(f"[{idx}] 点赞异常：{e}")
                like_ok = False
            if like_ok:
                like_succ += 1
                logger.info(f"[{idx}] 点赞成功 → {post.name}")

            # -------------- 评论 --------------
            try:
                content = await self.llm.generate_comment(post)
                comment_ok, _ = await self.qzone.comment(
                    fid=post.tid,
                    target_id=str(post.uin),
                    content=content,
                )
                logger.info(f"[{idx}] 评论成功 → {post.name}")
            except Exception as e:
                logger.warning(f"[{idx}] 评论异常：{e}")
                comment_ok = False
            if comment_ok:
                comment_succ += 1
                # 落库
                comment = Comment(
                    uin=self.qzone.ctx.uin,
                    nickname=bot_name,
                    content=content,
                    create_time=int(time.time()),
                    tid=0,
                    parent_tid=None,
                )
                post.comments.append(comment)
                await post.save(self.db)
                # 可视化
                if event:
                    img_path = await post.to_image(self.style)
                    await event.send(event.image_result(img_path))

        logger.info(f"执行完毕，点赞成功 {like_succ} 条，评论成功 {comment_succ} 条")

    async def publish_feed(
        self,
        event: AiocqhttpMessageEvent | None = None,
        text: str | None = None,
        images: list[str] | None = None,
        post: Post | None = None,
        publish: bool = True,
    ):
        """
        发说说封装
        Args:
            event (AiocqhttpMessageEvent): 事件
            text (str): 文本
            images (list[str]): 图片
            post (Post | None, optional): 原说说. Defaults to None.
            publish (bool, optional): 是否发布. Defaults to True.
        """
        # TODO: llm配图
        # images = await self.llm.generate_images(text, self.per_qzone_num)

        if not post:
            uin = event.get_self_id() if event else self.uin
            name = await get_nickname(event, uin) if event else self.name
            gin = (event.get_group_id() or 0) if event else 0
            post = Post(
                uin=int(uin),
                name=name,
                gin=int(gin),
                text=text or "",
                images=images or [],
                status="pending",
            )
        if publish:
            succ, data = await self.qzone.publish(post)
            if not succ:
                logger.error(f"发布说说失败：{str(data)}")
                if event:
                    await event.send(event.plain_result(str(data)))
                    event.stop_event()
                raise StopIteration
            post.tid = data.get("tid", "")
            post.status = "approved"
            if now := data.get("now", ""):
                post.create_time = now
        # 落库
        await post.save(self.db)

        # 可视化
        if event:
            img_path = await post.to_image(self.style)
            await event.send(event.image_result(img_path))
