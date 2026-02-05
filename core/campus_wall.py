from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .config import PluginConfig
from .db import PostDB
from .model import Post
from .sender import Sender
from .service import PostService
from .utils import get_image_urls


class CampusWall:
    def __init__(
        self,
        config: PluginConfig,
        service: PostService,
        db: PostDB,
        sender: Sender,
    ):
        self.cfg = config
        self.service = service
        self.db = db
        self.sender = sender

    async def contribute(self, event: AiocqhttpMessageEvent, anon: bool = False):
        """投稿 <文字+图片>"""
        sender_name = event.get_sender_name()
        raw_text = event.message_str.partition(" ")[2]
        text = f"{raw_text}"
        images = await get_image_urls(event)
        post = Post(
            uin=int(event.get_sender_id()),
            name=sender_name,
            gin=int(event.get_group_id() or 0),
            text=text,
            images=images,
            anon=anon,
            status="pending",
        )
        await self.db.save(post)

        # 通知投稿者
        await self.sender.send_post(event, post, message="已投，等待审核...")

        # 通知管理员
        await self.sender.send_admin_post(
            post,
            client=event.bot,
            message=f"收到新投稿#{post.id}",
        )
        event.stop_event()

    async def delete(self, event: AiocqhttpMessageEvent):
        """撤稿 <稿件ID> <理由>"""
        args = event.message_str.split(" ")
        post_id = args[1] if len(args) >= 2 else -1
        reason = event.message_str.removeprefix(f"撤稿 {post_id}").strip()
        post = await self.db.get(post_id)
        if not post or not post.id:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        if post.uin != int(event.get_sender_id()):
            yield event.plain_result("你只能撤回自己的稿件")
            return
        await self.db.delete(post.id)
        msg = f"稿件#{post.id}已撤回"
        if reason:
            msg += f"\n理由：{reason}"
        yield event.plain_result(msg)
        # 通知管理员
        await self.sender.send_admin_post(post, client=event.bot, message=msg)
        event.stop_event()


    async def view(self, event: AstrMessageEvent):
        "查看稿件 <ID>, 默认最新稿件"
        args = event.message_str.split(" ")[1:] or ["-1"]
        for post_id in args:
            if not post_id.isdigit():
                continue
            post = await self.db.get(post_id)
            if not post:
                yield event.plain_result(f"稿件#{post_id}不存在")
                continue
            await self.sender.send_post(event, post)

    async def approve(self, event: AiocqhttpMessageEvent):
        """管理员命令：通过稿件 <稿件ID>, 默认最新稿件"""
        args = event.message_str.split(" ")
        post_id = args[1] if len(args) >= 2 else -1
        post = await self.db.get(post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return

        if post.status == "approved":
            yield event.plain_result(f"稿件#{post.id}已通过，请勿重复通过")
            return
        if self.cfg.show_name:
            post.text = f"【来自 {post.show_name} 的投稿】\n\n{post.text}"

        # 发布说说
        try:
            post_ = await self.service.publish_post(post=post)
        except Exception as e:
            yield event.plain_result(str(e))
            return

        # 通知管理员
        await self.sender.send_post(event, post_, message=f"已发布说说#{post.id}")

        # 通知投稿者
        if (
            str(post_.uin) != event.get_self_id()
            and str(post_.gin) != event.get_group_id()
        ):
            await self.sender.send_user_post(
                post_,
                client=event.bot,
                message=f"您的投稿#{post.id}已通过",
            )
        event.stop_event()

    async def reject(self, event: AiocqhttpMessageEvent):
        """管理员命令：拒绝稿件 <稿件ID> <原因>"""
        args = event.message_str.split(" ")
        post_id = args[1] if len(args) >= 2 else -1
        reason = event.message_str.removeprefix(f"拒绝稿件 {post_id}").strip()
        post = await self.db.get(post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return

        if post.status == "rejected":
            yield event.plain_result(f"稿件#{post.id}已拒绝，请勿重复拒绝")
            return

        if post.status == "approved":
            yield event.plain_result(f"稿件#{post.id}已发布，无法拒绝")
            return

        reason = event.message_str.removeprefix(f"拒绝稿件 {post.id}").strip()

        # 更新字段，存入数据库
        post.status = "rejected"
        if reason:
            post.extra_text = reason
        await self.db.save(post)

        # 通知管理员
        admin_msg = f"已拒绝稿件#{post.id}"
        if reason:
            admin_msg += f"\n理由：{reason}"
        yield event.plain_result(admin_msg)

        # 通知投稿者
        if (
            str(post.uin) != event.get_self_id()
            and str(post.gin) != event.get_group_id()
        ):
            user_msg = f"您的投稿#{post.id}未通过"
            if reason:
                user_msg += f"\n理由：{reason}"
            await self.sender.send_user_post(
                post, client=event.bot, message=user_msg
            )
