import re
import time
from aiocqhttp import CQHttp
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.api import logger
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from .post import Post, PostManager
from .utils import download_file, get_image_urls, get_reply_message_str
from .api import QzoneAPI


@register(
    "astrbot_plugin_qzone",
    "Zhalslar",
    "QQ空间对接插件",
    "v1.0.0",
)
class QzonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 管理群ID，审批信息会发到此群
        self.manage_group: int = config.get("manage_group", 0)
        # 管理员QQ号列表，审批信息会私发给这些人
        self.admins_id: list[str] = list(set(context.get_config().get("admins_id", [])))
        # 数据库文件
        db_path = StarTools.get_data_dir("astrbot_plugin_qzone") / "posts.db"
        # 数据库管理类
        self.pm = PostManager(db_path)
        # QQ空间API类
        self.qzone = QzoneAPI()

    async def initialize(self):
        await self.pm.init_db()

    @filter.event_message_type(filter.EventMessageType.ALL, property=1)
    async def init_curfew_manager(self, event: AiocqhttpMessageEvent):
        "自动登录QQ空间（不优雅的方案）"
        if not self.qzone.cookies:
            await self.qzone.login(client=event.bot)
            logger.info(f"已登录QQ空间: {self.qzone.uin}")

    async def notice_admin(self, client: CQHttp, message: str):
        """通知管理群或管理员"""

        async def send_to_admins():
            for admin_id in self.admins_id:
                if admin_id.isdigit():
                    try:
                        await client.send_private_msg(
                            user_id=int(admin_id), message=message
                        )
                    except Exception as e:
                        logger.error(f"无法反馈管理员：{e}")

        if self.manage_group:
            try:
                await client.send_group_msg(
                    group_id=int(self.manage_group), message=message
                )
            except Exception as e:
                logger.error(f"无法反馈管理群：{e}")
                await send_to_admins()
        elif self.admins_id:
            await send_to_admins()

    async def notice_user(
        self, client: CQHttp, group_id: int = 0, user_id: int = 0, message: str = ""
    ):
        """通知投稿者"""

        async def send_to_user():
            try:
                await client.send_private_msg(user_id=int(user_id), message=message)
            except Exception as e:
                logger.error(f"无法通知投稿者：{e}")

        if group_id:
            try:
                await client.send_group_msg(group_id=int(group_id), message=message)
            except Exception as e:
                logger.error(f"无法投稿者的群：{e}")
                await send_to_user()
        elif self.admins_id:
            await send_to_user()

    def extract_post_id(self, event: AiocqhttpMessageEvent) -> int | None:
        """从引用消息中提取稿件 ID"""
        content = get_reply_message_str(event)
        if not content or "新投稿" not in content:
            return None
        match = re.search(r"新投稿#(\d+)", content)
        return int(match.group(1)) if match else 0

    @filter.command("发说说")
    async def publish_emotion(self, event: AiocqhttpMessageEvent):
        """直接发说说，无需审核"""
        text = event.message_str.removeprefix("发说说").strip()
        image_urls = await get_image_urls(event)
        images = [
            file for url in image_urls if (file := await download_file(url)) is not None
        ]
        post_id = (await self.pm.get_total_count()) + 1
        post = Post(
            id=post_id,
            uin=int(event.get_sender_id()),
            text=text,
            images=image_urls,
            anon=False,
            status="approved",
            create_time=int(time.time()),
        )
        await self.pm.add_post(post)
        await self.qzone.publish_emotion(text, images)
        yield event.plain_result(f"已发布说说#{post_id}")


    @filter.command("投稿")
    async def submit(self, event: AiocqhttpMessageEvent):
        """投稿 <文字+图片>"""
        # 存入数据库
        text = event.message_str.removeprefix("投稿").strip()
        post_id = (await self.pm.get_total_count()) + 1
        post = Post(
            id=post_id,
            uin=int(event.get_sender_id()),
            text=text,
            images=await get_image_urls(event),
            anon=False,
            status="pending",
            create_time=int(time.time()),
        )
        await self.pm.add_post(post)

        # 通知管理员
        msg = f"【新投稿#{post_id}】\n{post.to_str()}"
        await self.notice_admin(client=event.bot, message=msg)  # type: ignore
        event.stop_event()

    @filter.command("查看稿件", alias={"查看投稿"})
    async def check_post(self, event: AiocqhttpMessageEvent, post_id: int = -1):
        post = await self.pm.get_post(key="id", value=post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        msg = f"【稿件#{post_id}】\n{post.to_str()}"
        yield event.plain_result(msg)

    @filter.command("通过")
    async def approve(self, event: AiocqhttpMessageEvent):
        """(引用稿件)通过"""
        post_id = self.extract_post_id(event)
        if not post_id:
            yield event.plain_result("未检测到稿件ID")
            return

        # 更新稿件状态
        await self.pm.update_status(post_id, "approved")

        # 发布说说
        text, image_urls = await self.pm.get_text_and_images_by_id(post_id)
        images = [
            file for url in image_urls if (file := await download_file(url)) is not None
        ]
        await self.qzone.publish_emotion(text, images)

        # 通知管理员
        yield event.plain_result(f"已发布说说#{post_id}")

        # 通知投稿者
        post = await self.pm.get_post(key="id", value=post_id)
        if not post:
            return
        await self.notice_user(
            client=event.bot,
            user_id=post.uin,
            message=f"恭喜！您的投稿#{post_id}已通过并发布到空间",
        )

    @filter.command("不通过")
    async def reject(self, event: AiocqhttpMessageEvent):
        """(引用稿件)不通过 <原因>"""
        post_id = self.extract_post_id(event)
        if not post_id:
            yield event.plain_result("未检测到稿件ID")
            return
        # 更新稿件状态
        await self.pm.update_status(post_id, "rejected")

        reason = event.message_str.removeprefix("不通过").strip()
        # 通知管理员
        admin_msg = f"已拒绝稿件#{post_id}"
        if reason:
            admin_msg += f"\n理由：{reason}"
        yield event.plain_result(admin_msg)

        # 通知投稿者
        post = await self.pm.get_post(key="id", value=post_id)
        if not post:
            return

        user_msg = f"很遗憾，您的投稿#{post_id}未通过"
        if reason:
            user_msg += f"\n理由：{reason}"
        await self.notice_user(client=event.bot, user_id=post.uin, message=user_msg)

    async def terminate(self):
        """插件卸载时取消所有撤回任务"""
        await self.qzone.terminate()
        logger.info("自动撤回插件已卸载")
