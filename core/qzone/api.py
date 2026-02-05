import base64
import time
from typing import Any

from astrbot.api import logger

from ..config import PluginConfig
from ..model import Comment, Post
from .client import QzoneHttpClient
from .model import ApiResponse
from .parser import QzoneParser
from .session import QzoneSession
from .utils import normalize_images


class QzoneAPI(QzoneHttpClient):
    """QQ 空间 HTTP API 封装"""

    BASE_URL = "https://user.qzone.qq.com"
    UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
    EMOTION_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    DOLIKE_URL = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
    LIST_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    ZONE_LIST_URL = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds3_html_more"
    VISITOR_URL = "https://h5.qzone.qq.com/proxy/domain/g.qzone.qq.com/cgi-bin/friendshow/cgi_get_visitor_more"
    REPLY_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
    DELETE_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_delete_v6"
    DETAIL_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msgdetail_v6"

    def __init__(self, session: QzoneSession, config: PluginConfig):
        super().__init__(session, config)

    async def _upload_image(self, image: bytes) -> ApiResponse:
        """上传单张图片 (本接口较为脆弱)"""
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "POST",
            self.UPLOAD_IMAGE_URL,
            data={
                "filename": "filename",
                "uploadtype": "1",
                "albumtype": "7",
                "skey": ctx.skey,
                "uin": ctx.uin,
                "p_skey": ctx.p_skey,
                "output_type": "json",
                "base64": "1",
                "picfile": base64.b64encode(image).decode(),
            },
            headers={
                "referer": f"{self.BASE_URL}/{ctx.uin}",
                "origin": self.BASE_URL,
            },
            timeout=60,
        )
        logger.debug(raw)
        return ApiResponse.from_raw(raw, code_key="ret", msg_key="msg")

    async def get_visitor(self) -> ApiResponse:
        """获取访客数"""
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "GET",
            self.VISITOR_URL,
            params={
                "uin": ctx.uin,
                "mask": 7,
                "g_tk": ctx.gtk2,
                "page": 1,
                "fupdate": 1,
                "clear": 1,
            },
        )
        return ApiResponse.from_raw(raw)

    async def publish(self, post: Post) -> ApiResponse:
        """发表说说, 返回tid"""
        ctx = await self.session.get_ctx()
        data: dict[str, Any] = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": post.text,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": ctx.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": f"{self.BASE_URL}/{ctx.uin}",
        }
        if post.images:
            logger.debug(f"正在上传图片: {post.images}")
            pic_bos, richvals = [], []
            imgs: list[bytes] = await normalize_images(post.images)
            for img in imgs:
                resp = await self._upload_image(img)
                if not resp.ok:
                    raise RuntimeError(f"上传图片失败: {resp.message}")
                picbo, richval = QzoneParser.parse_upload_result(resp.data)
                pic_bos.append(picbo)
                richvals.append(richval)
            data.update(
                pic_bo=",".join(pic_bos),
                richtype="1",
                richval="\t".join(richvals),
            )

        raw = await self.request(
            "POST",
            self.EMOTION_URL,
            params={"g_tk": ctx.gtk2, "uin": ctx.uin},
            data=data,
        )
        return ApiResponse.from_raw(raw)

    async def like(self, post: Post) -> ApiResponse:
        """
        点赞指定说说
        """
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "POST",
            self.DOLIKE_URL,
            params={
                "g_tk": ctx.gtk2,
            },
            data={
                "qzreferrer": f"{self.BASE_URL}/{ctx.uin}",  # 来源
                "opuin": ctx.uin,  # 操作者QQ
                "unikey": f"{self.BASE_URL}/{post.uin}/mood/{post.tid}",  # 动态唯一标识
                "curkey": f"{self.BASE_URL}/{post.uin}/mood/{post.tid}",  # 要操作的动态对象
                "appid": 311,  # 应用ID(说说:311)
                "from": 1,  # 来源
                "typeid": 0,  # 类型ID
                "abstime": int(time.time()),  # 当前时间戳
                "fid": post.tid,  # 动态ID
                "active": 0,  # 活动ID
                "format": "json",  # 返回格式
                "fupdate": 1,  # 更新标记
            },
        )
        return ApiResponse.from_raw(raw)

    async def comment(self, post: Post, content: str) -> ApiResponse:
        """
        评论指定说说
        """
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "POST",
            self.COMMENT_URL,
            params={"g_tk": ctx.gtk2},
            data={
                "topicId": f"{post.uin}_{post.tid}__1",  # 说说ID
                "uin": ctx.uin,  # botQQ
                "hostUin": post.uin,  # 目标QQ
                "feedsType": 100,  # 说说类型
                "inCharset": "utf-8",  # 字符集
                "outCharset": "utf-8",  # 字符集
                "plat": "qzone",  # 平台
                "source": "ic",  # 来源
                "platformid": 52,  # 平台id
                "format": "fs",  # 返回格式
                "ref": "feeds",  # 引用
                "content": content,  # 评论内容
            },
        )
        return ApiResponse.from_raw(raw)

    async def reply(
        self,
        post: Post,
        comment: Comment,
        content: str,
    ) -> ApiResponse:
        """回复指定评论"""
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "POST",
            self.REPLY_URL,
            params={
                "g_tk": ctx.gtk2,
            },
            data={
                "topicId": f"{post.uin}_{post.tid}__1",
                "uin": ctx.uin,
                "hostUin": post.uin,
                "feedsType": 100,
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                "plat": "qzone",
                "source": "ic",
                "platformid": 52,
                "format": "fs",
                "ref": "feeds",
                "content": content,
                "commentId": comment.tid,
                "commentUin": comment.uin,
                "richval": "",  # 富文本内容
                "richtype": "",  # 富文本类型
                "private": "0",  # 是否私密评论
                "paramstr": "2",
                "qzreferrer": f"https://user.qzone.qq.com/{ctx.uin}/main",  # 来源页
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
                "TE": "trailers",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
                "Referer": "https://user.qzone.qq.com/",
                "Origin": "https://user.qzone.qq.com",
            },
        )
        return ApiResponse.from_raw(raw)

    async def delete(self, tid: str) -> ApiResponse:
        """删除指定说说"""
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "POST",
            self.DELETE_URL,
            params={"g_tk": ctx.gtk2},
            data={
                "uin": ctx.uin,
                "topicId": f"{ctx.uin}_{tid}__1",
                "feedsType": 0,
                "feedsFlag": 0,
                "feedsKey": tid,
                "feedsAppid": 311,
                "feedsTime": int(time.time()),
                "fupdate": 1,
                "ref": "feeds",
                "qzreferrer": (
                    "https://user.qzone.qq.com/"
                    f"proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/"
                    f"feeds_html_module?g_iframeUser=1&i_uin={ctx.uin}&i_login_uin={ctx.uin}"
                    "&mode=4&previewV8=1&style=35&version=8"
                    "&needDelOpr=true"
                ),
            },
        )
        return ApiResponse.from_raw(raw)

    async def get_feeds(
        self,
        target_id: str,
        *,
        pos: int = 0,
        num: int = 1,
    ) -> ApiResponse:
        """
        获取指定QQ号的好友说说列表

        Args:
            target_id (str): 目标QQ号。
            pos (int): 起始位置。
            num (int): 要获取的说说数量。
        """
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "GET",
            self.LIST_URL,
            params={
                "g_tk": ctx.gtk2,
                "uin": target_id,  # 目标QQ
                "ftype": 0,  # 全部说说
                "sort": 0,  # 最新在前
                "pos": pos,  # 起始位置
                "num": num,  # 获取条数
                "replynum": 100,  # 评论数
                "callback": "_preloadCallback",
                "code_version": 1,
                "format": "json",
                "need_comment": 1,
                "need_private_comment": 1,
            },
        )
        return ApiResponse.from_raw(raw)

    async def get_detail(self, post: Post) -> ApiResponse:
        """
        获取单条说说详情（含完整评论、转发、图片、视频等）
        """
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "GET",
            self.DETAIL_URL,
            params={
                "uin": post.uin,
                "tid": post.tid,
                "format": "jsonp",
                "g_tk": ctx.gtk2,
            },
        )

        return ApiResponse.from_raw(raw)

    async def get_recent_feeds(self, page: int = 1) -> ApiResponse:
        """
        获取自己的好友说说列表，返回已读与未读的说说列表
        """
        ctx = await self.session.get_ctx()
        raw = await self.request(
            "GET",
            self.ZONE_LIST_URL,
            params={
                "uin": ctx.uin,  # QQ号
                "scope": 0,  # 访问范围
                "view": 1,  # 查看权限
                "filter": "all",  # 全部动态
                "flag": 1,  # 标记
                "applist": "all",  # 所有应用
                "pagenum": page,  # 页码, 测试时发现暂时是无效配置
                "aisortEndTime": 0,  # AI排序结束时间
                "aisortOffset": 0,  # AI排序偏移
                "aisortBeginTime": 0,  # AI排序开始时间
                "begintime": 0,  # 开始时间
                "format": "json",  # 返回格式
                "g_tk": ctx.gtk2,  # 令牌
                "useutf8": 1,  # 使用UTF8编码
                "outputhtmlfeed": 1,  # 输出HTML格式
            },
        )
        return ApiResponse.from_raw(raw)
