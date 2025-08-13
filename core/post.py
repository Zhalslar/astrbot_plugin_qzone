from datetime import datetime
import json
from pathlib import Path
import typing
import aiosqlite
import pydantic


class Post(pydantic.BaseModel):
    """稿件"""

    id: typing.Optional[int] = None
    """稿件ID"""
    tid: str = ""
    """QQ给定的说说ID"""
    uin: int
    """用户ID"""
    name: str
    """用户昵称"""
    gin: int
    """群聊ID"""
    text: str
    """文本内容"""
    images: list[str]
    """图片key列表"""
    anon: bool
    """是否匿名"""
    status: str
    """状态"""
    create_time: int
    """创建时间"""
    extra_text: typing.Optional[str] = None
    """额外文本"""

    def to_str(self) -> str:
        """把稿件信息整理成易读文本"""
        status_map = {
            "pending": "待审核",
            "approved": "已发布",
            "rejected": "未发布",
        }
        lines = [
            f"时间：{datetime.fromtimestamp(self.create_time).strftime('%Y-%m-%d %H:%M')}",
            f"用户：{self.name}({self.uin})",
        ]
        if self.gin:
            lines.append(f"群聊：{self.gin}")
        if self.anon:
            lines.append("匿名：是")
        lines += [
            f"状态：{status_map.get(self.status, self.status)}",
            f"文本：{self.text or '无'}",
            f"图片：{', '.join(self.images) if self.images else '无'}",
        ]
        if self.extra_text:
            lines.append(f"补充：{self.extra_text}")
        return "\n".join(lines)


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
        "create_time",
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
            anon=bool(row[7]),
            status=row[8],
            create_time=row[9],
            extra_text=row[10],
        )

    @staticmethod
    def _encode_images(imgs: list[str]) -> str:
        return json.dumps(imgs, ensure_ascii=False)

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
                    anon INTEGER NOT NULL CHECK(anon IN (0,1)),
                    status TEXT NOT NULL,
                    create_time INTEGER NOT NULL,
                    extra_text TEXT
                )
            """)
            await db.commit()

    async def add(self, post: Post) -> int:
        """添加稿件"""
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                INSERT INTO posts (tid, uin, name, gin, text, images, anon, status, create_time, extra_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post.tid,
                    post.uin,
                    post.name,
                    post.gin,
                    post.text,
                    self._encode_images(post.images),
                    int(post.anon),
                    post.status,
                    post.create_time,
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
        key: typing.Literal[
            "id",
            "tid",
            "uin",
            "name",
            "gin",
            "status",
            "anon",
            "text",
            "images",
            "create_time",
            "extra_text",
        ] = "id",
        value,
    ) -> typing.Optional[Post]:
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
        post_id: int,
        key: typing.Literal[
            "id",
            "tid",
            "uin",
            "name",
            "gin",
            "status",
            "anon",
            "text",
            "images",
            "create_time",
            "extra_text",
        ],
        value,
    ) -> int:
        if key not in self.ALLOWED_QUERY_KEYS:
            raise ValueError(f"不允许更新的字段: {key}")
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
