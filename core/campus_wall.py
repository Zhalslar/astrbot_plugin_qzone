
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import BaseMessageComponent, Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.context import Context

from .post import Post, PostDB
from .qzone_api import Qzone
from .utils import get_image_urls


class CampusWall:
    def __init__(
        self,
        context: Context,
        config: AstrBotConfig,
        qzone: Qzone,
        db: PostDB,
        style
    ):
        self.qzone = qzone
        self.db = db
        self.style =style
        # 管理群ID，审批信息会发到此群
        self.manage_group: int = config.get("manage_group", 0)
        # 管理员QQ号列表，审批信息会私发给这些人
        self.admins_id: list[str] = list(set(context.get_config().get("admins_id", [])))

    async def notice_admin(
        self, event: AiocqhttpMessageEvent, chain: list[BaseMessageComponent]
    ):
        """通知管理群或管理员"""
        client = event.bot
        obmsg = await event._parse_onebot_json(MessageChain(chain))

        async def send_to_admins():
            for admin_id in self.admins_id:
                if admin_id.isdigit():
                    try:
                        await client.send_private_msg(
                            user_id=int(admin_id), message=obmsg
                        )
                    except Exception as e:
                        logger.error(f"无法反馈管理员：{e}")

        if self.manage_group:
            try:
                await client.send_group_msg(
                    group_id=int(self.manage_group), message=obmsg
                )
            except Exception as e:
                logger.error(f"无法反馈管理群：{e}")
                await send_to_admins()
        elif self.admins_id:
            await send_to_admins()

    async def notice_user(
        self,
        event: AiocqhttpMessageEvent,
        chain: list[BaseMessageComponent],
        group_id: int = 0,
        user_id: int = 0,
    ):
        """通知投稿者"""
        client = event.bot
        obmsg = await event._parse_onebot_json(MessageChain(chain))

        async def send_to_user():
            try:
                await client.send_private_msg(user_id=int(user_id), message=obmsg)
            except Exception as e:
                logger.error(f"无法通知投稿者：{e}")

        if group_id:
            try:
                await client.send_group_msg(group_id=int(group_id), message=obmsg)
            except Exception as e:
                logger.error(f"无法投稿者的群：{e}")
                await send_to_user()
        elif self.admins_id:
            await send_to_user()

    @staticmethod
    def parse_input(input: str | int) -> list[int]:
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
        await post.save(self.db)
        # 渲染图片
        img_path = await post.to_image(self.style)
        img_seg = Image.fromFileSystem(img_path)

        # 通知投稿者
        chain = [Plain("已投，等待审核..."), img_seg]
        await event.send(event.chain_result(chain))

        # 通知管理员
        chain = [Plain(f"收到新投稿#{post.id}"), img_seg]
        await self.notice_admin(event, chain)
        event.stop_event()


    async def view(self, event: AiocqhttpMessageEvent, input: str | int):
        "查看稿件 <ID>, 默认最新稿件"
        for post_id in self.parse_input(input):
            post = await self.db.get(post_id)
            if not post:
                await event.send(event.plain_result(f"稿件#{post_id}不存在"))
                return
            img_path = await post.to_image(self.style)
            await event.send(event.image_result(img_path))


    async def approve(self, event: AiocqhttpMessageEvent, input: str | int):
        """通过稿件 <稿件ID>, 默认最新稿件"""
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
        res = await self.qzone.publish(post)

        # 处理错误
        if error := res.get("error"):
            await event.send(event.plain_result(error))
            logger.error(f"发布说说失败：{error}")
            event.stop_event()
            raise error

        # 更新字段，存入数据库
        post.tid = res["tid"]
        post.create_time = res["now"]
        post.status = "approved"
        await post.save(self.db)

        # 渲染图片
        img_path = await post.to_image(self.style)
        img_seg = Image.fromFileSystem(str(img_path))

        # 通知管理员
        chain = [Plain(f"已发布说说#{post_id}"), img_seg]
        await event.send(event.chain_result(chain))

        # 通知投稿者
        chain = [Plain(f"您的投稿#{post_id}已通过"), img_seg]
        await self.notice_user(
            event,
            chain=chain,
            group_id=post.gin,
            user_id=post.uin,
        )
        logger.info(f"已发布说说#{post_id}")


    async def reject(self, event: AiocqhttpMessageEvent, input: str | int):
        """拒绝稿件 <稿件ID> <原因>"""
        for post_id in self.parse_input(input):
            post = await self.db.get(post_id)
            if not post:
                await event.send(event.plain_result(f"稿件#{post_id}不存在"))
                return

            if post.status == "rejected":
                await event.send(event.plain_result(f"稿件#{post_id}已拒绝，请勿重复拒绝"))
                return

            if post.status == "approved":
                await event.send(event.plain_result(f"稿件#{post_id}已发布，无法拒绝"))
                return

            reason = event.message_str.removeprefix(f"拒绝稿件 {post_id}").strip()

            # 更新字段，存入数据库
            post.status = "rejected"
            if reason:
                post.extra_text = reason
            await post.save(self.db)

            # 通知管理员
            admin_msg = f"已拒绝稿件#{post_id}"
            if reason:
                admin_msg += f"\n理由：{reason}"
            await event.send(event.plain_result(admin_msg))

            # 通知投稿者
            user_msg = f"您的投稿#{post_id}未通过"
            if reason:
                user_msg += f"\n理由：{reason}"
            await self.notice_user(
                event,
                chain=[Plain(user_msg)],
                group_id=post.gin,
                user_id=post.uin,
            )

    async def delete(self, event: AiocqhttpMessageEvent, input: str | int):
        """删除稿件 <稿件ID>"""
        for post_id in self.parse_input(input):
            post = await self.db.get(post_id)
            if not post:
                await event.send(event.plain_result(f"稿件#{post_id}不存在"))
                return

            await self.db.delete(post_id)
            await event.send(event.plain_result(f"已删除稿件#{post_id}"))
