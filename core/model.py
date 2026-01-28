
import datetime as _dt
import re
from datetime import datetime

import pydantic
from pydantic import BaseModel


def extract_and_replace_nickname(input_string):
    # 匹配{}内的内容，包括非标准JSON格式
    pattern = r"\{[^{}]*\}"

    def replace_func(match):
        content = match.group(0)
        # 按照键值对分割
        pairs = content[1:-1].split(",")
        nick_value = ""
        for pair in pairs:
            if ":" not in pair:
                continue
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


class Comment(BaseModel):
    """QQ 空间单条评论（含主评论与楼中楼）"""

    uin: int
    nickname: str
    content: str
    create_time: int
    create_time_str: str = ""
    tid: int = 0
    parent_tid: int | None = None  # 为 None 表示主评论
    source_name: str = ""
    source_url: str = ""

    # 可选：把 create_time 转成 datetime
    @property
    def dt(self) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(self.create_time)

    # 可选：去掉 QQ 内置表情标记 [em]e123[/em]
    @property
    def plain_content(self) -> str:
        return re.sub(r"\[em\]e\d+\[/em\]", "", self.content)

    # ------------------- 工厂方法 -------------------
    @staticmethod
    def from_raw(raw: dict, parent_tid: int | None = None) -> "Comment":  # noqa: UP037
        """单条 dict → Comment（内部使用）"""
        return Comment(
            uin=int(raw.get("uin") or 0),
            nickname=raw.get("name") or "",
            content=raw.get("content") or "",
            create_time=int(raw.get("create_time") or 0),
            create_time_str=raw.get("createTime2") or "",
            tid=int(raw.get("tid") or 0),
            parent_tid=parent_tid,
            source_name=raw.get("source_name") or "",
            source_url=raw.get("source_url") or "",
        )

    @staticmethod
    def build_list(comment_list: list[dict]) -> list["Comment"]:  # noqa: UP037
        """把 emotion_cgi_msgdetail_v6 里的 commentlist 整段 flatten 成 List[Comment]"""
        res: list["Comment"] = []  # noqa: UP037
        for main in comment_list:
            # 主评论
            main_tid = int(main.get("tid") or 0)
            res.append(Comment.from_raw(main, parent_tid=None))
            # 楼中楼
            for sub in main.get("list_3") or []:
                res.append(Comment.from_raw(sub, parent_tid=main_tid))
        return res

    # ------------------- 方便打印 / debug -------------------
    def __str__(self) -> str:
        flag = "└─↩" if self.parent_tid else "●"
        return f"{flag} {self.nickname}({self.uin}): {self.plain_content}"

    def pretty(self, indent: int = 0) -> str:
        """树状缩进打印（仅用于把主/子评论手动分组后展示）"""
        prefix = "  " * indent
        return f"{prefix}{self.nickname}: {self.plain_content}"


class Post(pydantic.BaseModel):
    """稿件"""

    id: int | None = None
    """稿件ID"""
    tid: str | None = None
    """QQ给定的说说ID"""
    uin: int = 0
    """用户ID"""
    name: str = ""
    """用户昵称"""
    gin: int = 0
    """群聊ID"""
    text: str = ""
    """文本内容"""
    images: list[str] = pydantic.Field(default_factory=list)
    """图片列表"""
    videos: list[str] = pydantic.Field(default_factory=list)
    """视频列表"""
    anon: bool = False
    """是否匿名"""
    status: str = "approved"
    """状态"""
    create_time: int = pydantic.Field(
        default_factory=lambda: int(datetime.now().timestamp())
    )
    """创建时间"""
    rt_con: str = ""
    """转发内容"""
    comments: list[Comment] = pydantic.Field(default_factory=list)
    """评论列表"""
    extra_text: str | None = None
    """额外文本"""

    class Config:
        json_encoders = {Comment: lambda c: c.model_dump()}

    def to_str(self) -> str:
        """把稿件信息整理成易读文本"""
        is_pending = self.status == "pending"
        lines = [
            f"### 【{self.id}】{self.name}{'投稿' if is_pending else '发布'}于{datetime.fromtimestamp(self.create_time).strftime('%Y-%m-%d %H:%M')}"
        ]
        if self.text:
            lines.append(f"\n\n{remove_em_tags(self.text)}\n\n")
        if self.rt_con:
            lines.append(f"\n\n[转发]：{remove_em_tags(self.rt_con)}\n\n")
        if self.images:
            images_str = "\n".join(f"  ![图片]({img})" for img in self.images)
            lines.append(images_str)
        if self.videos:
            videos_str = "\n".join(f"  [视频]({vid})" for vid in self.videos)
            lines.append(videos_str)
        if self.comments:
            lines.append("\n\n【评论区】\n")
            for comment in self.comments:
                lines.append(
                    f"- **{remove_em_tags(comment.nickname)}**: {remove_em_tags(extract_and_replace_nickname(comment.content))}"
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
