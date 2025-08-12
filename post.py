from datetime import datetime
import json
from pathlib import Path
import typing
import aiosqlite
import pydantic


class Post(pydantic.BaseModel):
    """稿件"""
    id: int
    """稿件ID"""
    uin: int
    """用户ID"""
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
            "approved": "已通过",
            "rejected": "已拒绝",
        }
        lines = [
            f"用户：{self.uin}",
            f"匿名：{'是' if self.anon else '否'}",
            f"状态：{status_map.get(self.status, self.status)}",
            f"时间：{datetime.fromtimestamp(self.create_time).strftime('%Y-%m-%d %H:%M:%S')}",
            f"文本：{self.text}",
            f"图片：{', '.join(self.images) if self.images else '无'}",
        ]
        if self.extra_text:
            lines.append(f"额外文本：{self.extra_text}")
        return "\n".join(lines)


class PostManager:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def init_db(self):
        """初始化数据库"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY,
                    uin INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    images TEXT NOT NULL,
                    anon INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    create_time INTEGER NOT NULL,
                    extra_text TEXT
                )
            """)
            await db.commit()

    async def add_post(self, post: Post):
        """添加稿件"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO posts (
                    id, uin, text, images, anon, status, create_time, extra_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    post.id,
                    post.uin,
                    post.text,
                    json.dumps(post.images, ensure_ascii=False),
                    int(post.anon),
                    post.status,
                    post.create_time,
                    post.extra_text,
                ),
            )
            await db.commit()

    async def exists_by_id(self, post_id: int) -> bool:
        """判断稿件是否存在（根据 id）"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM posts WHERE id = ? LIMIT 1", (post_id,)
            )
            row = await cursor.fetchone()
            return row is not None

    async def get_post(self, key: str = "id", value: typing.Any = None) -> typing.Optional[Post]:
        """根据任意字段获取单条稿件，默认按 id 查询"""
        if value is None:
            raise ValueError("必须提供查询值")

        async with aiosqlite.connect(self.db_path) as db:
            query = f"SELECT * FROM posts WHERE {key} = ? LIMIT 1"
            cursor = await db.execute(query, (value,))
            row = await cursor.fetchone()
            if row:
                return Post(
                    id=row[0],
                    uin=row[1],
                    text=row[2],
                    images=json.loads(row[3]),
                    anon=bool(row[4]),
                    status=row[5],
                    create_time=row[6],
                    extra_text=row[7],
                )
            return None

    async def get_text_and_images_by_id(self, post_id: int) -> tuple[str, list[str]]:
        """根据 ID 返回稿件的 text 和 images"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT text, images FROM posts WHERE id = ?",
                (post_id,),
            )
            row = await cursor.fetchone()
            if row:
                text = row[0]
                images = json.loads(row[1]) if row[1] else []
                return text, images
            return "", []

    async def get_total_count(self) -> int:
        """获取当前数据库中稿件总数"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM posts")
            row = await cursor.fetchone()
            if row is None:
                return 0
            return row[0]

    async def update_status(self, post_id: int, status: str):
        """更新稿件状态"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE posts SET status = ? WHERE id = ?", (status, post_id)
            )
            await db.commit()

    async def delete_post(self, post_id: int):
        """删除稿件"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM posts WHERE id = ?", (post_id,))
            await db.commit()



