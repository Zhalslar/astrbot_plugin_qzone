import asyncio
import zoneinfo

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
        llm: LLMAction,
    ):
        self.qzone = qzone
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


    async def run_once(self):
        """执行一次完整的遍历 + 点赞 + 评论"""
        logger.info("[AutoComment] 开始自动遍历好友说说...")
        succ, data = await self.qzone.get_recent_feeds()
        if succ:
            logger.error(f"获取说说失败：{data}")
            return
        posts: list[Post] = data # type: ignore
        posts = [post for post in posts if post.uin != self.qzone.ctx.uin]
        for post in posts:
            try:
                await self.like_post(post)
                await self.comment_post(post)
            except Exception as e:
                logger.error(f"[AutoComment] 处理稿件 {post.tid} 失败：{e}")
                continue
            await asyncio.sleep(2)

        logger.info("[AutoComment] 本轮任务结束")


    async def like_post(self, post: Post):
        try:
            succ, data = await self.qzone.like(fid=post.tid, target_id=str(post.uin))
            if not succ:
                logger.error(f"[AutoComment] 点赞失败: {data}")
                return
            logger.info(f"[AutoComment] 已点赞: {post.name}({post.uin})/{post.tid} ")
        except Exception as e:
            logger.error(f"[AutoComment] 点赞异常: {e}")


    async def comment_post(self, post: Post):
        try:
            content = await self.llm.generate_comment(post)

            succ, data = await self.qzone.comment(
                fid=post.tid,
                target_id=str(post.uin),
                content=content,
            )
            if not succ:
                logger.error(f"[AutoComment] 评论失败: {data}")
                return
            logger.info(
                f"[AutoComment] 已评论: {post.name}({post.uin})/{post.tid} -> {content}"
            )

        except Exception as e:
            logger.error(f"[AutoComment] 评论异常: {e}")

    async def terminate(self):
        self.scheduler.remove_all_jobs()
        logger.info("[AutoComment] 已停止")
