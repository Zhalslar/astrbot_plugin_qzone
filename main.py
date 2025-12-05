# main.py

import asyncio
import random
from pathlib import Path

import pillowmd

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.core.config.default import VERSION
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.core.utils.version_comparator import VersionComparator

from .core.campus_wall import CampusWall
from .core.llm_action import LLMAction
from .core.operate import PostOperator
from .core.post import PostDB
from .core.qzone_api import Qzone
from .core.scheduler import AutoComment, AutoPublish
from .core.utils import get_ats, get_image_urls


@register("astrbot_plugin_qzone", "Zhalslar", "...", "...")
class QzonePlugin(Star):
    # 数据库版本
    DB_VERSION = 4

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config

        # 检查版本
        if not VersionComparator.compare_version(VERSION, "4.1.0") >= 0:
            raise Exception("AstrBot 版本过低, 请升级至 4.1.0 或更高版本")

        # pillowmd样式目录
        default_style_dir = (
            Path(get_astrbot_data_path()) / "plugins/astrbot_plugin_qzone/default_style"
        )
        self.pillowmd_style_dir = config.get("pillowmd_style_dir") or default_style_dir

        # 数据库文件
        self.db_path: Path = (
            StarTools.get_data_dir("astrbot_plugin_qzone")
            / f"posts_{self.DB_VERSION}.db"
        )
        # 缓存
        self.cache: Path = StarTools.get_data_dir("astrbot_plugin_qzone") / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        # 数据库管理器
        self.db = PostDB(self.db_path)

    async def initialize(self):
        """加载、重载插件时触发"""
        # 初始化数据库
        await self.db.initialize()
        # 实例化pillowmd样式
        try:
            self.style = pillowmd.LoadMarkdownStyles(self.pillowmd_style_dir)
        except Exception as e:
            logger.error(f"无法加载pillowmd样式：{e}")

        asyncio.create_task(self.initialize_qzone(False))

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        """平台加载完成时"""
        asyncio.create_task(self.initialize_qzone(True))

    async def initialize_qzone(self, wait_ws_connected: bool = False):
        """初始化QQ空间、自动评论模块、自动发说说模块"""
        client = None
        for inst in self.context.platform_manager.platform_insts:
            if isinstance(inst, AiocqhttpAdapter):
                if client := inst.get_client():
                    break
        if not client:
            return
        # 等待 ws 连接完成
        if wait_ws_connected:
            ws_connected = asyncio.Event()

            @client.on_websocket_connection
            def _(_):  # 连接成功时触发
                ws_connected.set()

            try:
                await asyncio.wait_for(ws_connected.wait(), timeout=10)
            except asyncio.TimeoutError:
                logger.warning("等待 aiocqhttp WebSocket 连接超时")

        # 加载QQ空间模块
        self.qzone = Qzone(client)

        # llm内容生成器
        self.llm = LLMAction(self.context, self.config, client)

        # 加载稿件操作模块
        self.operator = PostOperator(
            self.context, self.config, self.qzone, self.db, self.llm, self.style
        )

        # 加载自动评论模块
        if self.config.get("comment_cron"):
            self.auto_comment = AutoComment(self.context, self.config, self.operator)

        # 加载自动发说说模块
        if self.config.get("publish_cron"):
            self.auto_publish = AutoPublish(self.context, self.config, self.operator)

        # 加载表白墙模块
        self.campus_wall = CampusWall(
            self.context,
            self.config,
            self.qzone,
            self.db,
            self.style,
        )
        logger.info("表白墙模块加载完毕！")

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
        img = await self.style.AioRender(text=data, useImageUrl=True)
        img_path = img.Save(self.cache)
        await event.send(event.image_result(str(img_path)))

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def prob_read_feed(self, event: AiocqhttpMessageEvent):
        """按概率触发点赞+评论"""
        if (
            random.random() < self.config["read_prob"]
            and event.get_sender_id() not in self.config["ignore_users"]
        ):
            await self.operator.read_feed(
                event,
                get_recent=False,
                get_sender=True,
                send_error=False,
                send_admin=self.config["send_admin"],
            )

    @filter.command("看说说", alias={"查看说说"})
    async def view_feed(
        self, event: AiocqhttpMessageEvent, at: str | None = None
    ) -> None:
        """
        看说说 <@群友> <序号>
        """
        at_ids = get_ats(event)
        get_recent = not at_ids

        # 把本次要查看的用户从忽略列表中移除
        for uid in {event.get_sender_id(), *at_ids}:
            if int(uid) in self.config["ignore_users"]:
                self.config["ignore_users"].remove(int(uid))

        self.config.save_config()
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
        text = event.message_str.removeprefix("发说说").strip()
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

    async def terminate(self):
        """插件卸载时"""
        if hasattr(self, "qzone"):
            await self.qzone.terminate()
        if hasattr(self, "auto_comment"):
            await self.auto_comment.terminate()
        if hasattr(self, "auto_publish"):
            await self.auto_publish.terminate()
