import random
import zoneinfo
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from astrbot.api import logger

from .config import PluginConfig
from .service import PostService

# ============================
# 基类：随机偏移的周期任务
# ============================


class AutoRandomCronTask:
    """
    基类：在 cron 规定的周期内随机某个时间点执行任务。
    子类只需实现 async do_task()。
    """

    def __init__(self, job_name: str, cron_expr: str, timezone: zoneinfo.ZoneInfo):
        self.timezone = timezone
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
                func=self.schedule_random_job,
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
    def __init__(self, config: PluginConfig, service: PostService):
        cron = config.trigger.comment_cron
        timezone = config.timezone
        super().__init__("AutoComment", cron, timezone)
        self.cfg = config
        self.service = service

    async def do_task(self):
        posts = await self.service.query_feeds(pos=0, num=20)
        await self.service.comment_posts(posts)
        if self.cfg.trigger.like_when_comment:
            await self.service.like_posts(posts)


# ============================
# 自动发说说
# ============================


class AutoPublish(AutoRandomCronTask):
    def __init__(self, config: PluginConfig, service: PostService):
        cron = config.trigger.publish_cron
        timezone = config.timezone
        super().__init__("AutoPublish", cron, timezone)
        self.service = service

    async def do_task(self):
        try:
            text = await self.service.llm.generate_post()
        except Exception as e:
            logger.error(f"自动生成内容失败：{e}")
            return
        await self.service.publish_post(text=text)
