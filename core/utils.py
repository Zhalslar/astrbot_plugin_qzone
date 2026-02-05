from typing import Union

import aiohttp

from astrbot.api import logger
from astrbot.core.message.components import At, Image, Reply
from astrbot.core.platform import AstrMessageEvent
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

BytesOrStr = Union[str, bytes]  # noqa: UP007


def get_ats(event: AiocqhttpMessageEvent) -> list[str]:
    """获取被at者们的id列表,(@增强版)"""
    ats = [str(seg.qq) for seg in event.get_messages()[1:] if isinstance(seg, At)]
    for arg in event.message_str.split(" "):
        if arg.startswith("@") and arg[1:].isdigit():
            ats.append(arg[1:])
    return ats


async def get_nickname(event: AiocqhttpMessageEvent, user_id) -> str:
    """获取指定群友的群昵称或Q名"""
    group_id = event.get_group_id()
    if group_id:
        member_info = await event.bot.get_group_member_info(
            group_id=int(group_id), user_id=int(user_id)
        )
        return member_info.get("card") or member_info.get("nickname")
    else:
        stranger_info = await event.bot.get_stranger_info(user_id=int(user_id))
        return stranger_info.get("nickname")

async def get_login_name(self, event: AiocqhttpMessageEvent) -> str:
    """获取bot的登录名"""
    self_id = event.get_self_id()
    try:
        info = await event.bot.get_login_info()
        return info.get("nickname") or self_id
    except Exception:
        return self_id


def resolve_target_id(
    event: AiocqhttpMessageEvent,
    *,
    get_sender: bool = False,
) -> str:
    if at_ids := get_ats(event):
        return at_ids[0]
    return event.get_sender_id() if get_sender else event.get_self_id()


def parse_range(event: AstrMessageEvent) -> tuple[int, int]:
    """
    解析范围参数，返回 (offset, limit)

    用户输入：
    - n        → 第 n 条
    - s~e      → 第 s 到 e 条
    - 其它 / 无 → 第 1 条
    """
    parts = event.message_str.strip().split()
    if not parts:
        return 0, 1

    end = parts[-1]

    # 范围：s~e
    if "~" in end:
        try:
            s, e = end.split("~", 1)
            s_i = int(s)
            e_i = int(e)
            if s_i <= 0 or e_i < s_i:
                raise ValueError
            return s_i - 1, e_i - s_i + 1
        except ValueError:
            return 0, 1

    # 单个数字：n
    try:
        n = int(end)
        if n <= 0:
            raise ValueError
        return n - 1, 1
    except ValueError:
        return 0, 1


async def download_file(url: str) -> bytes | None:
    """下载图片"""
    url = url.replace("https://", "http://")
    try:
        async with aiohttp.ClientSession() as client:
            response = await client.get(url)
            img_bytes = await response.read()
            return img_bytes
    except Exception as e:
        logger.error(f"图片下载失败: {e}")


async def get_image_urls(event: AstrMessageEvent, reply: bool = True) -> list[str]:
    """获取图片url列表"""
    chain = event.get_messages()
    images: list[str] = []
    # 遍历引用消息
    if reply:
        reply_seg = next((seg for seg in chain if isinstance(seg, Reply)), None)
        if reply_seg and reply_seg.chain:
            for seg in reply_seg.chain:
                if isinstance(seg, Image) and seg.url:
                    images.append(seg.url)
    # 遍历原始消息
    for seg in chain:
        if isinstance(seg, Image) and seg.url:
            images.append(seg.url)
    return images


def get_reply_message_str(event: AstrMessageEvent) -> str | None:
    """
    获取被引用的消息解析后的纯文本消息字符串。
    """
    return next(
        (
            seg.message_str
            for seg in event.message_obj.message
            if isinstance(seg, Reply)
        ),
        "",
    )
