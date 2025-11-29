import random
import zoneinfo
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.star.context import Context

from .operate import PostOperator

# ============================
# 基类：随机偏移的周期任务
# ============================


class AutoRandomCronTask:
    """
    基类：在 cron 规定的周期内随机某个时间点执行任务。
    子类只需实现 async do_task()。
    """

    def __init__(self, context: Context, cron_expr: str, job_name: str):
        tz = context.get_config().get("timezone")
        self.timezone = (
            zoneinfo.ZoneInfo(tz) if tz else zoneinfo.ZoneInfo("Asia/Shanghai")
        )

        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.scheduler.start()

        self.cron_expr = cron_expr
        self.job_name = job_name

        self.register_task()

        logger.info(f"[{self.job_name}] 已启动，任务周期：{self.cron_expr}")

    # 注册 cron → 触发 schedule_random_job
    def register_task(self):
        try:
            self.trigger = CronTrigger.from_crontab(self.cron_expr)
            self.scheduler.add_job(
                func=self.schedule_random_job(),
                trigger=self.trigger,
                name=f"{self.job_name}_scheduler",
                max_instances=1,
            )
        except Exception as e:
            logger.error(f"[{self.job_name}] Cron 格式错误：{e}")

    # 计算当前周期随机时间点，并安排 DateTrigger 执行
    def schedule_random_job(self):
        now = datetime.now(self.timezone)
        next_run = self.trigger.get_next_fire_time(None, now)
        if not next_run:
            logger.error(f"[{self.job_name}] 无法计算下一次周期时间")
            return

        cycle_seconds = int((next_run - now).total_seconds())
        delay = random.randint(0, cycle_seconds)
        target_time = now + timedelta(seconds=delay)

        logger.info(f"[{self.job_name}] 下周期随机执行时间：{target_time}")

        self.scheduler.add_job(
            func=self._run_task_wrapper,
            trigger=DateTrigger(run_date=target_time, timezone=self.timezone),
            name=f"{self.job_name}_once_{target_time.timestamp()}",
            max_instances=1,
        )

    # 统一包装（方便打印日志）
    async def _run_task_wrapper(self):
        logger.info(f"[{self.job_name}] 开始执行任务")
        await self.do_task()
        logger.info(f"[{self.job_name}] 本轮任务完成")

    # 子类实现
    async def do_task(self):
        raise NotImplementedError

    async def terminate(self):
        self.scheduler.remove_all_jobs()
        logger.info(f"[{self.job_name}] 已停止")


# ============================
# 自动评论
# ============================


class AutoComment(AutoRandomCronTask):
    def __init__(
        self,
        context: Context,
        config: AstrBotConfig,
        operator: PostOperator,
    ):
        self.operator = operator
        super().__init__(context, config["comment_cron"], "AutoComment")

    async def do_task(self):
        await self.operator.read_feed(get_recent=True)


# ============================
# 自动发说说
# ============================


class AutoPublish(AutoRandomCronTask):
    def __init__(
        self,
        context: Context,
        config: AstrBotConfig,
        operator: PostOperator
    ):
        self.operator = operator
        super().__init__(context, config["publish_cron"], "AutoPublish")

    async def do_task(self):
        await self.operator.publish_feed(llm_text=True)
