import asyncio
import zoneinfo

from aiocqhttp import CQHttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.context import Context

from .llm_action import LLMAction
from .post import Post
from .qzone_api import Qzone


class AutoComment:
    """
    自动评论器：自动遍历好友说说并评论(顺便点赞，但实测点赞无效)
    """

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig,
        qzone: Qzone,
        client: CQHttp,
        llm: LLMAction,
    ):
        self.qzone = qzone
        self.client = client
        self.llm = llm

        self.per_qzone_num = config.get("per_qzone_num", 5)

        tz = context.get_config().get("timezone")
        self.timezone = (
            zoneinfo.ZoneInfo(tz) if tz else zoneinfo.ZoneInfo("Asia/Shanghai")
        )

        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.scheduler.start()
        cron_cfg = config.get("comment_cron", "0 8 * * 1")
        self.register_task(cron_cfg)

        logger.info(f"[AutoComment] 已启动，任务周期：{cron_cfg}")


    def register_task(self, cron_expr: str):
        """
        注册一个 cron 任务，例如 "0 8 * * 1"
        """
        try:
            trigger = CronTrigger.from_crontab(cron_expr)
            self.scheduler.add_job(
                func=self.run_once,
                trigger=trigger,
                name="qzone_auto_comment",
                max_instances=1,
            )
        except Exception as e:
            logger.error(f"[AutoComment] Cron 格式错误：{e}")


    async def get_friend_ids(self) -> list[int]:
        """
        通过 aiocqhttp 获取好友 QQ 号列表
        """
        try:
            res = await self.client.get_friend_list()
            return [f["user_id"] for f in res]
        except Exception as e:
            logger.error(f"[AutoComment] 获取好友失败：{e}")
            return []

    async def run_once(self):
        """执行一次完整的遍历 + 点赞 + 评论"""
        logger.info("[AutoComment] 开始自动遍历好友说说...")

        friend_ids = await self.get_friend_ids()
        if not friend_ids:
            logger.warning("[AutoComment] 无好友，跳过")
            return

        for uin in friend_ids:
            try:
                await self.process_friend(uin)
            except Exception as e:
                logger.error(f"[AutoComment] 处理好友 {uin} 失败：{e}")
                await asyncio.sleep(1)

        logger.info("[AutoComment] 本轮任务结束")


    async def process_friend(self, uin: int):
        """
        获取好友最近说说，自动点赞 & 评论
        """
        posts: list[Post] = await self.qzone.get_qzones(
            target_id=str(uin), pos=1, num=self.per_qzone_num
        )
        if not posts:
            return

        for post in posts:
            await self.like_post(post)
            await self.comment_post(post)
            await asyncio.sleep(1)


    async def like_post(self, post: Post):
        try:
            res = await self.qzone.like(fid=post.tid, target_id=str(post.uin))
            if res.get("code") == 0:
                logger.info(f"[AutoComment] 已点赞: {post.uin}/{post.tid}")
            else:
                logger.warning(f"[AutoComment] 点赞失败: {res}")
        except Exception as e:
            logger.error(f"[AutoComment] 点赞异常: {e}")


    async def comment_post(self, post: Post):
        if not self.llm:
            logger.warning("[AutoComment] 未提供 llm，跳过评论")
            return

        try:
            content = await self.llm.generate_comment(post)

            res = await self.qzone.comment(
                fid=post.tid,
                target_id=str(post.uin),
                content=content,
            )
            if res.get("code") == 0:
                logger.info(f"[AutoComment] 已评论: {post.uin}/{post.tid} -> {content}")
            else:
                logger.warning(f"[AutoComment] 评论失败: {res}")
        except Exception as e:
            logger.error(f"[AutoComment] 评论异常: {e}")

    async def terminate(self):
        self.scheduler.remove_all_jobs()
        logger.info("[AutoComment] 已停止")
