from __future__ import annotations

from pathlib import Path

from ..model import Post, extract_and_replace_nickname, remove_em_tags
from .parser_card_data import (
    Author,
    CommentEntry,
    ImageContent,
    ParseResult,
    Platform,
)
from .resource_fetcher import ResourceFetcher


class QzonePostCardAdapter:
    PLATFORM = Platform(name="qzone", display_name="QQ空间")

    def __init__(self, fetcher: ResourceFetcher):
        self.fetcher = fetcher

    async def to_parse_result(self, post: Post) -> ParseResult:
        avatar_path = await self._resolve_avatar(post)
        result = ParseResult(
            platform=self.PLATFORM,
            author=Author(
                name=post.show_name if post.show_name else post.name,
                avatar=avatar_path,
            ),
            timestamp=post.create_time,
            text=remove_em_tags(post.text),
            contents=await self._resolve_contents(post),
            comments=self._build_comments(post),
            extra=self._build_extra(post),
        )
        if post.rt_con:
            result.repost = ParseResult(
                platform=self.PLATFORM,
                author=Author(name="转发内容"),
                text=remove_em_tags(post.rt_con),
            )
        return result

    async def _resolve_avatar(self, post: Post) -> Path | None:
        if post.avatar_url:
            avatar_path = await self.fetcher.fetch_url_to_cache(
                post.avatar_url,
                prefix="avatar_url",
                suffix=".jpg",
            )
            if avatar_path is not None:
                return avatar_path

        if post.uin:
            return await self.fetcher.fetch_avatar_to_cache(post.uin)
        return None

    async def _resolve_contents(self, post: Post) -> list[ImageContent]:
        images: list[ImageContent] = []
        for index, url in enumerate(post.images):
            path = await self.fetcher.fetch_url_to_cache(
                url,
                prefix=f"image_{index}",
                suffix=".jpg",
            )
            if path is not None:
                images.append(ImageContent(path))
        return images

    def _build_extra(self, post: Post) -> dict[str, str]:
        infos: list[str] = []
        if post.extra_text:
            infos.append(post.extra_text)
        return {"info": "\n".join(info for info in infos if info)} if infos else {}

    def _build_comments(self, post: Post) -> list[CommentEntry]:
        comments: list[CommentEntry] = []
        for comment in post.comments[:5]:
            nickname = remove_em_tags(comment.nickname).strip()
            content = remove_em_tags(
                extract_and_replace_nickname(comment.content).strip()
            ).strip()
            if not nickname and not content:
                continue
            comments.append(CommentEntry(nickname=nickname, content=content))
        return comments
