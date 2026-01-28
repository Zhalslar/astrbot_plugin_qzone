import random

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.campus_wall import CampusWall
from .core.config import PluginConfig
from .core.db import PostDB
from .core.llm_action import LLMAction
from .core.operate import PostOperator
from .core.qzone_api import Qzone
from .core.scheduler import AutoComment, AutoPublish
from .core.sender import Sender
from .core.utils import get_ats, get_image_urls


class QzonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # 配置
        self.cfg = PluginConfig(config, context)
        # QQ空间
        self.qzone = Qzone(self.cfg)
        # 数据库
        self.db = PostDB(self.cfg)
        # LLM模块
        self.llm = LLMAction(self.cfg)
        # 消息发送器
        self.sender = Sender(self.cfg)
        # 操作器
        self.operator = PostOperator(
            self.cfg, self.qzone, self.db, self.llm, self.sender
        )
        # 表白墙
        self.campus_wall = CampusWall(self.cfg, self.qzone, self.db, self.sender)
        # 自动评论模块
        self.auto_comment: AutoComment | None = None
        # 自动发说说模块
        self.auto_publish: AutoPublish | None = None

    async def initialize(self):
        """插件加载时触发"""
        await self.db.initialize()

        if not self.auto_comment and self.cfg.trigger.comment_cron:
            self.auto_comment = AutoComment(self.cfg, self.operator)

        if not self.auto_publish and self.cfg.trigger.publish_cron:
            self.auto_publish = AutoPublish(self.cfg, self.operator)

    async def terminate(self):
        """插件卸载时"""
        if self.qzone:
            await self.qzone.terminate()
        if self.auto_comment:
            await self.auto_comment.terminate()
        if self.auto_publish:
            await self.auto_publish.terminate()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def prob_read_feed(self, event: AiocqhttpMessageEvent):
        """监听消息"""
        if not self.cfg.client:
            self.cfg.client = event.bot

        # 按概率触发点赞+评论
        sender_id = event.get_sender_id()
        if (
            not self.cfg.source.is_ignore_user(sender_id)
            and random.random() < self.cfg.trigger.read_prob
        ):
            await self.operator.read_feed(
                event, get_recent=False, get_sender=True, send_error=False
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看访客")
    async def visitor(self, event: AiocqhttpMessageEvent):
        """查看访客"""
        succ, data = await self.qzone.get_visitor()
        if not succ:
            await event.send(event.plain_result(data))
            logger.error(f"查看访客失败：{data}")
            return
        if not data:
            await event.send(event.plain_result("无访客记录"))
            return
        await self.sender.send_msg(event, data)

    @filter.command("看说说", alias={"查看说说"})
    async def view_feed(self, event: AiocqhttpMessageEvent):
        """
        看说说 <@群友> <序号>
        """
        at_ids = get_ats(event)
        if at_ids:
            get_recent = False
            self.cfg.remove_ignore_users(at_ids)
        else:
            get_recent = True
            sender_id = event.get_sender_id()
            self.cfg.remove_ignore_users(sender_id)

        await self.operator.view_feed(event, get_recent=get_recent)

    @filter.command("读说说", alias={"评论说说", "评说说"})
    async def read_feed(self, event: AiocqhttpMessageEvent, at: str | None = None):
        """读说说 <序号/范围> 点赞+评论最近说说"""
        get_recent = False if str(at).startswith("@") else True
        await self.operator.read_feed(event, get_recent=get_recent)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_feed(self, event: AiocqhttpMessageEvent):
        """发说说 <内容> <图片>, 由用户指定内容"""
        text = event.message_str.partition(" ")[2]
        images = await get_image_urls(event)
        await self.operator.publish_feed(event=event, text=text, images=images)

    @filter.command("写说说", alias={"写稿", "写草稿"})
    async def write_draft(self, event: AiocqhttpMessageEvent, topic: str | None = None):
        """写说说 <主题> <图片>, 由AI写完后用‘通过稿件 ID’命令发布"""
        text = await self.llm.generate_diary(group_id=event.get_group_id(), topic=topic)
        images = await get_image_urls(event)
        await self.operator.publish_feed(event, text, images, publish=False)

    @filter.command("投稿")
    async def contribute_post(self, event: AiocqhttpMessageEvent):
        """投稿 <内容> <图片>"""
        await self.campus_wall.contribute(event)

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("看稿", alias={"查看稿件"})
    async def view_post(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        "查看稿件 <稿件ID>, 默认最新稿件"
        await self.campus_wall.view(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("过稿", alias={"通过稿件", "通过投稿"})
    async def approve_post(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """通过稿件 <稿件ID>"""
        await self.campus_wall.approve(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("拒稿", alias={"拒绝稿件", "拒绝投稿"})
    async def reject_post(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """拒绝稿件 <稿件ID> <原因>"""
        await self.campus_wall.reject(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删稿", alias={"删除稿件"})
    async def delete_post(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """删除稿件 <稿件ID>"""
        await self.campus_wall.delete(event, input)
