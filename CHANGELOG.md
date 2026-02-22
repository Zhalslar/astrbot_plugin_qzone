# Changelog

## v3.0.2

- Fix: 修复 QZone API 返回空字符串时 `json5.loads()` 抛出 `ValueError: Empty strings are not legal JSON5` 导致插件异常中断的问题。
- Improve: 新增接口响应兜底解析，针对空响应、无 JSON 片段、JSON 解析失败返回可处理的错误结果，避免直接抛异常。
- Improve: `QzoneParser.parse_response` 在解析结果非 `dict` 时，改为返回统一错误对象，不再抛出 `RuntimeError`，调用方错误处理路径更一致。
- Fix: 修复 `403` 被误判为“登录失效”并重复重登的问题；仅在 `401` 或接口明确登录失效（`code = -3000`）时触发重登。
- Improve: 查询说说失败时细化错误提示，区分“无权限查看”“登录状态失效”“接口响应异常”“暂无可见说说”等场景。
- Refactor: 新增 `core/qzone/constants.py`，集中维护错误码、错误消息、HTTP 状态及内部元数据键，降低多处散落导致的不一致风险。
- Refactor: 将 HTTP 状态注入到内部元数据 `__qzone_internal__.http_status`，并在 `ApiResponse.data` 中剥离内部字段，避免与业务字段冲突。
- Improve: 统一错误消息常量为中文（如“响应内容为空”“权限不足”），更符合插件用户使用场景。
- Behavior: 保持无参数调用默认行为为查询最新一条说说（`pos=0, num=1`）。
