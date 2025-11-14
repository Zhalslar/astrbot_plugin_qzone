# main.py

import time
from http.cookies import SimpleCookie
from pathlib import Path

import pillowmd

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.core.message.components import BaseMessageComponent, Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
    AiocqhttpAdapter,
)
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.auto_comment import AutoComment
from .core.auto_publish import AutoPublish
from .core.llm_action import LLMAction
from .core.post import Post, PostManager
from .core.qzone_api import Qzone
from .core.utils import (
    get_ats,
    get_image_urls,
)


@register("astrbot_plugin_qzone", "Zhalslar", "...", "...")
class QzonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        # 管理群ID，审批信息会发到此群
        self.manage_group: int = config.get("manage_group", 0)
        # pillowmd样式目录
        default_style_dir = (
            Path(get_astrbot_data_path()) / "plugins/astrbot_plugin_qzone/default_style"
        )
        self.pillowmd_style_dir = config.get("pillowmd_style_dir") or default_style_dir
        # 管理员QQ号列表，审批信息会私发给这些人
        self.admins_id: list[str] = list(set(context.get_config().get("admins_id", [])))
        # 数据库文件
        db_path = StarTools.get_data_dir("astrbot_plugin_qzone") / "posts_v2.db"
        # 缓存
        self.cache = StarTools.get_data_dir("astrbot_plugin_qzone") / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        # 数据库管理类
        self.pm = PostManager(db_path)
        # llm内容生成器
        self.llm = LLMAction(context, config)
        # QQ空间API
        self.qzone: Qzone

    async def initialize(self):
        # 初始化数据库
        await self.pm.init_db()
        # 实例化pillowmd样式
        try:
            self.style = pillowmd.LoadMarkdownStyles(self.pillowmd_style_dir)
        except Exception as e:
            logger.error(f"无法加载pillowmd样式：{e}")
        # 初始化AiocqhttpAdapter的Qzone（只取首个）
        for inst in self.context.platform_manager.platform_insts:
            if isinstance(inst, AiocqhttpAdapter):
                self.client = inst.get_client()
                await self.initialize_qzone()
                break
        # 初始化自动评论器
        if self.qzone and self.client and self.llm:
            self.auto_comment = AutoComment(
                self.context, self.config, self.qzone, self.client, self.llm
            )
            self.auto_pulish = AutoPublish(
                self.context, self.config, self.qzone, self.client, self.llm
            )

    async def initialize_qzone(self):
        """初始化Qzone"""
        try:
            cookie_str = (
                await self.client.get_cookies(domain="user.qzone.qq.com")
            ).get("cookies", "")
            self.cookies = {k: v.value for k, v in SimpleCookie(cookie_str).items()}
            self.qzone = Qzone(self.cookies)
            logger.info(f"Qzone 初始化成功: {self.cookies}")
        except Exception as e:
            logger.error(f"Qzone 初始化失败: {e}")

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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发布说说", alias={"发说说"})
    async def publish_emotion(self, event: AiocqhttpMessageEvent):
        """直接发说说，无需审核"""
        text = event.message_str.removeprefix("发说说").removeprefix("发布说说").strip()
        images = await get_image_urls(event)
        await self.qzone.publish_emotion(text, images)
        posts: list[Post] = await self.qzone.get_qzones(target_id=event.get_self_id())
        post = posts[0]
        img = await self.style.AioRender(
            text=post.to_str(), useImageUrl=True, autoPage=False
        )
        img_path = img.Save(self.cache)
        yield event.image_result(str(img_path))

    @filter.command("投稿")
    async def submit(self, event: AiocqhttpMessageEvent):
        """投稿 <文字+图片>"""
        post = Post(
            uin=int(event.get_sender_id()),
            name=event.get_sender_name(),
            gin=int(event.get_group_id() or 0),
            text=event.message_str.removeprefix("投稿").strip(),
            images=await get_image_urls(event),
            anon=False,
            status="pending",
            create_time=int(time.time()),
        )
        post_id = await self.pm.add(post)

        # 通知投稿者
        yield event.plain_result(f"您的稿件#{post_id}已提交，请耐心等待审核")

        # 通知管理员
        img = await self.style.AioRender(
            text=post.to_str(), useImageUrl=True, autoPage=False
        )
        img_path = img.Save(self.cache)
        await self.notice_admin(event, [Image.fromFileSystem(str(img_path))])
        event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("通过投稿")
    async def approve(self, event: AiocqhttpMessageEvent, post_id: int):
        """通过投稿 <稿件ID>"""
        # 更新稿件状态
        await self.pm.update(post_id, key="status", value="approved")
        post = await self.pm.get(key="id", value=post_id)
        if not post:
            return

        # 发布说说
        tid = await self.qzone.publish_emotion(text=post.text, images=post.images)
        await self.pm.update(post_id, key="tid", value=tid)

        # 通知管理员
        yield event.plain_result(f"已发布说说#{post_id}")

        # 通知投稿者
        img = await self.style.AioRender(
            text=post.to_str(), useImageUrl=True, autoPage=False
        )
        img_path = img.Save(self.cache)
        await self.notice_user(
            event,
            chain=[Image.fromFileSystem(str(img_path))],
            group_id=post.gin,
            user_id=post.uin,
        )

        logger.info(f"已发布说说#{post_id}, 说说tid: {tid}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("拒绝投稿")
    async def reject(self, event: AiocqhttpMessageEvent, post_id: int):
        """拒绝投稿 <稿件ID> <原因>"""
        # 更新稿件状态
        await self.pm.update(post_id, key="status", value="rejected")
        post = await self.pm.get(key="id", value=post_id)
        if not post:
            return

        reason = event.message_str.removeprefix("不通过").strip()
        # 通知管理员
        admin_msg = f"已拒绝稿件#{post_id}"
        if reason:
            admin_msg += f"\n理由：{reason}"
        yield event.plain_result(admin_msg)

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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看稿件")
    async def check_post(self, event: AiocqhttpMessageEvent, post_id: int = -1):
        post = await self.pm.get(key="id", value=post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        img = await self.style.AioRender(
            text=post.to_str(), useImageUrl=True, autoPage=False
        )
        img_path = img.Save(self.cache)
        yield event.image_result(str(img_path))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看访客")
    async def visitor(self, event: AiocqhttpMessageEvent):
        """查看访客"""
        data = (await self.qzone.get_visitor())["data"]
        text = self.qzone.parse_qzone_visitors(data)
        img = await self.style.AioRender(text=text, useImageUrl=True, autoPage=True)
        img_path = img.Save(self.cache)
        yield event.image_result(str(img_path))

    async def get_post(self, event: AiocqhttpMessageEvent) -> Post:
        """获取说说，返回Post对象"""
        at_ids = get_ats(event)
        target_id = at_ids[0] if at_ids else event.get_sender_id()
        end_parm = event.message_str.split(" ")[-1]
        index = int(end_parm) if end_parm.isdigit() else 1
        posts: list[Post] = await self.qzone.get_qzones(target_id=target_id, pos=index)
        if posts:
            return posts[0]
        else:
            await event.send(event.plain_result("没发现有说说"))
            event.stop_event()
            raise StopIteration

    @filter.command("查看说说")
    async def view_qzone(self, event: AiocqhttpMessageEvent):
        """查看说说 <@群友> <序号>"""
        post = await self.get_post(event)
        img = await self.style.AioRender(
            text=post.to_str(), useImageUrl=True, autoPage=False
        )
        img_path = img.Save(self.cache)
        yield event.image_result(str(img_path))

    @filter.command("点赞说说")
    async def like(self, event: AiocqhttpMessageEvent):
        """点赞说说 <@群友> <序号>"""
        post = await self.get_post(event)
        res = await self.qzone.like(fid=post.tid, target_id=str(post.uin))
        if res.get("code") == 0:
            yield event.plain_result(f"已给{post.name}的说说点赞: {post.text[:10]}")
        else:
            yield event.plain_result(f"点赞说说失败: {res}")
            logger.error(f"点赞失败: {res}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("评论说说")
    async def comment(self, event: AiocqhttpMessageEvent):
        """评论说说 <@群友> <序号>"""
        post = await self.get_post(event)
        content = await self.llm.generate_comment(post)
        res = await self.qzone.comment(
            fid=post.tid,
            target_id=str(post.uin),
            content=content,
        )
        if res.get("code") == 0:
            yield event.plain_result(
                f"已评论{post.name}的说说({post.text[:6]}...): {content}"
            )
        else:
            yield event.plain_result(f"评论失败: {res}")
            logger.error(f"评论失败: {res}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("写日记")
    async def keep_diary(self, event: AiocqhttpMessageEvent):
        """根据聊天记录总结、发布日记"""
        diary_text = await self.llm.generate_diary(
            client=self.client, group_id=event.get_group_id()
        )
        await self.qzone.publish_emotion(text=diary_text)
        posts: list[Post] = await self.qzone.get_qzones(target_id=event.get_self_id())
        post = posts[0]
        img = await self.style.AioRender(
            text=post.to_str(), useImageUrl=True, autoPage=False
        )
        img_path = img.Save(self.cache)
        yield event.image_result(str(img_path))

    async def terminate(self):
        """插件卸载时关闭Qzone API网络连接"""
        await self.qzone.terminate()
        await self.auto_comment.terminate()
        await self.auto_pulish.terminate()
