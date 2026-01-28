from astrbot.api import logger
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .config import PluginConfig
from .db import PostDB
from .model import Post
from .qzone_api import Qzone
from .sender import Sender
from .utils import get_image_urls


class CampusWall:
    def __init__(self, config: PluginConfig, qzone: Qzone, db: PostDB, sender: Sender):
        self.cfg = config
        self.qzone = qzone
        self.db = db
        self.sender = sender

    @staticmethod
    def parse_input(input: str | int | None = None) -> list[int]:
        """解析 post_id 输入，支持单个 ID 或范围如 2~4"""
        post_ids = []
        if "~" in str(input):
            try:
                start, end = map(int, str(input).split("~"))
                post_ids = list(range(start, end + 1))
            except ValueError:
                raise ValueError("范围格式错误，应为如：2~4")
        elif isinstance(input, int):
            post_ids = [input]
        else:
            post_ids = [-1]
        return post_ids

    async def contribute(self, event: AiocqhttpMessageEvent):
        """投稿 <文字+图片>"""
        sender_name = event.get_sender_name()
        raw_text = event.message_str.removeprefix("投稿").strip()
        text = f"【来自 {sender_name} 的投稿】\n\n{raw_text}"
        images = await get_image_urls(event)
        post = Post(
            uin=int(event.get_sender_id()),
            name=sender_name,
            gin=int(event.get_group_id() or 0),
            text=text,
            images=images,
            anon=False,
            status="pending",
        )
        await self.db.save(post)

        # 通知投稿者
        await self.sender.send_post(
            post,
            event=event,
            message="已投，等待审核...",
        )

        # 通知管理员
        await self.sender.send_admin_post(
            post,
            client=event.bot,
            message=f"收到新投稿#{post.id}",
        )
        event.stop_event()

    async def view(self, event: AiocqhttpMessageEvent, input: str | int | None = None):
        "查看稿件 <ID>, 默认最新稿件"
        for post_id in self.parse_input(input):
            post = await self.db.get(post_id)
            if not post:
                await event.send(event.plain_result(f"稿件#{post_id}不存在"))
                return
            await self.sender.send_post(post, event=event)

    async def approve(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """管理员命令：通过稿件 <稿件ID>, 默认最新稿件"""
        for post_id in self.parse_input(input):
            post = await self.db.get(post_id)
            if not post:
                await event.send(event.plain_result(f"稿件#{post_id}不存在"))
                return

            if post.status == "approved":
                await event.send(
                    event.plain_result(f"稿件#{post_id}已通过，请勿重复通过")
                )
                return

            # 发布说说
            succ, data = await self.qzone.publish(post)

            # 处理错误
            if not succ:
                await event.send(event.plain_result(str(data)))
                logger.error(f"发布说说失败：{data}")
                event.stop_event()
                raise StopIteration

            # 更新字段，存入数据库
            post.tid = data.get("tid")
            post.create_time = data.get("now", 0)
            post.status = "approved"
            await self.db.save(post)

            # 通知管理员
            await self.sender.send_admin_post(
                post,
                client=event.bot,
                message=f"已发布说说#{post_id}",
            )

            # 通知投稿者
            if (
                str(post.uin) != event.get_self_id()
                and str(post.gin) != event.get_group_id()
            ):
                await self.sender.send_user_post(
                    post,
                    client=event.bot,
                    message=f"您的投稿#{post_id}已通过",
                )

    async def reject(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """管理员命令：拒绝稿件 <稿件ID> <原因>"""
        for post_id in self.parse_input(input):
            post = await self.db.get(post_id)
            if not post:
                await event.send(event.plain_result(f"稿件#{post_id}不存在"))
                return

            if post.status == "rejected":
                await event.send(
                    event.plain_result(f"稿件#{post_id}已拒绝，请勿重复拒绝")
                )
                return

            if post.status == "approved":
                await event.send(event.plain_result(f"稿件#{post_id}已发布，无法拒绝"))
                return

            reason = event.message_str.removeprefix(f"拒绝稿件 {post_id}").strip()

            # 更新字段，存入数据库
            post.status = "rejected"
            if reason:
                post.extra_text = reason
            await self.db.save(post)

            # 通知管理员
            admin_msg = f"已拒绝稿件#{post_id}"
            if reason:
                admin_msg += f"\n理由：{reason}"
            await event.send(event.plain_result(admin_msg))

            # 通知投稿者
            if (
                str(post.uin) != event.get_self_id()
                and str(post.gin) != event.get_group_id()
            ):
                user_msg = f"您的投稿#{post_id}未通过"
                if reason:
                    user_msg += f"\n理由：{reason}"
                await self.sender.send_user_post(
                    post, client=event.bot, message=user_msg
                )

    async def delete(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """管理员命令：删除稿件 <稿件ID>"""
        for post_id in self.parse_input(input):
            post = await self.db.get(post_id)
            if not post:
                await event.send(event.plain_result(f"稿件#{post_id}不存在"))
                return

            await self.db.delete(post_id)
            await event.send(event.plain_result(f"已删除稿件#{post_id}"))
