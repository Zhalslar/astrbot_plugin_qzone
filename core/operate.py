import time

from astrbot.api import logger
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.context import Context

from .comment import Comment
from .config import PluginConfig
from .llm_action import LLMAction
from .post import Post, PostDB
from .qzone_api import Qzone
from .utils import get_ats, get_nickname


class PostOperator:
    def __init__(
        self,
        context: Context,
        config: PluginConfig,
        qzone: Qzone,
        db: PostDB,
        llm: LLMAction,
        style,
    ):
        self.context = context
        self.config = config
        self.qzone = qzone
        self.db = db
        self.llm = llm
        self.style = style
        self.uin = 0
        self.name = "我"
        # 获取唯一管理员
        self.admin_ids: list[str] = context.get_config().get("admins_id", [])
        self.admin_id = next(aid for aid in self.admin_ids if aid.isdigit())

    # ------------------------ 公共 pipeline ------------------------ #
    async def _pipeline(
        self,
        event: AiocqhttpMessageEvent | None,
        get_recent: bool = False,
        get_sender: bool = False,
        no_self: bool = False,
        no_commented: bool = False,
        send_error: bool = True,
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

        if int(target_id) in self.config.ignore_users:  # 忽略用户
            logger.warning(f"已忽略用户（{target_id}）的QQ空间")
            return []

        posts: list[Post] = []

        # 解析范围参数
        pos, num = 0, 1  # 默认值
        if event:
            end_parm = event.message_str.strip().split()[-1]
            if "~" in end_parm:
                try:
                    start_str, end_str = end_parm.split("~", 1)
                    start_index, end_index = int(start_str), int(end_str)
                    if start_index <= 0 or end_index < start_index:
                        raise ValueError("范围不合法")
                    pos = start_index - 1
                    num = end_index - start_index + 1
                except ValueError:
                    # 格式不对就回退到默认 1 条
                    pos, num = 0, 1
            elif end_parm.isdigit():
                pos = int(end_parm) - 1
                num = 1

        if get_recent:
            # 获取最新说说
            succ, data = await self.qzone.get_recent_feeds()
        else:
            # pos为开始位置， num为获取数量
            succ, data = await self.qzone.get_feeds(
                target_id=target_id, pos=pos, num=num
            )

        # 处理错误
        if not succ:
            logger.error(f"获取说说失败：{data}")
            if isinstance(data, dict):
                if code := data.get("code"):
                    if code in [-10031]:
                        self.config.ignore_users.append(str(target_id))
                        logger.warning(
                            f"已将用户（{target_id}）添加到忽略列表，下次不再处理该用户的空间"
                        )
                        self.config.save_config()
                if event and send_error:
                    await event.send(
                        event.plain_result(data.get("message") or "获取不到说说")
                    )
                    event.stop_event()
            return []

        posts = data[pos : pos + num] if get_recent else data  # type: ignore

        # 过滤自己的说说
        self.uin = str(self.qzone.ctx.uin)
        if no_self:
            posts = [post for post in posts if str(post.uin) != self.uin]

        final_posts: list[Post] = []
        for post in posts:
            if no_commented:
                # 过滤已评论过的说说
                detail = await self.qzone.get_detail(post)
                if any(str(c.uin) == self.uin for c in detail.comments):
                    continue
                final_posts.append(detail)
            elif len(posts) == 1:
                # 单条说说则获取详情
                detail = await self.qzone.get_detail(post)
                final_posts.append(detail)
            else:
                # 多条说说则只获取基本信息
                final_posts.append(post)

        # 存到数据库
        for p in final_posts:
            await p.save(self.db)

        return final_posts

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
        send_error: bool = True,
        send_admin: bool = False,
    ):
        """
        读说说 <序号/范围> 即点赞+评论说说
        Args:
            event (AiocqhttpMessageEvent): 事件对象
            get_recent (bool): 是否获取最新说说
            get_sender (bool): 是否获取发送者
            no_self (bool): 是否过滤自己的说说
            no_commented (bool): 是否过滤已评论过的说说
            send_error (bool): 是否发送错误信息
            send_admin (bool): 是否仅发送消息给管理员
        """
        posts: list[Post] = await self._pipeline(
            event, get_recent, get_sender, no_self, no_commented, send_error
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
                if not content:
                    logger.error(f"[{idx}] 获取评论内容失败")
                    continue
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
                    content=content, # type: ignore
                    create_time=int(time.time()),
                    tid=0,
                    parent_tid=None,
                )
                post.comments.append(comment)
                await post.save(self.db)
                # 可视化
                if event:
                    img_path = await post.to_image(self.style)
                    if send_admin:
                        event.message_obj.group_id = None # type: ignore
                        event.message_obj.sender.user_id = self.admin_id
                    await event.send(event.image_result(img_path))

        logger.info(f"执行完毕，点赞成功 {like_succ} 条，评论成功 {comment_succ} 条")

    async def publish_feed(
        self,
        event: AiocqhttpMessageEvent | None = None,
        text: str | None = None,
        images: list[str] | None = None,
        post: Post | None = None,
        publish: bool = True,
        llm_text: bool = False,
        llm_images: bool = False,
    ):
        """
        发说说封装
        Args:
            event (AiocqhttpMessageEvent): 事件
            text (str): 文本
            images (list[str]): 图片
            post (Post | None, optional): 原说说.
            publish (bool, optional): 是否发布.
            llm_text (bool, optional): 是否使用llm配文(仅在text为空时生效).
            llm_images (bool, optional): 是否使用llm配图(仅在images为空时生效).
        """
        # llm配文
        if llm_text and not text:
            text = await self.llm.generate_diary()

        # TODO:llm配图
        #if llm_images and not images:
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


    # async def reply_comment(self, event: AiocqhttpMessageEvent):
    #     """
    #     回复评论
    #     Args:
    #         event (AiocqhttpMessageEvent): 事件
    #     """
    #     post = await Post.get_by_tid(self.db, event.message_obj.message_id)
    #     comment = await Comment.get_by_tid(self.db, event.message_obj.message_id)
    #     reply_event_data = await get_reply_event_data(event)
    #     new_event = Event.from_payload(reply_event_data)
    #     if not new_event:
    #         logger.error(f"无法从回复消息数据构造 Event 对象: {reply_event_data}")
    #         return await event.send(event.plain_result("无法从回复消息数据构造 Event 对象"))
    #     abm_reply = await self._convert_handle_message_event(new_event, get_reply=False)
    #     if not abm_reply:
    #         logger.error(f"无法从回复消息数据构造 Event 响应对象: {reply_event_data}")
    #         return await event.send(event.plain_result("无法从回复消息数据构造 Event 响应对象"))
    #     reply_text = await self.llm.generate_comment(post, comment, abm_reply)
    #     reply_ok, _ = await self.qzone.comment(
    #         fid=post.tid,
    #         target_id=str(comment.uin),
    #         content=reply_text,
    #         parent_tid=comment.tid,
    #     )
    #     if not reply_ok:
    #         logger.error(f"回复评论失败")
    #         return await event.send(event.plain_result("回复评论失败"))
    #     comment = Comment(
    #         uin=self.qzone.ctx.uin,
    #         nickname=bot_name,
    #         content=reply_text,
    #         create_time=int(time.time()),
    #         tid=0,
    #         parent_tid=comment.tid,
    #     )
    #     post.comments.append(comment)
    #     await post.save(self.db)
    #     img_path = await post.to_image(self.style)
    #     await event.send(event.image_result(img_path))
    #     await self.update_dashboard(event)
