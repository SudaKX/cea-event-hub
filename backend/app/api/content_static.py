"""活动内容的静态托管。

三条与别处刻意不同的行为，都写在同一个地方以免漏掉：

1. **`Access-Control-Allow-Origin`**：不透明源的沙箱活动页对同主机也算跨源，
   没有这个头它连自己目录下的 `data.json` 都取不到。`/api/**` 则**不**带任何
   CORS 头——这个不对称正是让"桥接代理"成为物理边界而非约定的机制
   （design.md 决策 5）。两侧必须同时正确。

2. **缓存策略**：URL 带版本参数时说明它指向一份不可变内容，可以长期缓存；
   否则一律要求回源校验。因为活动页的子资源（`./style.css`）不继承入口页的
   `?v=`，给它一年缓存会让内容更新后页面样式不跟着变。`no-cache` 的含义是
   "用之前先校验"而不是"不要缓存"，配合 ETag 仍能返回 304。

3. **桥接脚本注入**：返回 HTML 时，若活动页自己没引用 SDK 就补一行。这样作者
   零约定，而显式引用仍然有效（幂等）。见 `services/content_inject.py` 里关于
   "为什么必须由服务端做"的说明。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.responses import Response
from starlette.staticfiles import StaticFiles

from app.services.content_inject import ensure_sdk

#: 长期缓存时长（一年）。仅用于带版本参数的 URL。
IMMUTABLE_MAX_AGE = 31_536_000

_DOCUMENT_SUFFIXES = (".html", ".htm")

#: 注入时要保留的响应头：条件请求（304）靠它们工作，重建响应时不能丢
_PRESERVED_HEADERS = ("etag", "last-modified")


class ContentStaticFiles(StaticFiles):
    def __init__(
        self,
        *,
        cors_origin: str,
        sdk_path: str = "/sdk/v1/cea.js",
        inject_sdk: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.cors_origin = cors_origin
        self.sdk_path = sdk_path
        self.inject_sdk = inject_sdk

    async def get_response(self, path: str, scope: dict[str, Any]):
        response = await super().get_response(path, scope)

        if self.inject_sdk and is_document(path) and response.status_code == 200:
            response = self._with_sdk(path, response)

        response.headers["Access-Control-Allow-Origin"] = self.cors_origin

        if self._is_versioned(scope):
            response.headers["Cache-Control"] = f"public, max-age={IMMUTABLE_MAX_AGE}, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"

        return response

    def _with_sdk(self, path: str, original: Any) -> Response:
        """读出文件、注入脚本、重建响应。

        **不自己去算 ETag 与 Last-Modified**，而是从 Starlette 已经生成的
        `FileResponse` 上抄过来 —— 那两个头的算法属于框架内部细节，自己复刻一份
        迟早会跟框架版本对不上，导致条件请求失效。

        也不能沿用原来的 `Content-Length`：注入会让内容变长，照抄会把响应截断。
        新建 `Response` 时会按实际 body 重新计算。
        """
        located = self.lookup_path(path)
        if not located or located[1] is None:
            # 走到这里说明 super() 已经成功返回过，理论上不会发生；保守起见放弃注入
            return original

        full_path = located[0]
        try:
            # 活动页是我们自己托管的内容，编码可控；errors="replace" 保证不会
            # 因为一个坏字节让整页 500
            text = Path(full_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return original

        rebuilt = Response(
            content=ensure_sdk(text, src=self.sdk_path).encode("utf-8"),
            status_code=200,
            media_type="text/html",
        )
        for header in _PRESERVED_HEADERS:
            value = original.headers.get(header)
            if value:
                rebuilt.headers[header] = value
        return rebuilt

    @staticmethod
    def _is_versioned(scope: dict[str, Any]) -> bool:
        """URL 是否带版本参数。

        只认 `v`：把任意查询串都当作版本标记会让 `?t=<时间戳>` 这类缓存击穿
        参数意外地获得长期缓存。
        """
        query = scope.get("query_string", b"")
        if not query:
            return False
        for pair in query.decode("latin-1").split("&"):
            key, _, value = pair.partition("=")
            if key == "v" and value:
                return True
        return False


def is_document(path: str) -> bool:
    return path.lower().endswith(_DOCUMENT_SUFFIXES)


__all__ = ["ContentStaticFiles", "IMMUTABLE_MAX_AGE", "is_document"]
