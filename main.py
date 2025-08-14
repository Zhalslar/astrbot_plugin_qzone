import asyncio
from datetime import datetime
import re
import time
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core import AstrBotConfig
from astrbot.api import logger
from astrbot.core.message.components import BaseMessageComponent, Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from .core.post import Post, PostManager
from .core.utils import (
    get_image_urls,
    get_reply_message_str,
    parse_qzone_visitors,
)
from .core.api import QzoneAPI


@register(
    "astrbot_plugin_qzone",
    "Zhalslar",
    "QQ空间对接插件",
    "v1.0.2",
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

    def post_to_chain(self, title: str, post: Post) -> list[BaseMessageComponent]:
        """稿件信息->易读消息"""
        status_map = {
            "pending": "待审核",
            "approved": "已发布",
            "rejected": "被拒绝",
        }
        lines = [f"{title}{status_map.get(post.status, post.status)}"]
        lines += [
            f"时间：{datetime.fromtimestamp(post.create_time).strftime('%Y-%m-%d %H:%M')}",
            f"用户：{post.name}({post.uin})",
        ]
        if post.gin:
            lines.append(f"群聊：{post.gin}")
        if post.anon:
            lines.append("匿名：是")
        lines += [
            "------------------",
            f"{post.text}",
        ]
        chain: list[BaseMessageComponent] = [Plain("\n".join(lines))]
        for url in post.images:
            chain.append(Image.fromURL(url))
        return chain

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

    def extract_post_id(self, event: AiocqhttpMessageEvent) -> int | None:
        """从引用消息中提取稿件 ID"""
        content = get_reply_message_str(event)
        if not content or "新投稿" not in content:
            return None
        match = re.search(r"新投稿#(\d+)", content)
        return int(match.group(1)) if match else None

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_emotion(self, event: AiocqhttpMessageEvent):
        """直接发说说，无需审核"""
        post = Post(
            uin=int(event.get_sender_id()),
            name=event.get_sender_name(),
            gin=int(event.get_group_id() or 0),
            text=event.message_str.removeprefix("发说说").strip(),
            images=await get_image_urls(event),
            anon=False,
            status="approved",
            create_time=int(time.time()),
        )
        post_id = await self.pm.add(post)
        tid = await self.qzone.publish_emotion(client=event.bot, post=post)
        await self.pm.update(post_id, key="tid", value=tid)
        yield event.plain_result(f"已发布说说#{post_id}")
        logger.info(f"已发布说说#{post_id}, 说说tid: {tid}")

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
        title = f"【新投稿#{post_id}】"
        chain = self.post_to_chain(title, post)
        await self.notice_admin(event, chain)
        event.stop_event()

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("通过")
    async def approve(self, event: AiocqhttpMessageEvent):
        """(引用稿件)通过"""
        post_id = self.extract_post_id(event)
        if not post_id:
            yield event.plain_result("未检测到稿件ID")
            return

        # 更新稿件状态
        await self.pm.update(post_id, key="status", value="approved")
        post = await self.pm.get(key="id", value=post_id)
        if not post:
            return

        # 发布说说
        tid = await self.qzone.publish_emotion(client=event.bot, post=post)
        await self.pm.update(post_id, key="tid", value=tid)

        # 通知管理员
        yield event.plain_result(f"已发布说说#{post_id}")

        # 通知投稿者
        title = f"【您的投稿#{post_id}】"
        await self.notice_user(
            event,
            chain=self.post_to_chain(title, post),
            group_id=post.gin,
            user_id=post.uin,
        )

        logger.info(f"已发布说说#{post_id}, 说说tid: {tid}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("不通过")
    async def reject(self, event: AiocqhttpMessageEvent):
        """(引用稿件)不通过 <原因>"""
        post_id = self.extract_post_id(event)
        if not post_id:
            yield event.plain_result("未检测到稿件ID")
            return
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
        title = f"【稿件#{post_id}】"
        chain = self.post_to_chain(title, post)
        yield event.chain_result(chain)

    @filter.command("查看说说")
    async def emotion(self, event: AiocqhttpMessageEvent, num: int = 1):
        """查看说说 <序号>"""
        posts = await self.qzone.get_emotion(client=event.bot, num=num)
        post = posts[-1]
        if p := await self.pm.get(key="tid", value=post.tid):
            post_id = p.id
        else:
            post_id = await self.pm.add(post)
        title = f"【说说#{post_id}】"
        chain = self.post_to_chain(title, post)
        yield event.chain_result(chain)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看访客")
    async def visitor(self, event: AiocqhttpMessageEvent):
        """查看访客"""
        data = (await self.qzone.get_visitor(client=event.bot))["data"]
        msg = parse_qzone_visitors(data)
        yield event.plain_result(msg)

    @filter.command("点赞说说")
    async def like(self, event: AiocqhttpMessageEvent, num: int = 10):
        """点赞 <说说数量>"""
        posts = await self.qzone.get_emotion(client=event.bot, num=num)
        results = await asyncio.gather(
            *[self.qzone.like(client=event.bot, tid=p.tid) for p in posts],
            return_exceptions=False,
        )  # 并发点赞，返回 True/False 列表
        msg = f"点赞完成，成功 {sum(results)}/{len(posts)} 次"
        yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("评论说说")
    async def comment(
        self, event: AiocqhttpMessageEvent, post_id: int, content: str = ""
    ):
        """评论 <内容>"""
        post_id = post_id if isinstance(post_id, int) else post_id.removeprefix("#")
        post = await self.pm.get(value=post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        res = await self.qzone.comment(
            client=event.bot,
            tid=post.tid,
            content=content,
        )
        msg = (
            f"已评论说说#{post_id}: {content}"
            if "非法请求" in res.__str__()
            else "非法请求, 评论失败"
        )
        yield event.plain_result(msg)

    async def terminate(self):
        """插件卸载时关闭Qzone API网络连接"""
        await self.qzone.terminate()
        logger.info("已关闭Qzone API网络连接")
