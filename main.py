# main.py

import asyncio
from pathlib import Path

import pillowmd

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.auto_comment import AutoComment
from .core.auto_publish import AutoPublish
from .core.campus_wall import CampusWall
from .core.llm_action import LLMAction
from .core.post import Post, PostManager
from .core.qzone_api import Qzone
from .core.utils import get_ats, get_image_urls, get_nickname


@register("astrbot_plugin_qzone", "Zhalslar", "...", "...")
class QzonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config

        # pillowmd样式目录
        default_style_dir = (
            Path(get_astrbot_data_path()) / "plugins/astrbot_plugin_qzone/default_style"
        )
        self.pillowmd_style_dir = config.get("pillowmd_style_dir") or default_style_dir

        # 数据库文件
        self.db_path = StarTools.get_data_dir("astrbot_plugin_qzone") / "posts_v2.db"
        # 缓存
        self.cache = StarTools.get_data_dir("astrbot_plugin_qzone") / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        # 数据库管理器
        self.pm = PostManager(self.db_path)

    async def initialize(self):
        """加载、重载插件时触发"""
        # 初始化数据库
        await self.pm.init_db()
        # 实例化pillowmd样式
        try:
            self.style = pillowmd.LoadMarkdownStyles(self.pillowmd_style_dir)
        except Exception as e:
            logger.error(f"无法加载pillowmd样式：{e}")

        # 加载、重载插件时登录QQ空间
        await self.initialize_qzone(False)

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        """平台加载完成时，登录QQ空间"""
        await self.initialize_qzone(True)

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

        # 登录QQ空间（独立运行）
        self.qzone = Qzone(client)
        asyncio.create_task(self.qzone.login())

        # llm内容生成器
        self.llm = LLMAction(self.context, self.config, client)

        # 加载自动评论模块
        # if self.config.get("comment_cron"):
        #     self.auto_comment = AutoComment(
        #         self.context, self.config, self.qzone, self.llm
        #     )
        # 加载自动发说说模块
        if self.config.get("comment_cron"):
            self.auto_publish = AutoPublish(
                self.context, self.config, self.qzone, self.llm
            )

        # 加载表白墙模块
        self.campus_wall = CampusWall(
            self.context,
            self.config,
            self.qzone,
            self.llm,
            self.pm,
            self.cache,
            self.style,
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看访客")
    async def visitor(self, event: AiocqhttpMessageEvent):
        """查看访客"""
        data = (await self.qzone.get_visitor())["data"]
        text = self.qzone.parse_qzone_visitors(data)
        img = await self.style.AioRender(text=text, useImageUrl=True, autoPage=True)
        img_path = img.Save(self.cache)
        yield event.image_result(str(img_path))

    async def _get_posts(self, event: AiocqhttpMessageEvent) -> list[Post]:
        """获取说说，返回稿件列表"""
        # 解析目标用户
        at_ids = get_ats(event)
        target_id = at_ids[0] if at_ids else event.get_sender_id()
        end_parm = event.message_str.split(" ")[-1]

        # 解析范围参数
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

        # 获取说说, pos为开始位置， num为获取数量
        posts: list[Post] = await self.qzone.get_qzones(
            target_id=target_id, pos=index, num=num
        )

        if posts:
            # 顺便存到数据库
            for p in posts:
                p.id = await self.pm.add(p)
            # 返回结果
            return posts
        else:
            await event.send(event.plain_result("获取不到说说"))
            event.stop_event()
            raise StopIteration

    @filter.command("查看说说")
    async def view_qzone(self, event: AiocqhttpMessageEvent):
        """查看说说 <@群友> <序号>"""
        posts = await self._get_posts(event)
        for post in posts:
            img = await self.style.AioRender(
                text=post.to_str(), useImageUrl=True, autoPage=False
            )
            img_path = img.Save(self.cache)
            yield event.image_result(str(img_path))

    @filter.command("点赞说说")
    async def like(self, event: AiocqhttpMessageEvent):
        """点赞说说 <@群友> <序号>"""
        posts = await self._get_posts(event)
        for post in posts:
            res = await self.qzone.like(fid=post.tid, target_id=str(post.uin))
            if res.get("code") == 0:
                yield event.plain_result(f"已给{post.name}的说说点赞: {post.text[:10]}")
            else:
                yield event.plain_result(f"点赞失败: {res.get('message')}")
                logger.error(f"点赞失败: {res}")

    # @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("评论说说")
    async def comment(self, event: AiocqhttpMessageEvent):
        """评论说说 <@群友> <序号>"""
        posts = await self._get_posts(event)
        for post in posts:
            content = await self.llm.generate_comment(post)
            res = await self.qzone.comment(
                fid=post.tid,
                target_id=str(post.uin),
                content=content,
            )
            # 评论成功
            if res.get("code") == 0:
                # 同步评论到数据库
                bot_id = event.get_self_id()
                bot_name = await get_nickname(event, bot_id)
                comment = {
                    "content": content,
                    "qq_account": bot_id,
                    "nickname": bot_name,
                    "comment_tid": post.tid,
                    "created_time": post.create_time,
                }
                # 更新数据
                post.comments.append(comment)
                await self.pm.update(post.id, key="comments", value=post.comments)
                # 展示
                img = await self.style.AioRender(
                    text=post.to_str(), useImageUrl=True, autoPage=False
                )
                img_path = img.Save(self.cache)
                yield event.image_result(str(img_path))

            # 评论失败
            else:
                yield event.plain_result(f"评论失败: {res.get('message')}")
                logger.error(f"评论失败: {res}")

    async def _publish(
        self, event: AiocqhttpMessageEvent, text: str, images: list[str]
    ):
        """发说说"""
        await self.qzone.publish_emotion(text, images)
        post = Post(
            uin=int(event.get_self_id()),
            name="我",
            gin=int(event.get_group_id() or 0),
            text=text,
            images=images,
        )
        post.id = await self.pm.add(post)
        img = await self.style.AioRender(
            text=post.to_str(), useImageUrl=True, autoPage=False
        )
        img_path = img.Save(self.cache)
        await event.send(event.image_result(str(img_path)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_handle(self, event: AiocqhttpMessageEvent):
        """发说说 <内容> <图片>, 用户指定内容"""
        text = event.message_str.removeprefix("发说说").strip()
        images = await get_image_urls(event)
        await self.qzone.publish_emotion(text, images)
        await self._publish(event, text, images)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("写说说")
    async def keep_diary(self, event: AiocqhttpMessageEvent, topic: str | None = None):
        """写说说 <主题> <图片>, 由AI生成"""
        text = await self.llm.generate_diary(group_id=event.get_group_id(), topic=topic)
        images = await get_image_urls(event)
        await self.qzone.publish_emotion(text=text, images=images)
        await self._publish(event, text, images)

    @filter.command("写草稿")
    async def write_draft(self, event: AiocqhttpMessageEvent, topic: str | None = None):
        """写草稿 <主题> <图片>, 只写不发"""
        text = await self.llm.generate_diary(group_id=event.get_group_id(), topic=topic)
        images = await get_image_urls(event)
        await self._publish(event, text, images)

    @filter.command("投稿")
    async def contribute(self, event: AiocqhttpMessageEvent):
        """投稿 <内容> <图片>"""
        await self.campus_wall.contribute(event)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看稿件")
    async def view_post(self, event: AiocqhttpMessageEvent, post_id: int = -1):
        "查看稿件 <稿件ID>, 默认最新稿件"
        await self.campus_wall.view(event, post_id)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("通过稿件")
    async def approve_post(self, event: AiocqhttpMessageEvent, post_id: int):
        """通过投稿 <稿件ID>"""
        await self.campus_wall.approve(event, post_id)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("拒绝稿件")
    async def reject_post(self, event: AiocqhttpMessageEvent, post_id: int):
        """拒绝投稿 <稿件ID> <原因>"""
        await self.campus_wall.reject(event, post_id)

    async def terminate(self):
        """插件卸载时"""
        if hasattr(self, "qzone"):
            await self.qzone.terminate()
        if hasattr(self, "auto_comment"):
            await self.auto_comment.terminate()
        if hasattr(self, "auto_publish"):
            await self.auto_publish.terminate()
