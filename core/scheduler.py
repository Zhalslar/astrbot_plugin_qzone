import random
import zoneinfo
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from astrbot.api import logger

from .config import PluginConfig
from .sender import Sender
from .service import PostService
from .qzone import QzoneParser


class AutoRandomCronTask:
    """
    Schedule one task per cron cycle around the cron anchor time.
    Subclasses only need to implement async do_task().
    """

    def __init__(
        self,
        job_name: str,
        cron_expr: str,
        timezone: zoneinfo.ZoneInfo,
        offset_seconds: int,
    ):
        self.timezone = timezone
        self.scheduler = AsyncIOScheduler(timezone=self.timezone)
        self.scheduler.start()

        self.cron_expr = cron_expr
        self.job_name = job_name
        self.offset_seconds = offset_seconds
        self._last_base_time: datetime | None = None
        self._terminated = False

        self._register_task()

        logger.info(
            f"[{self.job_name}] 已启动，任务周期：{self.cron_expr}，偏移范围：±{self.offset_seconds} 分钟"
        )

    def _register_task(self):
        try:
            self.trigger = CronTrigger.from_crontab(
                self.cron_expr, timezone=self.timezone
            )
            self._schedule_next_job()
        except Exception as e:
            logger.error(f"[{self.job_name}] Cron 格式错误：{e}")

    def _schedule_next_job(self):
        if self._terminated:
            logger.debug(f"[{self.job_name}] 调度器已终止，跳过后续调度")
            return
        if not hasattr(self, "trigger"):
            return

        now = datetime.now(self.timezone)
        base_time = self.trigger.get_next_fire_time(self._last_base_time, now)
        if not base_time:
            logger.error(f"[{self.job_name}] 无法计算下一次基准时间")
            return

        self._last_base_time = base_time

        delay_seconds = (
            random.randint(-self.offset_seconds, self.offset_seconds)
            if self.offset_seconds
            else 0
        )
        target_time = base_time + timedelta(seconds=delay_seconds)

        if target_time <= now:
            target_time = now + timedelta(seconds=1)
            logger.warning(
                f"[{self.job_name}] 偏移后时间已过，改为立即补偿执行：{target_time}"
            )

        logger.info(
            f"[{self.job_name}] 基准时间：{base_time}，偏移：{delay_seconds} 秒，执行时间：{target_time}"
        )

        try:
            self.scheduler.add_job(
                func=self._run_task_wrapper,
                trigger=DateTrigger(run_date=target_time, timezone=self.timezone),
                name=f"{self.job_name}_once_{int(base_time.timestamp())}",
                max_instances=1,
            )
        except Exception as e:
            if self._terminated:
                logger.debug(
                    f"[{self.job_name}] 调度器终止后跳过 add_job：{type(e).__name__}: {e}"
                )
                return
            logger.error(f"[{self.job_name}] 添加调度任务失败：{e}")

    async def _run_task_wrapper(self):
        logger.info(f"[{self.job_name}] 开始执行任务")
        try:
            await self.do_task()
        except Exception as e:
            logger.exception(f"[{self.job_name}] 任务执行失败: {e}")
        finally:
            if not self._terminated:
                self._schedule_next_job()
            logger.info(f"[{self.job_name}] 本轮任务完成")

    async def do_task(self):
        raise NotImplementedError

    async def terminate(self):
        if self._terminated:
            return
        self._terminated = True
        self.scheduler.remove_all_jobs()
        try:
            self.scheduler.shutdown(wait=False)
        except Exception as e:
            logger.debug(f"[{self.job_name}] 关闭调度器时忽略异常：{e}")
        logger.info(f"[{self.job_name}] 已停止")


class AutoComment(AutoRandomCronTask):
    def __init__(
        self,
        config: PluginConfig,
        service: PostService,
        sender: Sender,
    ):
        cron = config.trigger.comment_cron
        timezone = config.timezone
        offset = config.trigger.comment_offset
        super().__init__("AutoComment", cron, timezone, offset)
        self.cfg = config
        self.service = service
        self.sender = sender

    async def do_task(self):
        posts = await self.service.query_feeds(
            pos=0,
            num=20,
            no_self=True,
            no_commented=True,
        )
        for post in posts:
            try:
                # 先让LLM判断是否值得评论
                if not await self.service.llm.should_comment(post):
                    logger.info(f"[{self.job_name}] 跳过说说（不值得评论）: tid={post.tid}, name={post.name}")
                    continue
                await self.service.comment_posts(post)
                if self.cfg.trigger.like_when_comment:
                    await self.service.like_posts(post)
                await self.sender.send_admin_post(post, message="定时读说说")
            except Exception as e:
                logger.exception(
                    f"[{self.job_name}] 跳过说说评论失败: tid={post.tid}, uin={post.uin}, name={post.name}, error={e}"
                )


class AutoPublish(AutoRandomCronTask):
    def __init__(
        self,
        config: PluginConfig,
        service: PostService,
        sender: Sender,
    ):
        cron = config.trigger.publish_cron
        timezone = config.timezone
        offset = config.trigger.publish_offset
        super().__init__("AutoPublish", cron, timezone, offset)
        self.service = service
        self.sender = sender

    async def do_task(self):
        try:
            text = await self.service.llm.generate_post()
        except Exception as e:
            logger.error(f"自动生成内容失败：{e}")
            return
        post = await self.service.publish_post(text=text)
        await self.sender.send_admin_post(post, message="定时发说说")


