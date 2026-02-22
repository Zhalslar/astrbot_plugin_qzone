from dataclasses import dataclass
from typing import Any

from .constants import QZONE_CODE_OK, QZONE_CODE_UNKNOWN, QZONE_INTERNAL_META_KEY


class QzoneContext:
    """统一封装 Qzone 请求所需的所有动态参数"""

    def __init__(self, uin: int, skey: str, p_skey: str):
        self.uin = uin
        self.skey = skey
        self.p_skey = p_skey

    @property
    def gtk2(self) -> str:
        """动态计算 gtk2"""
        hash_val = 5381
        for ch in self.p_skey:
            hash_val += (hash_val << 5) + ord(ch)
        return str(hash_val & 0x7FFFFFFF)

    def cookies(self) -> dict[str, str]:
        return {
            "uin": f"o{self.uin}",
            "skey": self.skey,
            "p_skey": self.p_skey,
        }

    def headers(self) -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "referer": f"https://user.qzone.qq.com/{self.uin}",
            "origin": "https://user.qzone.qq.com",
            "Host": "user.qzone.qq.com",
            "Connection": "keep-alive",
        }



@dataclass(slots=True)
class ApiResponse:
    """
    统一接口响应结果
    """

    ok: bool
    code: int
    message: str | None
    data: dict[str, Any]
    raw: dict[str, Any]

    @classmethod
    def from_raw(
        cls,
        raw: dict[str, Any],
        *,
        code_key: str = "code",
        msg_key: str | tuple[str, ...] = ("message", "msg"),
        data_key: str | None = None,
        success_code: int = QZONE_CODE_OK,
    ) -> "ApiResponse":
        # 解析 code
        code = raw.get(code_key, QZONE_CODE_UNKNOWN)

        # 解析 message
        message = None
        if isinstance(msg_key, tuple):
            for k in msg_key:
                if raw.get(k):
                    message = raw.get(k)
                    break
        else:
            message = raw.get(msg_key) or raw.get("data", {}).get(msg_key) or code
        # 成功
        if code == success_code:
            data: dict[str, Any]
            if data_key is None:
                data = dict(raw)
                data.pop(QZONE_INTERNAL_META_KEY, None)
            else:
                data = raw.get(data_key, {})
            return cls(
                ok=True,
                code=code,
                message=None,
                data=data,
                raw=raw,
            )

        # 失败
        return cls(
            ok=False,
            code=code,
            message=message,
            data={},
            raw=raw,
        )

    # -------------------------
    # Python 语义增强
    # -------------------------
    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        if self.ok:
            return f"<ApiResponse ok code={self.code}>"
        return f"<ApiResponse fail code={self.code} message={self.message!r}>"

    # -------------------------
    # 使用辅助
    # -------------------------
    def unwrap(self) -> dict[str, Any]:
        if not self.ok:
            raise RuntimeError(f"{self.code}: {self.message}")
        return self.data or {}

    def get(self, key: str, default: Any = None) -> Any:
        """
        安全访问 data 内字段
        """
        if not self.ok or not self.data:
            return default
        return self.data.get(key, default)

    # -------------------------
    # 调试
    # -------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "raw": self.raw,
        }
