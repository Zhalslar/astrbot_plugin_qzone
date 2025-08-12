import asyncio
from http.cookies import SimpleCookie
import json
import base64
from aiocqhttp import CQHttp
import aiohttp
from astrbot.api import logger



def generate_gtk(skey) -> str:
    """生成gtk"""
    hash_val = 5381
    for i in range(len(skey)):
        hash_val += (hash_val << 5) + ord(skey[i])
    return str(hash_val & 2147483647)


def get_picbo_and_richval(upload_result):
    json_data = upload_result

    # for debug
    if "ret" not in json_data:
        raise Exception("获取图片picbo和richval失败")
    # end

    if json_data["ret"] != 0:
        raise Exception("上传图片失败")
    picbo_spt = json_data["data"]["url"].split("&bo=")
    if len(picbo_spt) < 2:
        raise Exception("上传图片失败")
    picbo = picbo_spt[1]

    richval = ",{},{},{},{},{},{},,{},{}".format(
        json_data["data"]["albumid"],
        json_data["data"]["lloc"],
        json_data["data"]["sloc"],
        json_data["data"]["type"],
        json_data["data"]["height"],
        json_data["data"]["width"],
        json_data["data"]["height"],
        json_data["data"]["width"],
    )

    return picbo, richval


GET_VISITOR_AMOUNT_URL = "https://h5.qzone.qq.com/proxy/domain/g.qzone.qq.com/cgi-bin/friendshow/cgi_get_visitor_more?uin={}&mask=7&g_tk={}&page=1&fupdate=1&clear=1"
UPLOAD_IMAGE_URL = "https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image"
EMOTION_PUBLISH_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"


class QzoneAPI:
    def __init__(self):
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100, ssl=False),
            timeout=aiohttp.ClientTimeout(total=10),
        )
        self.cookies: dict = {}
        self.gtk2: str = ""
        self.uin: int = 0

    async def login(self, client: CQHttp):
        cookie_str = (await client.get_cookies(domain="user.qzone.qq.com")).get(
            "cookies", ""
        )
        self.cookies = {k: v.value for k, v in SimpleCookie(cookie_str).items()}
        if "p_skey" in self.cookies:
            self.gtk2 = generate_gtk(self.cookies["p_skey"])
        if "uin" in self.cookies:
            self.uin = int(self.cookies["uin"][1:])
        logger.info(f"Cookies: {self.cookies}")

    async def do(
        self,
        method: str,
        url: str,
        params: dict = {},
        data: dict = {},
        headers: dict = {},
        timeout: int = 10,
    ) -> aiohttp.ClientResponse:
        async with self.session.request(
            method=method.upper(),
            url=url,
            params=params,
            data=data,
            headers=headers,
            cookies=self.cookies,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            await resp.read()
            return resp

    async def token_valid(self, *, max_retry: int = 3, backoff: float = 1.0) -> bool:
        """
        验证 token 是否可用。
        :param max_retry: 最大重试次数（含首次）。
        :param backoff:   重试间隔基数（秒），实际 sleep 时间为 `backoff * (2 ** attempt)`。
        """
        for attempt in range(max_retry):
            try:
                await self.get_visitor_amount()  # 只要调用成功就认为有效
                return True
            except asyncio.CancelledError:       # 任务被取消时直接向上抛
                raise
            except Exception as exc:
                logger.error(f"Token validation failed (attempt {attempt + 1}): {exc!r}")
                if attempt < max_retry - 1:
                    await asyncio.sleep(backoff * (2 ** attempt))
        return False

    def _image_to_base64(self, image: bytes) -> str:
        pic_base64 = base64.b64encode(image)
        return str(pic_base64)[2:-1]

    async def get_visitor_amount(self) -> tuple[int, int]:
        """获取空间访客信息

        Returns:
            tuple[int, int]: 今日访客数, 总访客数
        """
        res = await self.do(
            method="GET",
            url=GET_VISITOR_AMOUNT_URL.format(self.uin, self.gtk2),
        )
        json_text = res.text.replace("_Callback(", "")[:-3]

        try:
            json_obj = json.loads(json_text)
            visit_count = json_obj["data"]
            return visit_count["todaycount"], visit_count["totalcount"]
        except Exception as e:
            raise e

    async def _upload_image(self, image: bytes) -> str:
        """上传图片"""

        res = await self.do(
            method="POST",
            url=UPLOAD_IMAGE_URL,
            data={
                "filename": "filename",
                "zzpanelkey": "",
                "uploadtype": "1",
                "albumtype": "7",
                "exttype": "0",
                "skey": self.cookies["skey"],
                "zzpaneluin": self.uin,
                "p_uin": self.uin,
                "uin": self.uin,
                "p_skey": self.cookies["p_skey"],
                "output_type": "json",
                "qzonetoken": "",
                "refer": "shuoshuo",
                "charset": "utf-8",
                "output_charset": "utf-8",
                "upload_hd": "1",
                "hd_width": "2048",
                "hd_height": "10000",
                "hd_quality": "96",
                "backUrls": "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,http://119.147.64.75/cgi-bin/upload/cgi_upload_image",
                "url": f"https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk={self.gtk2}",
                "base64": "1",
                "picfile": self._image_to_base64(image),
            },
            headers={
                "referer": "https://user.qzone.qq.com/" + str(self.uin),
                "origin": "https://user.qzone.qq.com",
            },
            timeout=60,
        )
        if res.status == 200:
            text = await res.text()
            return eval(text[text.find("{") : text.rfind("}") + 1])
        else:
            raise Exception("上传图片失败")

    async def publish_emotion(self, content: str, images: list[bytes] | None = []) -> str:
        """发表说说
        :return: 说说tid
        :except: 发表失败
        """

        if images is None:
            images = []

        post_data = {
            "syn_tweet_verson": "1",
            "paramstr": "1",
            "who": "1",
            "con": content,
            "feedversion": "1",
            "ver": "1",
            "ugc_right": "1",
            "to_sign": "0",
            "hostuin": self.uin,
            "code_version": "1",
            "format": "json",
            "qzreferrer": "https://user.qzone.qq.com/" + str(self.uin),
        }

        if len(images) > 0:
            # 挨个上传图片
            pic_bos = []
            richvals = []
            for img in images:
                uploadresult = await self._upload_image(img)
                picbo, richval = get_picbo_and_richval(uploadresult)
                pic_bos.append(picbo)
                richvals.append(richval)

            post_data["pic_bo"] = ",".join(pic_bos)
            post_data["richtype"] = "1"
            post_data["richval"] = "\t".join(richvals)

        res = await self.do(
            method="POST",
            url=EMOTION_PUBLISH_URL,
            params={
                "g_tk": self.gtk2,
                "uin": self.uin,
            },
            data=post_data,
            headers={
                "referer": "https://user.qzone.qq.com/" + str(self.uin),
                "origin": "https://user.qzone.qq.com",
            },
        )
        text = await res.text()
        logger.debug("publish_emotion raw response:\n%s", text[:2000])
        data = json.loads(text)
        if data.get("code") == 0:
            return data["tid"]
        else:
            raise Exception(f"发表说说失败：{data.get('message', data)}")

    async def terminate(self):
        await self.session.close()
