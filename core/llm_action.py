import random
import re
from typing import Any

from aiocqhttp import CQHttp

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.context import Context

from .post import Post


class LLMAction:
    def __init__(self, context: Context, config: AstrBotConfig, client: CQHttp):
        self.context = context
        self.config = config
        self.client = client

    def _build_context(
        self, round_messages: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """
        把所有回合里的纯文本消息打包成 openai-style 的 user 上下文。
        """
        contexts: list[dict[str, str]] = []
        for msg in round_messages:
            # 提取并拼接所有 text 片段
            text_segments = [
                seg["data"]["text"] for seg in msg["message"] if seg["type"] == "text"
            ]

            text = f"{msg['sender']['nickname']}: {''.join(text_segments).strip()}"
            # 仅当真正说了话才保留
            if text:
                contexts.append({"role": "user", "content": text})
        return contexts

    async def _get_msg_contexts(self, group_id: str) -> list[dict]:
        """获取群聊历史消息"""
        message_seq = 0
        contexts: list[dict] = []
        while len(contexts) < self.config["diary_max_msg"]:
            payloads = {
                "group_id": group_id,
                "message_seq": message_seq,
                "count": 200,
                "reverseOrder": True,
            }
            result: dict = await self.client.api.call_action(
                "get_group_msg_history", **payloads
            )
            round_messages = result["messages"]
            if not round_messages:
                break
            message_seq = round_messages[0]["message_id"]

            contexts.extend(self._build_context(round_messages))
        return contexts

    @staticmethod
    def extract_content(diary: str) -> str:
        start_marker = '"""'
        end_marker = '"""'
        start = diary.find(start_marker) + len(start_marker)
        end = diary.find(end_marker, start)
        if start != -1 and end != -1:
            return diary[start:end].strip()
        return ""

    async def generate_diary(self, group_id: str = "", topic: str | None = None) -> str:
        """根据聊天记录生成日记"""
        get_using = self.context.get_using_provider()
        if not get_using:
            raise ValueError("未配置 LLM 提供商")
        contexts = []

        if group_id:
            contexts = await self._get_msg_contexts(group_id)
        else:
            group_list = await self.client.get_group_list()
            group_ids = [group["group_id"] for group in group_list]
            random_group_id = str(random.choice(group_ids))  # 随机获取一个群组
            contexts = await self._get_msg_contexts(random_group_id)
        # TODO: 更多模式

        system_prompt = (
            f"# 写作主题：{topic}\n\n"
            "请按照以下格式输出内容：\n"
            "- 直接进入正文，避免前言或无关内容。\n"
            "- 使用清晰的标题和子标题。\n"
            "- 每个段落聚焦一个主题。\n"
            "- 在段落末尾提供简短的总结。\n"
            + self.config["diary_prompt"]
            if topic
            else self.config["diary_prompt"]
        )

        # 系统提示，要求使用三对双引号包裹正文
        system_prompt = (
            f"# 写作主题：{topic or '从聊天内容中选一个主题'}\n\n"
            "# 输出格式要求：\n"
            '- 使用三对双引号（"""）将正文内容包裹起来。\n\n'
            + self.config["diary_prompt"]
        )

        logger.debug(f"{system_prompt}\n\n{contexts}")

        try:
            llm_response = await get_using.text_chat(
                system_prompt=system_prompt,
                contexts=contexts,
            )
            diary = self.extract_content(llm_response.completion_text)
            logger.info(f"LLM 生成的日记：{diary}")
            return diary

        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")

    async def generate_comment(self, post: Post) -> str:
        """根据帖子内容生成评论"""
        using_provider = self.context.get_using_provider()
        if not using_provider:
            raise ValueError("未配置 LLM 提供商")
        try:
            content = post.text
            if post.rt_con: # 转发文本
                content += f"\n[转发]\n{post.rt_con}"

            prompt = f"\n[帖子内容]：\n{content}"

            logger.debug(prompt)
            llm_response = await using_provider.text_chat(
                system_prompt=self.config["comment_prompt"],
                prompt=prompt,
                image_urls=post.images,
            )
            comment = re.sub(r"[\s\u3000]+", "", llm_response.completion_text).rstrip("。")
            logger.info(f"LLM 生成的评论：{comment}")
            return comment

        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")
