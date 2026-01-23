import random
import re
from typing import Any, Callable, Awaitable

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

    def _get_providers(self, task_type: str) -> list[str]:
        """
        Get the list of providers for a task.
        Reads from provider_id (primary) and provider_id_2 to provider_id_6.
        """
        providers = []
        base_key = ""
        
        if task_type == "comment":
            base_key = "comment_provider_id"
        elif task_type == "publish":
            base_key = "diary_provider_id"
            
        if not base_key:
            return []

        # Check primary (no suffix)
        if p1 := self.config.get(base_key):
            providers.append(p1)
            
        # Check backups 2 to 6
        for i in range(2, 7):
            key = f"{base_key}_{i}"
            if val := self.config.get(key):
                if val not in providers:
                    providers.append(val)
        
        return providers

    async def _execute_with_failover(
        self,
        task_type: str,
        execute_func: Callable[[Provider], Awaitable[Any]],
        notify_func: Callable[[str, str], Awaitable[None]] | None = None
    ) -> Any:
        """
        Execute an LLM task with failover.
        :param task_type: "comment" or "publish"
        :param execute_func: async function taking a Provider and returning result
        :param notify_func: async function taking (failed_provider_id, next_provider_id) to notify user
        """
        provider_ids = self._get_providers(task_type)
        
        if not provider_ids:
            # Try to get default provider from context directly
            default_p = self.context.get_using_provider()
            if default_p:
                try:
                    return await execute_func(default_p)
                except Exception as e:
                    raise ValueError(f"LLM Call Failed (Default Provider): {e}")
            else:
                raise ValueError("No LLM provider configured.")

        last_exception = None
        
        for i, p_id in enumerate(provider_ids):
            provider = self.context.get_provider_by_id(p_id)
            if not isinstance(provider, Provider):
                logger.warning(f"Provider ID {p_id} not found or invalid.")
                continue
            
            try:
                return await execute_func(provider)
            except Exception as e:
                last_exception = e
                logger.warning(f"LLM Provider {p_id} failed: {e}")
                
                # Check if there is a next provider
                if i < len(provider_ids) - 1:
                    next_p = provider_ids[i+1]
                    if notify_func:
                        await notify_func(p_id, next_p)
                else:
                    # No more providers
                    if notify_func:
                        await notify_func(p_id, "NONE")
        
        raise ValueError(f"All LLM providers failed. Last error: {last_exception}")

    def _build_context(
        self,
        round_messages: list[dict[str, Any]]
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

    async def generate_diary(self, group_id: str = "", topic: str | None = None, event_notify=None) -> str | None:
        """根据聊天记录生成日记"""
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
        
        system_prompt = (
            f"# 写作主题：{topic or '从聊天内容中选一个主题'}\n\n"
            "# 输出格式要求：\n"
            '- 使用三对双引号（"""）将正文内容包裹起来。\n\n'
            + self._select_prompt("diary_prompt")
        )

        logger.debug(f"{system_prompt}\n\n{contexts}")

        async def _req(provider: Provider):
            llm_response = await provider.text_chat(
                system_prompt=system_prompt,
                contexts=contexts,
            )
            return self.extract_content(llm_response.completion_text)

        async def _notify(failed_p, next_p):
            if event_notify:
                await event_notify.send(event_notify.plain_result(f"当前LLM {failed_p} 失效，正在切换到 {next_p}"))

        try:
            diary = await self._execute_with_failover("publish", _req, _notify)
            logger.info(f"LLM 生成的日记：{diary}")
            return diary
        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")

    async def generate_comment(self, post: Post, event_notify=None, index_info: str = "") -> str | None:
        """根据帖子内容生成评论"""
        
        content = post.text
        if post.rt_con:  # 转发文本
            content += f"\n[转发]\n{post.rt_con}"

        prompt = f"\n[帖子内容]：\n{content}"
        logger.debug(prompt)
        
        selected_prompt = self._select_prompt("comment_prompt")

        async def _req(provider: Provider):
            llm_response = await provider.text_chat(
                system_prompt=selected_prompt,
                prompt=prompt,
                image_urls=post.images,
            )
            return re.sub(r"[\s\u3000]+", "", llm_response.completion_text).rstrip("。")

        async def _notify(failed_p, next_p):
            if event_notify:
                prefix = f"正在评论{index_info} " if index_info else ""
                msg = f"{prefix}当前llm:{failed_p}失效正在切换到{next_p}"
                await event_notify.send(event_notify.plain_result(msg))

        try:
            comment = await self._execute_with_failover("comment", _req, _notify)
            logger.info(f"LLM 生成的评论：{comment}")
            return comment
        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")