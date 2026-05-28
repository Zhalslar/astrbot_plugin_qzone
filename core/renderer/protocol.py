from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..model import Post


class MessageRenderer(Protocol):
    async def render_post(self, post: Post) -> Path | None: ...

    async def render_text(self, text: str) -> Path | None: ...
