import copy
import random
import re
from typing import Any

from astrbot.api import logger
from astrbot.core.provider.provider import Provider

from .config import PluginConfig
from .model import Comment, Post


class LLMAction:
    def __init__(self, config: PluginConfig):
        self.cfg = config
        self.context = config.context

    @staticmethod
    def _join_prompt_parts(*parts: str) -> str:
        return "\n\n".join(part.strip() for part in parts if part and part.strip())

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
        if not self.cfg.client:
            raise RuntimeError("客户端未初始化")
        while len(contexts) < self.cfg.source.post_max_msg:
            payloads = {
                "group_id": group_id,
                "message_seq": message_seq,
                "count": 200,
                "reverseOrder": True,
            }
            result: dict = await self.cfg.client.api.call_action(
                "get_group_msg_history", **payloads
            )
            round_messages = result["messages"]
            if not round_messages:
                break
            message_seq = round_messages[0]["message_id"]

            contexts.extend(self._build_context(round_messages))
        return contexts

    @staticmethod
    def _get_event_umo(event: Any | None) -> str | None:
        umo = getattr(event, "unified_msg_origin", None)
        return str(umo) if umo else None

    def _get_event_platform_name(self, event: Any | None) -> str:
        getter = getattr(event, "get_platform_name", None)
        if callable(getter):
            try:
                platform_name = getter()
            except Exception:
                platform_name = None
            if platform_name:
                return str(platform_name)

        umo = self._get_event_umo(event)
        if umo and ":" in umo:
            return umo.split(":", 1)[0]
        return ""

    def _get_provider_settings(self, event: Any | None) -> dict[str, Any]:
        umo = self._get_event_umo(event)
        cfg = self.context.get_config(umo) if umo else self.context.get_config()
        provider_settings = cfg.get("provider_settings", {})
        return provider_settings if isinstance(provider_settings, dict) else {}

    def _get_provider(
        self, provider_id: str, event: Any | None = None
    ) -> Provider | None:
        provider = self.context.get_provider_by_id(provider_id) if provider_id else None
        if isinstance(provider, Provider):
            return provider

        try:
            umo = self._get_event_umo(event)
            provider = (
                self.context.get_using_provider(umo)
                if umo
                else self.context.get_using_provider()
            )
        except Exception as e:
            logger.error(f"获取当前会话的 LLM 提供商失败: {e}")
            return None

        return provider if isinstance(provider, Provider) else None

    async def _get_persona_context(
        self, event: Any | None
    ) -> tuple[str, list[dict[str, Any]]]:
        if not event:
            return "", []

        umo = self._get_event_umo(event)
        if not umo:
            return "", []

        try:
            conversation_persona_id = None
            cid = await self.context.conversation_manager.get_curr_conversation_id(umo)
            if cid:
                conversation = await self.context.conversation_manager.get_conversation(
                    umo, cid
                )
                if conversation:
                    conversation_persona_id = conversation.persona_id

            (
                persona_id,
                persona,
                _,
                _,
            ) = await self.context.persona_manager.resolve_selected_persona(
                umo=umo,
                conversation_persona_id=conversation_persona_id,
                platform_name=self._get_event_platform_name(event),
                provider_settings=self._get_provider_settings(event),
            )

            if not persona and persona_id:
                persona = self.context.persona_manager.get_persona_v3_by_id(persona_id)

            if not persona:
                return "", []

            persona_prompt = str(persona.get("prompt") or "").strip()
            begin_dialogs = copy.deepcopy(persona.get("_begin_dialogs_processed") or [])
            return persona_prompt, begin_dialogs
        except Exception as e:
            logger.warning(f"解析当前会话人格失败，将回退到插件默认任务提示词: {e}")
            return "", []

    async def _build_request_context(
        self,
        *,
        event: Any | None,
        task_prompt: str,
        contexts: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        persona_prompt, persona_contexts = await self._get_persona_context(event)
        system_prompt = self._join_prompt_parts(
            "# Persona Instructions\n\n" + persona_prompt if persona_prompt else "",
            task_prompt,
        )
        merged_contexts = [*persona_contexts]
        if contexts:
            merged_contexts.extend(contexts)
        return system_prompt, merged_contexts

    @staticmethod
    def extract_content(raw: str) -> str:
        start_marker = '"""'
        end_marker = '"""'
        start = raw.find(start_marker) + len(start_marker)
        end = raw.find(end_marker, start)
        if start != -1 and end != -1:
            return raw[start:end].strip()
        return ""

    async def generate_post(
        self,
        group_id: str = "",
        topic: str | None = None,
        *,
        event: Any | None = None,
    ) -> str | None:
        """生成帖子"""
        provider = self._get_provider(self.cfg.llm.post_provider_id, event)
        if not isinstance(provider, Provider):
            raise RuntimeError("未配置用于文本生成任务的 LLM 提供商")

        if not self.cfg.client:
            raise RuntimeError("客户端未初始化")

        if group_id:
            contexts = await self._get_msg_contexts(group_id)
        else:  # 随机获取一个群组
            group_list = await self.cfg.client.get_group_list()
            group_ids = [
                str(group["group_id"])
                for group in group_list
                if str(group["group_id"]) not in self.cfg.source.ignore_groups
            ]
            if not group_ids:
                logger.warning("未找到可用群组")
                return None
            group_id = random.choice(group_ids)
            contexts = await self._get_msg_contexts(group_id)
        # TODO: 更多模式

        task_prompt = self._join_prompt_parts(
            f"# 写作主题：{topic or '从聊天内容中选一个主题'}",
            self.cfg.llm.post_prompt,
            "# 输出格式要求：\n"
            '- 使用三对双引号（"""）将正文内容包裹起来。\n'
            "- 只输出最终可发布的说说正文，不要附带解释、标题或额外说明。",
        )
        system_prompt, contexts = await self._build_request_context(
            event=event,
            task_prompt=task_prompt,
            contexts=contexts,
        )

        logger.debug(f"{system_prompt}\n\n{contexts}")

        try:
            llm_response = await provider.text_chat(
                system_prompt=system_prompt,
                contexts=contexts,
            )
            diary = self.extract_content(llm_response.completion_text)
            if not diary:
                raise ValueError("LLM 生成的日记为空")
            logger.info(f"LLM 生成的日记：{diary}")
            return diary

        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")

    async def generate_comment(
        self, post: Post, *, event: Any | None = None
    ) -> str | None:
        """根据帖子内容生成评论"""
        provider = self._get_provider(self.cfg.llm.comment_provider_id, event)
        if not isinstance(provider, Provider):
            logger.error("未配置用于文本生成任务的 LLM 提供商")
            return None
        try:
            content = post.text
            if post.rt_con:  # 转发文本
                content += f"\n[转发]\n{post.rt_con}"

            prompt = f"\n[帖子内容]：\n{content}"
            system_prompt, contexts = await self._build_request_context(
                event=event,
                task_prompt=self._join_prompt_parts(
                    self.cfg.llm.comment_prompt,
                    "# 输出要求：\n- 只输出最终评论内容，不要解释，不要分点，不要添加额外前缀。",
                ),
            )

            logger.debug(prompt)
            # 先尝试带图片调用，若 LLM 不支持 vision 则降级为纯文本
            try:
                llm_response = await provider.text_chat(
                    system_prompt=system_prompt,
                    prompt=prompt,
                    contexts=contexts,
                    image_urls=post.images if post.images else None,
                )
            except Exception as img_err:
                err_msg = str(img_err).lower()
                if "image_url" in err_msg or "image" in err_msg:
                    logger.warning(
                        f"当前 LLM 不支持 vision，降级为纯文本评论: {img_err}"
                    )
                    llm_response = await provider.text_chat(
                        system_prompt=system_prompt,
                        prompt=prompt,
                        contexts=contexts,
                    )
                else:
                    raise
            comment = re.sub(r"[\s\u3000]+", "", llm_response.completion_text).rstrip(
                "。"
            )
            logger.info(f"LLM 生成的评论：{comment}")
            return comment

        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")

    async def generate_reply(
        self,
        post: Post,
        comment: Comment,
        *,
        event: Any | None = None,
    ) -> str | None:
        """根据评论内容生成回复"""
        provider = self._get_provider(self.cfg.llm.reply_provider_id, event)
        if not isinstance(provider, Provider):
            logger.error("未配置用于文本生成任务的 LLM 提供商")
            return None
        try:
            content = post.text
            if post.rt_con:  # 转发文本
                content += f"\n[转发]\n{post.rt_con}"

            prompt = f"\n## 帖子内容\n{content}"
            prompt += f"\n## 要回复的评论\n{comment.nickname}：{comment.content}"
            system_prompt, contexts = await self._build_request_context(
                event=event,
                task_prompt=self._join_prompt_parts(
                    self.cfg.llm.reply_prompt,
                    "# 输出要求：\n- 只输出最终回复内容，不要解释，不要分点，不要添加额外前缀。",
                ),
            )
            logger.debug(prompt)
            llm_response = await provider.text_chat(
                system_prompt=system_prompt,
                prompt=prompt,
                contexts=contexts,
            )
            reply = re.sub(r"[\s\u3000]+", "", llm_response.completion_text).rstrip(
                "。"
            )
            logger.info(f"LLM 生成的回复：{reply}")
            return reply

        except Exception as e:
            raise ValueError(f"LLM 调用失败：{e}")
