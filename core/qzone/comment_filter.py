"""自己说说下「待回复评论」的筛选逻辑。

抽成纯函数是为了能脱离 AstrBot / pydantic 做离线测试：
这里只依赖对象上的 uin / tid / parent_tid / create_time 属性。

判据只有两条，故意不依赖"库里是否见过这条评论"：

* 时间窗口：只处理最近 N 小时内的评论，避免插件启用时把历史评论一次刷完；
* 是否已经回复：QQ 空间是两级评论，机器人回复主评论时挂在该主评论下，
  所以"我的评论的 parent_tid"就是已经回复过的那条评论，据此跳过。

只看"见过没见过"会把"早就在库里、但机器人从没回复过"的评论永久漏掉，
这正是用户反馈"我评论它它不回"的场景。
"""

from collections.abc import Iterable
from typing import Protocol


class CommentLike(Protocol):
    uin: int
    tid: int
    parent_tid: int | str | None
    create_time: int


def comment_key(comment: CommentLike) -> tuple[int, int, str | None, int]:
    """评论指纹。

    楼中楼的 tid 是楼内序号，同一说说里会和主评论的 tid 重号，
    所以必须带上父级 tid；而 tid 本质是楼层号，评论被删除后会被新评论复用，
    因此再带上 create_time，避免"楼层号撞车"把两条评论认成同一条。
    """
    parent = comment.parent_tid
    return (
        int(comment.uin or 0),
        int(comment.tid or 0),
        None if parent in (None, "") else str(parent),
        int(comment.create_time or 0),
    )


def _replied_parent_keys(
    comments: Iterable[CommentLike],
    self_uin: int,
) -> set[str]:
    """已经由机器人回复过的评论 tid 集合。

    机器人回复某条评论时，新评论的 parent_tid 就是被回复的那条评论的 tid
    （主评论回复挂主评论，楼中楼回复也挂在主评论下）。
    """
    keys: set[str] = set()
    for comment in comments:
        if int(comment.uin or 0) != int(self_uin):
            continue
        parent = comment.parent_tid
        if parent not in (None, ""):
            keys.add(str(parent))
    return keys


def pick_pending_comments(
    comments: Iterable[CommentLike],
    *,
    self_uin: int,
    now: int,
    window_seconds: int,
    replied_keys: Iterable[str] = (),
) -> list[CommentLike]:
    """挑出需要机器人回复的评论。

    Args:
        comments: 服务端返回的最新评论树（主评论与楼中楼都算）。
        self_uin: 机器人自己的 QQ 号，自己的评论永远不回复。
        now: 当前时间戳。
        window_seconds: 只考虑最近多少秒内的评论；0 表示不限制时间。
        replied_keys: 额外已知的"已回复评论 tid"（来自本地快照，防止服务端
            评论树没带回机器人的回复时重复回复）。

    Returns:
        按时间升序排列的待回复评论。
    """
    comments = list(comments)
    replied = set(replied_keys) | _replied_parent_keys(comments, self_uin)

    pending: list[CommentLike] = []
    for comment in comments:
        if not comment.uin or int(comment.uin) == int(self_uin):
            continue

        if window_seconds > 0:
            create_time = int(comment.create_time or 0)
            if not create_time or now - create_time > window_seconds:
                # 拿不到时间或时间太旧的评论当作历史评论，不回复
                continue

        # 主评论看自己是否已在其下回复；楼中楼看其父评论下是否已有自己的回复
        target_tid = comment.tid if comment.parent_tid in (None, "") else comment.parent_tid
        if str(target_tid) in replied:
            continue

        pending.append(comment)

    pending.sort(key=lambda c: int(c.create_time or 0))
    return pending
