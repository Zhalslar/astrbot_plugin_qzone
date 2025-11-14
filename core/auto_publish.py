import zoneinfo

from aiocqhttp import CQHttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from astrbot.api import logger
from astrbot.core.star.context import Context

from .llm_action import LLMAction
from .qzone_api import Qzone


class AutoPublish:
    """
    自动发说说任务类
    """

    def __init__(
        self,
        context: Context,
        config,
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
        cron_cfg = config.get("pulish_cron", "45 1 * * *")
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

        diary_text = await self.llm.generate_diary(client=self.client)
        await self.qzone.publish_emotion(text=diary_text)

    async def terminate(self):
        self.scheduler.remove_all_jobs()
        logger.info("[AutoPublish] 已停止")