class AutoReply(AutoRandomCronTask):
    def __init__(
        self,
        config: PluginConfig,
        service: PostService,
        sender: Sender,
    ):
        cron = config.trigger.reply_cron
        timezone = config.timezone
        offset = config.trigger.reply_offset
        super().__init__("AutoReply", cron, timezone, offset)
        self.cfg = config
        self.service = service
        self.sender = sender

    async def do_task(self):
        """
        定时检查自己说说的评论，自动回复未回复的评论
        """
        # 获取自己的 uin
        uin = await self.service.session.get_uin()

        # 查询自己最近的说说
        posts = await self.service.query_feeds(
            target_id=str(uin),
            pos=0,
            num=10,
            with_detail=True,
        )

        replied_count = 0
        for post in posts:
            try:
                count = await self._reply_new_comments(post, uin)
                replied_count += count
            except Exception as e:
                logger.exception(
                    f"[{self.job_name}] 自动回评失败: tid={post.tid}, error={e}"
                )

        if replied_count > 0:
            logger.info(f"[{self.job_name}] 本轮共回复了 {replied_count} 条评论")

    async def _reply_new_comments(self, post, self_uin: int) -> int:
        """
        对一条说说中所有未回复的非己评论进行回复
        返回本条说说中实际回复的评论数
        """
        # 从数据库中加载已保存的说说（含历史评论记录）
        saved_post = await self.service.db.get(post.tid, key="tid")

        # 收集已经回复过的评论 tid 集合
        replied_tids: set[int] = set()
        if saved_post:
            for c in saved_post.comments:
                if c.uin == self_uin and c.parent_tid:
                    replied_tids.add(c.parent_tid)
                elif c.uin == self_uin and c.tid:
                    # 主评论被回复过的情况
                    replied_tids.add(c.tid)

        # 找出所有非自己的评论中未被回复的
        new_comments = [
            c for c in post.comments
            if c.uin != self_uin and c.tid not in replied_tids
        ]

        count = 0
        for comment in new_comments:
            try:
                await self.service.reply_comment_obj(post, comment)
                # 更新 replied_tids 避免重复回复
                replied_tids.add(comment.tid)
                count += 1

                # 通知管理员
                await self.sender.send_admin_post(
                    post,
                    message=f"自动回复了 {comment.nickname} 的评论",
                )

                # 回复间隔，避免太快被风控
                import asyncio
                await asyncio.sleep(3)

            except Exception as e:
                logger.warning(
                    f"[{self.job_name}] 回复评论失败: "
                    f"comment_tid={comment.tid}, {comment.nickname}: {e}"
                )

        return count


class AutoLike(AutoRandomCronTask):
    def __init__(
        self,
        config: PluginConfig,
        service: PostService,
        sender: Sender,
    ):
        cron = config.trigger.like_cron
        timezone = config.timezone
        offset = config.trigger.like_offset
        super().__init__("AutoLike", cron, timezone, offset)
        self.cfg = config
        self.service = service
        self.sender = sender

    def _get_liked_file(self):
        return self.cfg.data_dir / "liked_tids.json"

    def _load_liked(self) -> set[str]:
        f = self._get_liked_file()
        if f.exists():
            import json
            try:
                data = json.loads(f.read_text())
                return set(data)
            except Exception:
                return set()
        return set()

    def _save_liked(self, tids: set[str]):
        import json
        f = self._get_liked_file()
        f.write_text(json.dumps(list(tids), ensure_ascii=False))

    async def do_task(self):
        """
        定时浏览好友动态，自动点赞新的说说
        """
        liked_tids = self._load_liked()
        self_uin = await self.service.session.get_uin()

        # 获取好友动态
        try:
            resp = await self.service.qzone.get_recent_feeds()
            if not resp.ok:
                logger.warning(f"[{self.job_name}] 获取动态失败: {resp.message}")
                return

            posts = QzoneParser.parse_recent_feeds(resp.data)
        except Exception as e:
            logger.exception(f"[{self.job_name}] 获取动态异常: {e}")
            return

        liked_count = 0
        for post in posts:
            # 跳过自己的说说（不给自己点赞）
            if post.uin == self_uin:
                continue

            # 跳过已经点赞过的
            tid_key = f"{post.uin}_{post.tid}"
            if tid_key in liked_tids:
                continue

            try:
                await self.service.like_posts(post)
                liked_tids.add(tid_key)
                liked_count += 1
                logger.info(f"[{self.job_name}] 已点赞 {post.name} 的说说")

                # 间隔，避免太快被风控
                import asyncio
                await asyncio.sleep(3)
            except Exception as e:
                logger.warning(
                    f"[{self.job_name}] 点赞失败: "
                    f"tid={post.tid}, name={post.name}, error={e}"
                )

        if liked_count > 0:
            self._save_liked(liked_tids)
            logger.info(f"[{self.job_name}] 本轮共点赞了 {liked_count} 条说说")
            # 通知管理员
            if self.cfg.client:
                try:
                    msg_chain = [{"type": "text", "data": {"text": f"自动点赞了 {liked_count} 条好友说说"}}]
                    for admin_id in self.cfg.admins_id:
                        if admin_id.isdigit():
                            await self.cfg.client.send_private_msg(
                                user_id=int(admin_id), message=msg_chain
                            )
                except Exception as e:
                    logger.error(f"[{self.job_name}] 通知管理员失败: {e}")


# 需要导入 QzoneParser（在文件顶部已有 service 的导入，这里补充 parser）
