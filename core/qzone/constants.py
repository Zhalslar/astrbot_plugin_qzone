from http import HTTPStatus

# API code values
QZONE_CODE_OK = 0
QZONE_CODE_UNKNOWN = -1
QZONE_CODE_LOGIN_EXPIRED = -3000
QZONE_CODE_PERMISSION_DENIED = 403
QZONE_CODE_PERMISSION_DENIED_LEGACY = -403

# Parser-level synthetic messages
QZONE_MSG_EMPTY_RESPONSE = "响应内容为空"
QZONE_MSG_INVALID_RESPONSE = "响应内容格式异常"
QZONE_MSG_JSON_PARSE_ERROR = "JSON 解析失败"
QZONE_MSG_NON_OBJECT_RESPONSE = "JSON 根节点不是对象"
QZONE_MSG_PERMISSION_DENIED = "权限不足"

# Internal metadata keys injected by client-side transport
QZONE_INTERNAL_META_KEY = "__qzone_internal__"
QZONE_INTERNAL_HTTP_STATUS_KEY = "http_status"

# HTTP status aliases used by the transport layer
HTTP_STATUS_UNAUTHORIZED = int(HTTPStatus.UNAUTHORIZED)
HTTP_STATUS_FORBIDDEN = int(HTTPStatus.FORBIDDEN)
