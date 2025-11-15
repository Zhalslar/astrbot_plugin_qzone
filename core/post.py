# pots.py

import json
import re
import typing
from datetime import datetime
from pathlib import Path

import aiosqlite
import pydantic

post_key = typing.Literal[
    "id",
    "tid",
    "uin",
    "name",
    "gin",
    "status",
    "anon",
    "text",
    "images",
    "videos",
    "create_time",
    "rt_con",
    "comments",
    "extra_text",
]


def extract_and_replace_nickname(input_string):
    # 匹配{}内的内容，包括非标准JSON格式
    pattern = r"\{[^{}]*\}"

    def replace_func(match):
        content = match.group(0)
        # 按照键值对分割
        pairs = content[1:-1].split(",")
        nick_value = ""
        for pair in pairs:
            key, value = pair.split(":", 1)
            if key.strip() == "nick":
                nick_value = value.strip()
                break
        # 如果找到nick值，则返回@nick_value，否则返回空字符串
        return f"{nick_value} " if nick_value else ""

    return re.sub(pattern, replace_func, input_string)


def remove_em_tags(text):
    """
    移除字符串中的 [em]...[/em] 标记
    :param text: 输入的字符串
    :return: 移除标记后的字符串
    """
    # 使用正则表达式匹配 [em]...[/em] 并替换为空字符串
    cleaned_text = re.sub(r"\[em\].*?\[/em\]", "", text)
    return cleaned_text


class Post(pydantic.BaseModel):
    """稿件"""

    id: int | None = None
    """稿件ID"""
    tid: str = ""
    """QQ给定的说说ID"""
    uin: int = 0
    """用户ID"""
    name: str = ""
    """用户昵称"""
    gin: int = 0
    """群聊ID"""
    text: str = ""
    """文本内容"""
    images: list[str] = []
    """图片列表"""
    videos: list[str] = []
    """视频列表"""
    anon: bool = False
    """是否匿名"""
    status: str = "pending"
    """状态"""
    create_time: int = pydantic.Field(
        default_factory=lambda: int(datetime.now().timestamp())
    )
    """创建时间"""
    rt_con: str = ""
    """转发内容"""
    comments: list[dict] = []
    """评论列表"""
    extra_text: str | None = None
    """额外文本"""

    def to_str(self) -> str:
        """把稿件信息整理成易读文本"""
        is_pending = self.status == "pending"
        lines = [
            f"### {self.name}{'投稿' if is_pending else '发布'}于{datetime.fromtimestamp(self.create_time).strftime('%Y-%m-%d %H:%M')}"
        ]
        if self.text:
            lines.append(f"\n\n{self.text}\n\n")
        if self.images:
            images_str = "\n".join(f"  ![图片]({img})" for img in self.images)
            lines.append(images_str)
        if self.videos:
            videos_str = "\n".join(f"  [视频]({vid})" for vid in self.videos)
            lines.append(videos_str)
        if self.rt_con:
            lines.append(f"  转发：{self.rt_con}")
        if self.comments:
            lines.append("\n\n【评论区】\n")
            for comment in self.comments:
                lines.append(
                    f"- {comment['nickname']}: {remove_em_tags(extract_and_replace_nickname(comment['content']))}"
                )
        if is_pending:
            if self.anon:
                lines.append(f"\n\n备注：稿件#{self.id}待审核, 投稿来自匿名者")
            else:
                lines.append(
                    f"\n\n备注：稿件#{self.id}待审核, 投稿来自{self.name}({self.uin})"
                )
        return "\n".join(lines)

    def update(self, **kwargs):
        """更新 Post 对象的属性"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
            else:
                raise AttributeError(f"Post 对象没有属性 {key}")


class PostManager:
    # 允许查询的列
    ALLOWED_QUERY_KEYS = {
        "id",
        "tid",
        "uin",
        "name",
        "gin",
        "status",
        "anon",
        "text",
        "images",
        "videos",
        "create_time",
        "rt_con",
        "comments",
        "extra_text",
    }

    def __init__(self, db_path: Path):
        self.db_path = db_path

    @staticmethod
    def _row_to_post(row) -> Post:
        return Post(
            id=row[0],
            tid=row[1],
            uin=row[2],
            name=row[3],
            gin=row[4],
            text=row[5],
            images=json.loads(row[6]),
            videos=json.loads(row[7]),
            anon=bool(row[8]),
            status=row[9],
            create_time=row[10],
            rt_con=row[11],
            comments=json.loads(row[12]),
            extra_text=row[13],
        )

    @staticmethod
    def _encode_urls(urls: list[str]) -> str:
        return json.dumps(urls, ensure_ascii=False)

    async def init_db(self):
        """初始化数据库"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tid TEXT NOT NULL DEFAULT '',
                    uin INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    gin INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    images TEXT NOT NULL CHECK(json_valid(images)),
                    videos TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(videos)),
                    anon INTEGER NOT NULL CHECK(anon IN (0,1)),
                    status TEXT NOT NULL,
                    create_time INTEGER NOT NULL,
                    rt_con TEXT NOT NULL DEFAULT '',
                    comments TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(comments)),
                    extra_text TEXT
                )
            """)
            await db.commit()

    async def add(self, post: Post) -> int:
        """添加稿件"""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                INSERT INTO posts (tid, uin, name, gin, text, images, videos, anon, status, create_time, rt_con, comments, extra_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post.tid,
                    post.uin,
                    post.name,
                    post.gin,
                    post.text,
                    self._encode_urls(post.images),
                    self._encode_urls(post.videos),
                    int(post.anon),
                    post.status,
                    post.create_time,
                    post.rt_con,
                    json.dumps(post.comments, ensure_ascii=False),
                    post.extra_text,
                ),
            )
            await db.commit()
            last_id = cur.lastrowid  # 获取自增ID
            assert last_id is not None
            return last_id

    async def get(
        self,
        *,
        key: post_key = "id",
        value,
    ) -> Post | None:
        """根据指定字段查询一条稿件记录，默认按 id 查询"""
        if value is None:
            raise ValueError("必须提供查询值")

        async with aiosqlite.connect(self.db_path) as db:
            query = f"SELECT * FROM posts WHERE {key} = ? LIMIT 1"
            async with db.execute(query, (value,)) as cursor:
                row = await cursor.fetchone()
                return self._row_to_post(row) if row else None

    async def update(
        self,
        post_id: int | None,
        key: post_key,
        value,
    ) -> int:
        if key not in self.ALLOWED_QUERY_KEYS:
            raise ValueError(f"不允许更新的字段: {key}")

        # 如果值是 list 或 dict 类型，自动将其转换为 JSON 字符串
        if isinstance(value, list | dict):
            value = json.dumps(value, ensure_ascii=False)

        if post_id is None:
            raise ValueError("post_id未生成，请先用add方法将Post存入数据库")

        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                f"UPDATE posts SET {key} = ? WHERE id = ?", (value, post_id)
            )
            await db.commit()
            return cur.rowcount

    async def delete(self, post_id: int) -> int:
        """删除稿件"""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
            await db.commit()
            return cur.rowcount
