# main.py

import asyncio
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

from .core.auto_comment import AutoComment
from .core.auto_publish import AutoPublish
from .core.campus_wall import CampusWall
from .core.llm_action import LLMAction
from .core.post import Post, PostDB
from .core.qzone_api import Qzone
from .core.utils import get_ats, get_image_urls, get_nickname


@register("astrbot_plugin_qzone", "Zhalslar", "...", "...")
class QzonePlugin(Star):
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
        self.db_path = StarTools.get_data_dir("astrbot_plugin_qzone") / "posts_v2.db"
        # 缓存
        self.cache = StarTools.get_data_dir("astrbot_plugin_qzone") / "cache"
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

        # 加载、重载插件时登录QQ空间
        asyncio.create_task(self.initialize_qzone(False))

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        """平台加载完成时，登录QQ空间"""
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

        # 登录QQ空间
        self.qzone = Qzone(client)

        # llm内容生成器
        self.llm = LLMAction(self.context, self.config, client)

        # 加载自动评论模块
        if self.config.get("comment_cron"):
            self.auto_comment = AutoComment(
                self.context, self.config, self.qzone, self.llm
            )
            logger.info("自动发说说模块加载完毕！")

        # 加载自动发说说模块
        if self.config.get("comment_cron"):
            self.auto_publish = AutoPublish(
                self.context, self.config, self.qzone, self.llm
            )
            logger.info("自动发说说模块加载完毕！")

        # 加载表白墙模块
        if self.config.get("campus_wall_switch"):
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
        res = await self.qzone.get_visitor()
        if error := res.get("error"):
            yield event.plain_result(error)
            logger.error(f"查看访客失败：{error}")
            return
        data = res.get("data")
        if not data:
            yield event.plain_result("无访客记录")
            return
        text = self.qzone.parse_visitors(data)
        img = await self.style.AioRender(text=text, useImageUrl=True, autoPage=True)
        img_path = img.Save(self.cache)
        yield event.image_result(str(img_path))

    async def _get_posts(
        self, event: AiocqhttpMessageEvent, target_id: str = ""
    ) -> list[Post]:
        """获取说说，返回稿件列表"""
        # 解析目标用户
        if not target_id:
            at_ids = get_ats(event)
            target_id = at_ids[0] if at_ids else event.get_sender_id()

        # 解析范围参数
        end_parm = event.message_str.split(" ")[-1]
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
        res: dict = await self.qzone.get_posts(target_id=target_id, pos=index, num=num)

        # 处理错误
        if error := res.get("error"):
            await event.send(event.plain_result(error))
            logger.error(f"获取说说失败：{error}")
            event.stop_event()
            raise error

        # 解析结果
        posts = self.qzone.parse_posts(res)
        if not posts:
            await event.send(event.plain_result("获取不到说说"))
            event.stop_event()
            raise StopIteration

        # 存到数据库
        for post in posts:
            await post.save(self.db)

        return posts

    @filter.command("查看说说")
    async def view_qzone(self, event: AiocqhttpMessageEvent):
        """查看说说 <@群友> <序号>"""
        posts = await self._get_posts(event)
        for post in posts:
            img_path = await post.to_image(self.style)
            yield event.image_result(img_path)

    @filter.command("点赞说说")
    async def like(self, event: AiocqhttpMessageEvent):
        """点赞说说 <@群友> <序号>"""
        posts = await self._get_posts(event)
        for post in posts:
            res = await self.qzone.like(fid=post.tid, target_id=str(post.uin))
            if error := res.get("error"):
                yield event.plain_result(error)
                logger.error(f"点赞失败: {error}")
                continue
            yield event.plain_result(f"已给{post.name}的说说点赞: {post.text[:10]}")

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
            if error := res.get("error"):
                yield event.plain_result(error)
                logger.error(f"评论失败: {error}")
                continue

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
            await post.save(self.db)
            # 展示
            img_path = await post.to_image(self.style)
            yield event.image_result(img_path)

    # @filter.command("测试")
    # async def test(self, event: AiocqhttpMessageEvent):
    #     await self.qzone.get_recent_posts()
    #     event.stop_event()

    #@filter.command("删除说说") # 接口测试中
    async def delete_qzone(self, event: AiocqhttpMessageEvent):
        """删除说说 <序号>"""
        posts = await self._get_posts(event=event, target_id=event.get_self_id())
        for post in posts:
            res = await self.qzone.delete(post.tid)
            if res.get("code") == 0:
                yield event.plain_result(f"已删除{post.name}的说说: {post.text[:10]}")
            else:
                yield event.plain_result(f"删除失败: {res.get('message')}")

    async def _publish(
        self,
        event: AiocqhttpMessageEvent,
        text: str,
        images: list[str],
        publish: bool = True,
    ):
        """发说说封装"""
        self_id = event.get_self_id()
        post = Post(
            uin=int(self_id),
            name=await get_nickname(event, self_id),
            gin=int(event.get_group_id() or 0),
            text=text,
            images=images,
            status="pending",
        )
        if publish:
            res = await self.qzone.publish(post)
            if error := res.get("error"):
                await event.send(event.plain_result(error))
                logger.error(f"发布说说失败：{error}")
                event.stop_event()
                raise error
            post.tid = res["tid"]
            post.create_time = res["now"]
            post.status = "approved"

        await post.save(self.db)
        img_path = await post.to_image(self.style)
        await event.send(event.image_result(img_path))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_handle(self, event: AiocqhttpMessageEvent):
        """发说说 <内容> <图片>, 由用户指定内容"""
        text = event.message_str.removeprefix("发说说").strip()
        images = await get_image_urls(event)
        await self._publish(event, text, images)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("写说说")
    async def keep_diary(self, event: AiocqhttpMessageEvent, topic: str | None = None):
        """写说说 <主题> <图片>, 由AI生成内容后直接发布"""
        text = await self.llm.generate_diary(group_id=event.get_group_id(), topic=topic)
        images = await get_image_urls(event)
        await self._publish(event, text, images)

    @filter.command("写稿", alias={"写草稿"})
    async def write_draft(self, event: AiocqhttpMessageEvent, topic: str | None = None):
        """写稿 <主题> <图片>, 由AI写完后用‘通过稿件 ID’命令发布"""
        text = await self.llm.generate_diary(group_id=event.get_group_id(), topic=topic)
        images = await get_image_urls(event)
        await self._publish(event, text, images, publish=False)

    @filter.command("投稿")
    async def contribute(self, event: AiocqhttpMessageEvent):
        """投稿 <内容> <图片>"""
        await self.campus_wall.contribute(event)

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("查看稿件")
    async def view_post(self, event: AiocqhttpMessageEvent, input: str | int):
        "查看稿件 <稿件ID>, 默认最新稿件"
        await self.campus_wall.view(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("通过稿件")
    async def approve_post(self, event: AiocqhttpMessageEvent, input: str | int):
        """通过稿件 <稿件ID>"""
        await self.campus_wall.approve(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("拒绝稿件")
    async def reject_post(self, event: AiocqhttpMessageEvent, input: str | int):
        """拒绝稿件 <稿件ID> <原因>"""
        await self.campus_wall.reject(event, input)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删除稿件")
    async def delete_post(self, event: AiocqhttpMessageEvent, input: str | int):
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
