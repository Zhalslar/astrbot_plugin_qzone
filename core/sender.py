from aiocqhttp import CQHttp

from astrbot.api import logger
from astrbot.core.message.components import BaseMessageComponent, Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .config import PluginConfig
from .model import Post


class Sender:
    def __init__(self, config: PluginConfig):
        self.cfg = config
        self.style = None
        self._load_renderer()

    def _load_renderer(self):
        # 实例化pillowmd样式
        try:
            import pillowmd

            self.style = pillowmd.LoadMarkdownStyles(self.cfg.style_dir)
        except Exception as e:
            logger.error(f"无法加载pillowmd样式：{e}")

    async def _post_to_seg(self, post: Post) -> BaseMessageComponent:
        post_text = post.to_str()
        if self.style:
            img = await self.style.AioRender(text=post_text, useImageUrl=True)
            img_path = img.Save(self.cfg.cache_dir)
            return Image.fromFileSystem(str(img_path))
        else:
            return Plain(post_text)

    async def _send_to_admins(self, client: CQHttp, obmsg: list[dict]):
        for admin_id in self.cfg.admins_id:
            if admin_id.isdigit():
                try:
                    await client.send_private_msg(user_id=int(admin_id), message=obmsg)
                except Exception as e:
                    logger.error(f"无法反馈管理员：{e}")

    async def _send_to_manage_group(self, client: CQHttp, obmsg: list[dict]) -> bool:
        try:
            await client.send_group_msg(
                group_id=int(self.cfg.manage_group), message=obmsg
            )
            return True
        except Exception as e:
            logger.error(f"无法反馈管理群：{e}")
            return False

    async def _send_to_user(self, client: CQHttp, user_id: int, obmsg: list[dict]):
        try:
            await client.send_private_msg(user_id=int(user_id), message=obmsg)
        except Exception as e:
            logger.error(f"无法通知用户{user_id}：{e}")

    async def _send_to_group(self, client: CQHttp, group_id: int, obmsg: list[dict]):
        try:
            await client.send_group_msg(group_id=int(group_id), message=obmsg)
        except Exception as e:
            logger.error(f"无法通知群聊{group_id}：{e}")

    async def send_admin_post(
        self,
        post: Post,
        *,
        client: CQHttp | None = None,
        message: str = "",
    ):
        """通知管理群或管理员"""
        client = client or self.cfg.client
        if not client:
            logger.error("缺少客户端，无法发送消息")
            return

        chain = []
        if message:
            chain.append(Plain(message))
        post_seg = await self._post_to_seg(post)
        chain.append(post_seg)

        obmsg = await AiocqhttpMessageEvent._parse_onebot_json(MessageChain(chain))

        succ = False
        if self.cfg.manage_group:
            succ = await self._send_to_manage_group(client, obmsg)
        if not succ and self.cfg.admins_id:
            await self._send_to_admins(client, obmsg)

    async def send_user_post(
        self,
        post: Post,
        *,
        client: CQHttp | None = None,
        message: str = "",
    ):
        """通知投稿者"""
        client = client or self.cfg.client
        if not client:
            logger.error("缺少客户端，无法发送消息")
            return

        chain = []
        if message:
            chain.append(Plain(message))
        post_seg = await self._post_to_seg(post)
        chain.append(post_seg)

        obmsg = await AiocqhttpMessageEvent._parse_onebot_json(MessageChain(chain))

        if post.gin:
            await self._send_to_group(client, post.gin, obmsg)
        elif post.uin:
            await self._send_to_user(client, post.uin, obmsg)

    async def send_post(
        self,
        event: AstrMessageEvent,
        post: Post,
        *,
        message: str = "",
        send_admin: bool = False,
    ):
        if send_admin and self.cfg.admin_id:
            event.message_obj.group_id = None  # type: ignore
            event.message_obj.sender.user_id = self.cfg.admin_id

        post_text = post.to_str()

        chain = []

        if message:
            chain.append(Plain(message))

        if self.style:
            img = await self.style.AioRender(text=post_text, useImageUrl=True)
            img_path = img.Save(self.cfg.cache_dir)
            chain.append(Image(str(img_path)))
        else:
            chain.append(Plain(post_text))

        await event.send(event.chain_result(chain))

    async def send_msg(
        self,
        event: AstrMessageEvent,
        message: str = "",
    ):
        chain = []

        if self.style:
            img = await self.style.AioRender(text=message, useImageUrl=True)
            img_path = img.Save(self.cfg.cache_dir)
            chain.append(Image(str(img_path)))
        else:
            chain.append(Plain(message))

        await event.send(event.chain_result(chain))
