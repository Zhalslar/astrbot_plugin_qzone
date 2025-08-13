import datetime
from typing import Sequence, Union
import aiohttp
from astrbot.core.message.components import Image, Reply
from astrbot.core.platform import AstrMessageEvent
from astrbot.api import logger

BytesOrStr = Union[str, bytes]

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


async def normalize_images(images: Sequence[BytesOrStr] | None) -> list[bytes]:
    """
    将 str/bytes 混合列表统一转成 bytes 列表：
    - str -> 下载后转 bytes（下载失败则忽略）
    - bytes -> 原样保留
    - None -> 空列表
    """
    if images is None:
        return []

    cleaned: list[bytes] = []
    for item in images:
        if isinstance(item, bytes):
            cleaned.append(item)
        elif isinstance(item, str):
            file = await download_file(item)
            if file is not None:
                cleaned.append(file)
        else:
            raise TypeError(f"image 必须是 str 或 bytes，收到 {type(item)}")
    return cleaned



def parse_qzone_visitors(data: dict) -> str:
    """
    把 QQ 空间访客接口的数据解析成易读文本。
    """
    lines = []

    # 1. 统计摘要
    lines.append(f"📊 今日访客：{data.get('todaycount', 0)} 人")
    lines.append(f"📈 最近 30 天访客：{data.get('totalcount', 0)} 人")
    lines.append("")

    # 2. 逐条访客
    items = data.get("items", [])
    if not items:
        lines.append("暂无访客记录")
        return "\n".join(lines)

    lines.append("👀 最近来访明细：")
    for idx, v in enumerate(items, 1):
        # 基本信息
        name = v.get("name", "匿名")
        qq = v.get("uin", "0")
        ts = v.get("time", 0)
        dt = datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")

        # 渠道
        src_map = {
            0: "访问空间",
            13: "查看动态",
            32: "手机QQ",
            41: "国际版QQ/TIM",
        }
        src = src_map.get(v.get("src"), f"未知({v.get('src')})")

        # 黄钻
        yellow = v.get("yellow", -1)
        vip_info = f"(LV{yellow})" if yellow > 0 else ""

        # 隐身
        hide = " (隐身)" if v.get("is_hide_visit") else ""

        lines.append(f"\n·{dt}\n{name}{vip_info}{hide}{src}")

        # 说说快照
        shuos = v.get("shuoshuoes", [])
        if shuos:
            title = shuos[0].get("name", "")
            lines.append(f"   └─ 说说：{title}")

        # 带来的人
        brought = v.get("uins", [])
        if brought:
            names = ",".join(u.get("name", "") for u in brought)
            lines.append(f"   └─ 带来了{names}")

    return "\n".join(lines)
