
import zoneinfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.context import Context

from .llm_action import LLMAction
from .operate import PostOperator


class AutoComment:
    """
    自动评论器：自动遍历好友说说并评论(顺便点赞)
    """

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig,
        operator: PostOperator,
        llm: LLMAction,
    ):
        self.operator = operator
        self.llm = llm

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
        await self.operator.read_feed(get_recent=True)
        logger.info("[AutoComment] 本轮任务结束")

    async def terminate(self):
        self.scheduler.remove_all_jobs()
        logger.info("[AutoComment] 已停止")




class AutoPublish:
    """
    自动发说说任务类
    """

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig,
        operator: PostOperator,
        llm: LLMAction,
    ):
        self.operator = operator
        self.llm = llm

        self.per_qzone_num = config.get("per_qzone_num", 5)

        tz = context.get_config().get("timezone")
        self.timezone = (
            zoneinfo.ZoneInfo(tz) if tz else zoneinfo.ZoneInfo("Asia/Shanghai")
        )

        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.scheduler.start()
        cron_cfg = config.get("publish_cron", "45 1 * * *")
        self.register_task(cron_cfg)

        logger.info(f"[AutoPublish] 已启动，任务周期：{cron_cfg}")

    def register_task(self, cron_expr: str):
        """
        注册一个 cron 任务，例如 "45 1 * * *"
        """
        try:
            trigger = CronTrigger.from_crontab(cron_expr)
            self.scheduler.add_job(
                func=self.run_once,
                trigger=trigger,
                name="qzone_auto_publish",
                max_instances=1,
            )
        except Exception as e:
            logger.error(f"[AutoPublish] Cron 格式错误：{e}")

    async def run_once(self):
        """
        计划任务执行一次自动发说说
        """
        logger.info("[AutoPublish] 执行自动发说说任务")
        text = await self.llm.generate_diary()
        await self.operator.publish_feed(text=text)
        logger.info("[AutoPublish] 发说说完成")

    async def terminate(self):
        self.scheduler.remove_all_jobs()
        logger.info("[AutoPublish] 已停止")
