import base64
import random

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core import AstrBotConfig
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)

from .core.campus_wall import CampusWall
from .core.config import PluginConfig
from .core.db import PostDB
from .core.llm_action import LLMAction
from .core.model import Comment, Post
from .core.qzone import QzoneAPI, QzoneParser, QzoneSession
from .core.scheduler import AutoComment, AutoPublish
from .core.sender import Sender
from .core.service import PostService
from .core.utils import get_ats, get_image_urls, parse_range

try:
    from quart import jsonify as _quart_jsonify
    from quart import request as _quart_request
except Exception:
    _quart_jsonify = None
    _quart_request = None


class QzonePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        # 配置
        self.cfg = PluginConfig(config, context)
        # 会话
        self.session = QzoneSession(self.cfg)
        # QQ空间
        self.qzone = QzoneAPI(self.session, self.cfg)
        # 数据库
        self.db = PostDB(self.cfg)
        # LLM模块
        self.llm = LLMAction(self.cfg)
        # 消息发送器
        self.sender = Sender(self.cfg)
        # 操作服务
        self.service = PostService(self.qzone, self.session, self.db, self.llm)
        # 表白墙
        self.campus_wall = CampusWall(self.cfg, self.service, self.db, self.sender)
        # 自动评论模块
        self.auto_comment: AutoComment | None = None
        # 自动发说说模块
        self.auto_publish: AutoPublish | None = None
        self._page_post_refs: dict[str, Post] = {}
        self._register_page_web_apis()

    async def initialize(self):
        """插件加载时触发"""
        await self.db.initialize()

        if not self.auto_comment and self.cfg.trigger.comment_cron:
            self.auto_comment = AutoComment(self.cfg, self.service, self.sender)

        if not self.auto_publish and self.cfg.trigger.publish_cron:
            self.auto_publish = AutoPublish(self.cfg, self.service, self.sender)

    def _register_page_web_apis(self) -> None:
        routes = (
            ("page/status", self.page_status, ["GET"], "Qzone dashboard status"),
            ("page/feed", self.page_feed, ["GET"], "Qzone dashboard feed"),
            ("page/detail", self.page_detail, ["GET"], "Qzone dashboard detail"),
            ("page/publish", self.page_publish, ["POST"], "Qzone dashboard publish"),
            ("page/like", self.page_like, ["POST"], "Qzone dashboard like"),
            ("page/comment", self.page_comment, ["POST"], "Qzone dashboard comment"),
            ("page/reply", self.page_reply, ["POST"], "Qzone dashboard reply"),
            ("page/delete", self.page_delete, ["POST"], "Qzone dashboard delete"),
            (
                "page/upload-media",
                self.page_upload_media,
                ["POST"],
                "Qzone dashboard upload media",
            ),
        )
        for endpoint, handler, methods, desc in routes:
            self.context.register_web_api(
                f"/astrbot_plugin_qzone/{endpoint}",
                handler,
                methods,
                desc,
            )

    async def _page_response(self, payload: dict, status: int = 200):
        if _quart_jsonify is None:
            return payload
        response = _quart_jsonify(payload)
        response.status_code = status
        return response

    async def _page_json(self, callback):
        try:
            payload = await callback()
            status = 200
        except Exception as exc:
            logger.exception("qzone page api failed: %s", exc)
            payload = {
                "ok": False,
                "error": {
                    "message": str(exc) or "请求失败",
                },
            }
            status = 400
        return await self._page_response(payload, status)

    async def _page_query_params(self) -> dict:
        if _quart_request is None:
            return {}
        args = getattr(_quart_request, "args", {}) or {}
        try:
            return {str(key): value for key, value in args.items()}
        except Exception:
            return dict(args)

    async def _page_json_body(self) -> dict:
        if _quart_request is None:
            return {}
        try:
            data = await _quart_request.get_json(silent=True)
        except TypeError:
            data = await _quart_request.get_json()
        return data if isinstance(data, dict) else {}

    async def _build_page_status(self) -> dict:
        self._capture_page_client()
        try:
            uin = await self.session.get_uin()
            nickname = await self.session.get_nickname()
            bound = True
        except Exception:
            uin = 0
            nickname = ""
            bound = False
        return {
            "ok": True,
            "data": {
                "daemon": {"state": "ready" if bound else "offline"},
                "login": {
                    "bound": bound,
                    "uin": uin,
                    "nickname": nickname,
                    "avatar": "",
                },
                "limits": {
                    "feed": 10,
                    "images": 9,
                },
            },
        }

    def _remember_page_post(self, post: Post) -> str:
        post_id = f"{post.uin}:{post.tid}"
        self._page_post_refs[post_id] = post
        return post_id

    def _capture_page_client(self):
        if self.cfg.client is not None:
            return self.cfg.client
        try:
            platform = self.context.get_platform("aiocqhttp")
        except Exception:
            platform = None
        if platform is None:
            try:
                for candidate in self.context.platform_manager.platform_insts:
                    meta = candidate.meta()
                    if getattr(meta, "name", "") == "aiocqhttp":
                        platform = candidate
                        break
            except Exception:
                platform = None
        bot = getattr(platform, "bot", None) if platform is not None else None
        if bot is not None:
            self.cfg.client = bot
        return self.cfg.client

    def _page_post_payload(
        self,
        post: Post,
        *,
        include_comments: bool = False,
        self_uin: int = 0,
    ) -> dict:
        images = list(dict.fromkeys([*(post.images or []), *(post.videos or [])]))
        payload = {
            "id": self._remember_page_post(post),
            "author": {
                "uin": post.uin,
                "nickname": post.name or str(post.uin),
                "avatar": post.avatar_url or "",
            },
            "content": post.text or post.rt_con or "",
            "created_at": int(post.create_time or 0),
            "stats": {
                "likes": 0,
                "comments": len(post.comments or []),
            },
            "liked": False,
            "images": images,
            "can_delete": bool(self_uin and int(post.uin) == int(self_uin)),
        }
        if include_comments:
            payload["comments"] = [
                {
                    "id": str(comment.tid or index),
                    "author": {
                        "uin": comment.uin,
                        "nickname": comment.nickname or str(comment.uin),
                    },
                    "content": comment.content,
                    "created_at": int(comment.create_time or 0),
                    "can_reply": bool(comment.tid and comment.uin),
                }
                for index, comment in enumerate(post.comments or [], start=1)
            ]
        return payload

    def _require_page_post(self, post_id: str) -> Post:
        post = self._page_post_refs.get(str(post_id or "").strip())
        if not post:
            raise RuntimeError("说说引用已失效，请刷新页面后重试")
        return post

    async def page_status(self):
        return await self._page_json(self._build_page_status)

    async def page_feed(self):
        async def handler():
            self._capture_page_client()
            params = await self._page_query_params()
            scope = str(params.get("scope") or "friends")
            self_uin = await self.session.get_uin()
            target_id = None
            no_self = False
            if scope == "self":
                target_id = str(self_uin)
            elif scope == "profile":
                target_id = str(params.get("hostuin") or "").strip() or None
            else:
                no_self = False
            posts = await self.service.query_feeds(
                target_id=target_id,
                pos=0,
                num=min(max(int(params.get("limit") or 10), 1), 10),
                with_detail=False,
                no_self=no_self,
            )
            return {
                "ok": True,
                "data": {
                    "items": [
                        self._page_post_payload(post, self_uin=self_uin)
                        for post in posts
                    ],
                    "cursor": "",
                    "has_more": False,
                },
            }

        return await self._page_json(handler)

    async def page_detail(self):
        async def handler():
            self._capture_page_client()
            params = await self._page_query_params()
            post = self._require_page_post(params.get("id", ""))
            resp = await self.qzone.get_detail(post)
            if not resp.ok or not resp.data:
                raise RuntimeError(resp.message or "获取详情失败")
            parsed = QzoneParser.parse_feeds([resp.data])
            matched = parsed[0] if parsed else post
            self._remember_page_post(matched)
            return {
                "ok": True,
                "data": {
                    "post": self._page_post_payload(
                        matched,
                        include_comments=True,
                        self_uin=await self.session.get_uin(),
                    )
                },
            }

        return await self._page_json(handler)

    async def page_publish(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            media = body.get("media") or []
            images = []
            for item in media:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source") or "")
                if source.startswith("base64://"):
                    images.append(base64.b64decode(source.removeprefix("base64://")))
            post = await self.service.publish_post(
                text=str(body.get("content") or ""),
                images=images,
            )
            return {
                "ok": True,
                "data": {"post": self._page_post_payload(post)},
                "message": "说说已发布。",
            }

        return await self._page_json(handler)

    async def page_like(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            post = self._require_page_post(body.get("id", ""))
            resp = await self.qzone.like(post)
            if not resp.ok:
                raise RuntimeError(resp.message or "点赞失败")
            return {"ok": True, "data": {"liked": True, "verified": True}}

        return await self._page_json(handler)

    async def page_comment(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            post = self._require_page_post(body.get("id", ""))
            content = str(body.get("content") or "").strip()
            if not content:
                raise RuntimeError("评论内容不能为空")
            resp = await self.qzone.comment(post, content)
            if not resp.ok:
                raise RuntimeError(resp.message or "评论失败")
            comment = {
                "id": f"local-{len(post.comments) + 1}",
                "content": content,
                "author": {
                    "uin": await self.session.get_uin(),
                    "nickname": await self.session.get_nickname(),
                    "avatar": "",
                },
            }
            post.comments.append(
                Comment(
                    uin=comment["author"]["uin"],
                    nickname=comment["author"]["nickname"],
                    content=content,
                    create_time=0,
                    tid=0,
                    parent_tid=None,
                )
            )
            return {"ok": True, "data": {"comment": comment}, "message": "评论已发送。"}

        return await self._page_json(handler)

    async def page_reply(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            post = self._require_page_post(body.get("id", ""))
            content = str(body.get("content") or "").strip()
            comment_id = str(body.get("commentid") or "")
            if not content:
                raise RuntimeError("回复内容不能为空")
            target = next(
                (item for item in post.comments if str(item.tid) == comment_id), None
            )
            if target is None:
                raise RuntimeError("未找到要回复的评论")
            resp = await self.qzone.reply(post, target, content)
            if not resp.ok:
                raise RuntimeError(resp.message or "回复失败")
            reply = {
                "id": str(resp.data.get("tid") or ""),
                "content": content,
                "author": {
                    "uin": await self.session.get_uin(),
                    "nickname": await self.session.get_nickname(),
                    "avatar": "",
                },
            }
            post.comments.append(
                Comment(
                    uin=reply["author"]["uin"],
                    nickname=reply["author"]["nickname"],
                    content=content,
                    create_time=0,
                    tid=int(reply["id"]) if reply["id"].isdigit() else 0,
                    parent_tid=target.tid,
                )
            )
            return {"ok": True, "data": {"reply": reply}, "message": "回复已发送。"}

        return await self._page_json(handler)

    async def page_delete(self):
        async def handler():
            self._capture_page_client()
            body = await self._page_json_body()
            post = self._require_page_post(body.get("id", ""))
            self_uin = await self.session.get_uin()
            if int(post.uin) != int(self_uin):
                raise RuntimeError("只能删除自己发布的说说")
            resp = await self.qzone.delete(str(post.tid or ""))
            if not resp.ok:
                raise RuntimeError(resp.message or "删除失败")
            return {"ok": True, "data": {}, "message": "说说已删除。"}

        return await self._page_json(handler)

    async def page_upload_media(self):
        async def handler():
            if _quart_request is None:
                raise RuntimeError("当前环境不支持上传")
            files = await _quart_request.files
            upload = files.get("file") or files.get("image") or files.get("media")
            if upload is None:
                raise RuntimeError("没有收到图片文件")
            data = await upload.read()
            return {
                "ok": True,
                "data": {
                    "media": {
                        "kind": "image",
                        "name": upload.filename or "image.jpg",
                        "source": f"base64://{base64.b64encode(data).decode('ascii')}",
                        "size": len(data),
                        "mime_type": getattr(upload, "content_type", "")
                        or "image/jpeg",
                    }
                },
            }

        return await self._page_json(handler)

    async def terminate(self):
        """插件卸载时"""
        if self.qzone:
            await self.qzone.close()
        if self.auto_comment:
            await self.auto_comment.terminate()
        if self.auto_publish:
            await self.auto_publish.terminate()

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    async def prob_read_feed(self, event: AiocqhttpMessageEvent):
        """监听消息"""
        if not self.cfg.client:
            self.cfg.client = event.bot
            logger.debug("QQ空间所需的 CQHttp 客户端已初始化")

        # 按概率触发点赞+评论
        sender_id = event.get_sender_id()
        if (
            not self.cfg.source.is_ignore_user(sender_id)
            and random.random() < self.cfg.trigger.read_prob
        ):
            target_id = event.get_sender_id()
            posts = await self.service.query_feeds(
                target_id=target_id, pos=0, num=1, no_self=True, no_commented=True
            )
            for post in posts:
                try:
                    await self.service.comment_posts(post, event=event)
                    if self.cfg.trigger.like_when_comment:
                        await self.service.like_posts(post)
                    await self.sender.send_post(
                        event,
                        post,
                        message="触发读说说",
                        send_admin=self.cfg.trigger.send_admin,
                    )
                except Exception as e:
                    logger.error(e)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("查看访客")
    async def view_visitor(self, event: AiocqhttpMessageEvent):
        """查看访客"""
        try:
            msg = await self.service.view_visitor()
            await self.sender.send_msg(event, msg)
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)

    async def _get_posts(
        self,
        event: AiocqhttpMessageEvent,
        *,
        target_id: str | None = None,
        with_detail: bool = False,
        no_commented=False,
        no_self=False,
    ) -> list[Post]:
        pos, num = parse_range(event)
        at_ids = get_ats(event)
        if not target_id:
            target_id = at_ids[0] if at_ids else None

        if target_id:
            self.cfg.remove_ignore_users(target_id)
        try:
            logger.debug(
                f"正在查询说说： {target_id, pos, num, with_detail, no_commented, no_self}"
            )
            posts = await self.service.query_feeds(
                target_id=target_id,
                pos=pos,
                num=num,
                with_detail=with_detail,
                no_commented=no_commented,
                no_self=no_self,
            )
            if not posts:
                await event.send(event.plain_result("查询结果为空"))
                event.stop_event()
            return posts
        except Exception as e:
            await event.send(event.plain_result(str(e)))
            logger.error(e)
            event.stop_event()
            return []

    @filter.command("看说说", alias={"查看说说"})
    async def view_feed(self, event: AiocqhttpMessageEvent):
        """
        看说说 <@群友> <序号>
        """
        posts = await self._get_posts(event, with_detail=True)
        for post in posts:
            await self.sender.send_post(event, post)

    @filter.command("评说说", alias={"评论说说", "读说说"})
    async def comment_feed(self, event: AiocqhttpMessageEvent):
        """评说说 <序号/范围>"""
        posts = await self._get_posts(event, no_commented=True, no_self=True)
        for post in posts:
            try:
                await self.service.comment_posts(post, event=event)
                msg = "已评论"
                if self.cfg.trigger.like_when_comment:
                    await self.service.like_posts(post)
                    msg += "并点赞"
                await self.sender.send_post(event, post, message=msg)
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)

    @filter.command("赞说说")
    async def like_feed(self, event: AiocqhttpMessageEvent):
        """赞说说 <序号/范围>"""
        posts = await self._get_posts(event)
        for post in posts:
            try:
                await self.service.like_posts(post)
                await self.sender.send_post(event, post, message="已点赞")
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("发说说")
    async def publish_feed(self, event: AiocqhttpMessageEvent):
        """发说说 <内容> <图片>, 由用户指定内容"""
        text = event.message_str.partition(" ")[2]
        images = await get_image_urls(event)
        try:
            post = await self.service.publish_post(text=text, images=images)
            await self.sender.send_post(event, post, message="已发布")
            event.stop_event()
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("写说说", alias={"写稿"})
    async def write_feed(self, event: AiocqhttpMessageEvent):
        """写说说 <主题> <图片>, 由AI写完后管理员用‘通过稿件 ID’命令发布"""
        group_id = event.get_group_id()
        topic = event.message_str.partition(" ")[2]
        try:
            text = await self.llm.generate_post(
                group_id=group_id, topic=topic, event=event
            )
        except Exception as e:
            yield event.plain_result(str(e))
            logger.error(e)
            return
        images = await get_image_urls(event)
        if not text and not images:
            yield event.plain_result("说说生成失败")
            return
        self_id = event.get_self_id()
        post = Post(
            uin=int(self_id),
            text=text or "",
            images=images,
            status="pending",
        )
        await self.db.save(post)
        await self.sender.send_post(event, post)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("删说说")
    async def delete_feed(self, event: AiocqhttpMessageEvent):
        """删说说 <稿件ID>"""
        posts = await self._get_posts(event, target_id=event.get_self_id())
        for post in posts:
            try:
                await self.sender.send_post(event, post, message="已删除说说")
                await self.service.delete_post(post)
            except Exception as e:
                await event.send(event.plain_result(str(e)))
                logger.error(e)

    @filter.command("回评", alias={"回复评论"})
    async def reply_comment(
        self, event: AiocqhttpMessageEvent, post_id: int = -1, comment_index: int = -1
    ):
        """回评 <稿件ID> <评论序号>, 默认回复最后一条非己评论"""
        post = await self.db.get(post_id)
        if not post:
            yield event.plain_result(f"稿件#{post_id}不存在")
            return
        try:
            await self.service.reply_comment(post, index=comment_index, event=event)
            await self.sender.send_post(event, post, message="已回复评论")
        except Exception as e:
            await event.send(event.plain_result(str(e)))
            logger.error(e)

    @filter.command("投稿")
    async def contribute_post(self, event: AiocqhttpMessageEvent):
        """投稿 <内容> <图片>"""
        await self.campus_wall.contribute(event)

    @filter.command("匿名投稿")
    async def anon_contribute_post(self, event: AiocqhttpMessageEvent):
        """匿名投稿 <内容> <图片>"""
        await self.campus_wall.contribute(event, anon=True)

    @filter.command("撤稿")
    async def recall_post(self, event: AiocqhttpMessageEvent):
        """删除稿件 <稿件ID>"""
        async for msg in self.campus_wall.delete(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("看稿", alias={"查看稿件"})
    async def view_post(self, event: AiocqhttpMessageEvent):
        "查看稿件 <稿件ID>, 默认最新稿件"
        async for msg in self.campus_wall.view(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("过稿", alias={"通过稿件", "通过投稿"})
    async def approve_post(self, event: AiocqhttpMessageEvent):
        """通过稿件 <稿件ID>"""
        async for msg in self.campus_wall.approve(event):
            yield msg

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("拒稿", alias={"拒绝稿件", "拒绝投稿"})
    async def reject_post(self, event: AiocqhttpMessageEvent):
        """拒绝稿件 <稿件ID> <原因>"""
        async for msg in self.campus_wall.reject(event):
            yield msg

    @filter.llm_tool()
    async def llm_view_feed(
        self,
        event: AiocqhttpMessageEvent,
        user_id: str | None = None,
        pos: int = 0,
        like: bool = False,
        reply: bool = False,
    ):
        """
        查看、点赞、评论某位用户QQ空间的某条说说、动态
        Args:
            user_id(string): 目标用户的QQ账号，必定为一串数字，如(12345678), 默认为当前用户QQ号
            pos(number): 要查询的说说序号, 默认为0表示最新
            like(boolean): 是否点赞
            reply(boolean): 是否评论
        """
        try:
            user_id = user_id or event.get_sender_id()
            logger.debug(f"正在查询用户（{user_id}）的第 {pos} 条说说")

            posts = await self.service.query_feeds(
                target_id=user_id,
                pos=pos,
                num=1,
                with_detail=True,
            )

            if not posts:
                return "查询结果为空"

            post = posts[0]

            # 执行动作
            msg = ""

            if like and reply:
                await self.service.comment_posts(post, event=event)
                await self.service.like_posts(post)
                msg = "已评论并点赞"
            elif reply:
                await self.service.comment_posts(post, event=event)
                msg = "已评论"
            elif like:
                await self.service.like_posts(post)
                msg = "已点赞"

            # 发送展示
            await self.sender.send_post(event, post, message=msg)

            return msg + "\n" + post.text + "\n" + "\n".join(post.images)

        except Exception as e:
            logger.error(e)
            return str(e)

    @filter.llm_tool()
    async def llm_publish_feed(
        self,
        event: AiocqhttpMessageEvent,
        text: str = "",
        get_image: bool = True,
    ):
        """
        写一篇说说并发布到QQ空间
        Args:
            text(string): 要发布的说说内容
            get_image(boolean): 是否获取当前对话中的图片附加到说说里, 默认为True
        """
        images = await get_image_urls(event) if get_image else []
        try:
            post = await self.service.publish_post(text=text, images=images)
            await self.sender.send_post(event, post, message="已发布")
            return "已发布说说到QQ空间: \n" + post.text + "\n" + "\n".join(post.images)
        except Exception as e:
            return str(e)
