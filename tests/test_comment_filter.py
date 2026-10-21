"""core.qzone.comment_filter 的离线测试（不依赖 AstrBot / pydantic / 网络）。

运行：python -m unittest discover -s tests
"""

import importlib.util
import unittest
from collections import namedtuple
from pathlib import Path

# 插件包 __init__ 会连带 import astrbot，这里直接按文件加载被测模块，保证离线可测
_MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "qzone" / "comment_filter.py"
)
_spec = importlib.util.spec_from_file_location("comment_filter", _MODULE_PATH)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

comment_key = _module.comment_key
pick_pending_comments = _module.pick_pending_comments

C = namedtuple("C", "uin tid parent_tid create_time")

SELF = 10000
NOW = 1_700_000_000
HOUR = 3600


class PickPendingCommentsTest(unittest.TestCase):
    def pick(self, comments, *, replied=(), window=24 * HOUR):
        return pick_pending_comments(
            comments,
            self_uin=SELF,
            now=NOW,
            window_seconds=window,
            replied_keys=replied,
        )

    def test_skip_self_comments(self):
        comments = [C(SELF, 1, None, NOW - 100), C(20001, 2, None, NOW - 100)]
        self.assertEqual([c.uin for c in self.pick(comments)], [20001])

    def test_skip_comments_without_uin(self):
        comments = [C(0, 1, None, NOW - 100), C(20001, 2, None, NOW - 100)]
        self.assertEqual([c.uin for c in self.pick(comments)], [20001])

    def test_new_comment_inside_window_is_pending(self):
        comment = C(20001, 1, None, NOW - 600)
        self.assertEqual([c.tid for c in self.pick([comment])], [1])

    def test_comment_outside_window_is_skipped(self):
        old = C(20001, 1, None, NOW - 48 * HOUR)
        self.assertEqual(self.pick([old], window=24 * HOUR), [])

    def test_comment_without_time_is_skipped(self):
        untimed = C(20001, 1, None, 0)
        self.assertEqual(self.pick([untimed]), [])

    def test_zero_window_keeps_everything(self):
        old = C(20002, 2, None, NOW - 48 * HOUR)
        untimed = C(20003, 3, None, 0)
        picked = self.pick([old, untimed], window=0)
        # 排序按 create_time 升序，拿不到时间的评论（0）排在最前面
        self.assertEqual([c.uin for c in picked], [20003, 20002])

    def test_main_comment_already_replied_is_skipped(self):
        """机器人回复主评论后，新评论的 parent_tid 就是主评论 tid。"""
        main = C(20001, 1, None, NOW - 100)
        my_reply = C(SELF, 2, 1, NOW - 50)
        self.assertEqual(self.pick([main, my_reply]), [])

    def test_local_snapshot_replied_keys_are_skipped(self):
        """服务端评论树没带回机器人的回复时，靠本地快照兜底。"""
        main = C(20001, 1, None, NOW - 100)
        self.assertEqual(self.pick([main], replied=["1"]), [])

    def test_sub_comment_under_replied_main_is_skipped(self):
        """楼中楼回复挂在主评论下，所以主评论已有我的回复就整枝跳过。"""
        main = C(20001, 1, None, NOW - 200)
        sub = C(20002, 1, 1, NOW - 100)
        my_reply = C(SELF, 2, 1, NOW - 50)
        self.assertEqual(self.pick([main, sub, my_reply]), [])

    def test_sub_comment_without_reply_is_pending(self):
        main = C(20001, 1, None, NOW - 200)
        sub = C(20002, 1, 1, NOW - 100)
        picked = self.pick([main, sub])
        self.assertEqual([(c.uin, c.tid, c.parent_tid) for c in picked], [(20001, 1, None), (20002, 1, 1)])

    def test_sorted_by_create_time(self):
        comments = [
            C(20003, 3, None, NOW - 10),
            C(20001, 1, None, NOW - 100),
            C(20002, 2, None, NOW - 50),
        ]
        self.assertEqual([c.uin for c in self.pick(comments)], [20001, 20002, 20003])

    def test_comment_key_separates_recycled_tid(self):
        """tid 是楼层号，评论被删后会被复用；create_time 不同就是另一条评论。"""
        replied = C(20001, 2, None, NOW - 100)
        recycled = C(20002, 2, None, NOW - 10)
        self.assertNotEqual(comment_key(replied), comment_key(recycled))


if __name__ == "__main__":
    unittest.main()
