import random
import re
from typing import Any

from aiocqhttp import CQHttp

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.provider.provider import Provider
from astrbot.core.star.context import Context

from .post import Post


class LLMAction:
    def __init__(self, context: Context, config: AstrBotConfig, client: CQHttp):
        self.context = context
        self.config = config
        self.client = client
        self.comment_provider_id = self.config["comment_provider_id"]
        self.diary_provider_id = self.config["diary_provider_id"]

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

    def _select_prompt(self, config_key: str) -> str:
        val = self.config.get(config_key)
        if not val:
            return ""
        
        # Handle list
        if isinstance(val, list):
            if not val:
                return ""
            
            # 自动修复：检测是否被错误地拆分成了字符列表（例如 ["你", "好"]）
            # 如果列表长度大于1且所有元素都是单字符，则认为是配置迁移导致的错误，尝试合并回字符串
            if len(val) > 1 and all(isinstance(x, str) and len(x) == 1 for x in val):
                val = "".join(val)
                # 合并后 val 变为字符串，将落入下方的 str 处理逻辑
            else:
                return random.choice(val)
            
        # Handle string (legacy or recovered)
        if isinstance(val, str):
            # Support --- delimiter
            if "\n---\n" in val:
                return random.choice(re.split(r'\n-{3,}\n', val)).strip()
            return val
            
        return str(val)

    async def generate_diary(self, group_id: str = "", topic: str | None = None) -> str | None:
        """根据聊天记录生成日记"""
        provider = (
            self.context.get_provider_by_id(self.config["diary_provider_id"])
            or self.context.get_using_provider()
        )
        if not isinstance(provider, Provider):
            logger.error("未配置用于文本生成任务的 LLM 提供商")
            return None
        contexts = []

        if group_id:
            contexts = await self._get_msg_contexts(group_id)
        else:  # 随机获取一个群组
            group_list = await self.client.get_group_list()
            group_ids = [
                str(group["group_id"])
                for group in group_list
                if str(group["group_id"]) not in self.config["ignore_groups"]
            ]
            if not group_ids:
                logger.warning("未找到可用群组")
                return None
            contexts = await self._get_msg_contexts(random.choice(group_ids))
        # TODO: 更多模式

        # 系统提示，要求使用三对双引号包裹正文
        system_prompt = (
            f"# 写作主题：{topic or '从聊天内容中选一个主题'}\n\n"
            "# 输出格式要求：\n"
            '- 使用三对双引号（"""）将正文内容包裹起来。\n\n'
            + self._select_prompt("diary_prompt")
        )

        logger.debug(f"{system_prompt}\n\n{contexts}")

        try:
            llm_response = await provider.text_chat(
                system_prompt=system_prompt,
                contexts=contexts,
            )
            diary = self.extract_content(llm_response.completion_text)
            logger.info(f"LLM 生成的日记：{diary}")
            return diary

        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")

    async def generate_comment(self, post: Post) -> str | None:
        """根据帖子内容生成评论"""
        provider = (
            self.context.get_provider_by_id(self.config["comment_provider_id"])
            or self.context.get_using_provider()
        )
        if not isinstance(provider, Provider):
            logger.error("未配置用于文本生成任务的 LLM 提供商")
            return None
        try:
            content = post.text
            if post.rt_con:  # 转发文本
                content += f"\n[转发]\n{post.rt_con}"

            prompt = f"\n[帖子内容]：\n{content}"

            logger.debug(prompt)
            # Random prompt selection
            selected_prompt = self._select_prompt("comment_prompt")
            
            llm_response = await provider.text_chat(
                system_prompt=selected_prompt,
                prompt=prompt,
                image_urls=post.images,
            )
            comment = re.sub(r"[\s\u3000]+", "", llm_response.completion_text).rstrip(
                "。"
            )
            logger.info(f"LLM 生成的评论：{comment}")
            return comment

        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")
