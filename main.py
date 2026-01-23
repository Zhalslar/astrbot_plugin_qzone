import random

import pillowmd

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.campus_wall import CampusWall
from .core.config import PluginConfig
from .core.llm_action import LLMAction
from .core.operate import PostOperator
from .core.post import PostDB
from .core.qzone_api import Qzone
from .core.scheduler import AutoComment, AutoPublish
from .core.utils import get_ats, get_image_urls


class QzonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.cfg = PluginConfig(config, context)
        self.db = PostDB(self.cfg)
        self.qzone: Qzone | None = None
        self.llm: LLMAction | None = None
        self.operator: PostOperator | None = None
        self.auto_comment: AutoComment | None = None
        self.auto_publish: AutoPublish | None = None
        self.campus_wall: CampusWall | None = None

    async def initialize(self):
        """插件加载时触发"""
        # 初始化数据库
        await self.db.initialize()
        # 实例化pillowmd样式
        try:
            self.style = pillowmd.LoadMarkdownStyles(self.cfg.style_dir)
        except Exception as e:
            logger.error(f"无法加载pillowmd样式：{e}")

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
        # 加载QQ空间模块
        if not self.qzone:
            self.qzone = Qzone(client=event.bot)

        # llm内容生成器
        if not self.llm:
            self.llm = LLMAction(self.context, self.cfg, client=event.bot)

        # 加载稿件操作模块
        if not self.operator:
            self.operator = PostOperator(
                self.context, self.cfg, self.qzone, self.db, self.llm, self.style
            )

        # 加载自动评论模块
        if not self.auto_comment and self.cfg.comment_cron and self.operator:
            self.auto_comment = AutoComment(self.context, self.cfg, self.operator)

        # 加载自动发说说模块
        if not self.auto_publish and self.cfg.publish_cron and self.operator:
            self.auto_publish = AutoPublish(self.context, self.cfg, self.operator)

        # 加载表白墙模块
        if not self.campus_wall:
            self.campus_wall = CampusWall(
                self.cfg,
                self.qzone,
                self.db,
                self.style,
            )

        # 按概率触发点赞+评论
        if (
            random.random() < self.cfg.read_prob
            and event.get_sender_id() not in self.cfg.ignore_users
        ):
            await self.operator.read_feed(
                event,
                get_recent=False,
                get_sender=True,
                send_error=False,
                send_admin=self.cfg.send_admin,
            )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看访客")
    async def visitor(self, event: AiocqhttpMessageEvent):
        """查看访客"""
        if not self.qzone:
            return
        succ, data = await self.qzone.get_visitor()
        if not succ:
            await event.send(event.plain_result(data))
            logger.error(f"查看访客失败：{data}")
            return
        if not data:
            await event.send(event.plain_result("无访客记录"))
            return
        img = await self.style.AioRender(text=data, useImageUrl=True)
        img_path = img.Save(self.cfg.cache_dir)
        await event.send(event.image_result(str(img_path)))

    @filter.command("看说说", alias={"查看说说"})
    async def view_feed(self, event: AiocqhttpMessageEvent):
        """
        看说说 <@群友> <序号>
        """
        if not self.operator:
            return

        at_ids = get_ats(event)
        if at_ids:
            get_recent = False
            # 把本次要查看的用户从忽略列表中移除
            for uid in {event.get_sender_id(), *at_ids}:
                if str(uid) in self.cfg.ignore_users:
                    self.cfg.ignore_users.remove(uid)
            self.cfg.save_config()
        else:
            get_recent = True

        await self.operator.view_feed(event, get_recent=get_recent)

    @filter.command("读说说", alias={"评论说说", "评说说"})
    async def read_feed(self, event: AiocqhttpMessageEvent, at: str | None = None):
        """读说说 <序号/范围> 点赞+评论最近说说"""
        if not self.operator:
            return
        get_recent = False if str(at).startswith("@") else True
        await self.operator.read_feed(event, get_recent=get_recent)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_feed(self, event: AiocqhttpMessageEvent):
        """发说说 <内容> <图片>, 由用户指定内容"""
        if not self.operator:
            return
        text = event.message_str.partition(" ")[2]
        images = await get_image_urls(event)
        await self.operator.publish_feed(event=event, text=text, images=images)

    @filter.command("写说说", alias={"写稿", "写草稿"})
    async def write_draft(self, event: AiocqhttpMessageEvent, topic: str | None = None):
        """写说说 <主题> <图片>, 由AI写完后用‘通过稿件 ID’命令发布"""
        if not self.operator or not self.llm:
            return
        text = await self.llm.generate_diary(group_id=event.get_group_id(), topic=topic)
        images = await get_image_urls(event)
        await self.operator.publish_feed(event, text, images, publish=False)

    @filter.command("投稿")
    async def contribute_post(self, event: AiocqhttpMessageEvent):
        """投稿 <内容> <图片>"""
        if self.campus_wall:
            await self.campus_wall.contribute(event)

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("看稿", alias={"查看稿件"})
    async def view_post(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        "查看稿件 <稿件ID>, 默认最新稿件"
        if self.campus_wall:
            await self.campus_wall.view(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("过稿", alias={"通过稿件", "通过投稿"})
    async def approve_post(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """通过稿件 <稿件ID>"""
        if self.campus_wall:
            await self.campus_wall.approve(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("拒稿", alias={"拒绝稿件", "拒绝投稿"})
    async def reject_post(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """拒绝稿件 <稿件ID> <原因>"""
        if self.campus_wall:
            await self.campus_wall.reject(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删稿", alias={"删除稿件"})
    async def delete_post(
        self, event: AiocqhttpMessageEvent, input: str | int | None = None
    ):
        """删除稿件 <稿件ID>"""
        if self.campus_wall:
            await self.campus_wall.delete(event, input)
