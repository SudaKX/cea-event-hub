"""活动内容的静态托管。

两条与别处刻意不同的响应头，都写在同一个地方以免漏掉：

1. **`Access-Control-Allow-Origin`**：不透明源的沙箱活动页对同主机也算跨源，
   没有这个头它连自己目录下的 `data.json` 都取不到。`/api/**` 则**不**带任何
   CORS 头——这个不对称正是让"桥接代理"成为物理边界而非约定的机制
   （design.md 决策 5）。两侧必须同时正确。

2. **缓存策略**：URL 带版本参数时说明它指向一份不可变内容，可以长期缓存；
   否则一律要求回源校验。因为活动页的子资源（`./style.css`）不继承入口页的
   `?v=`，给它一年缓存会让内容更新后页面样式不跟着变。`no-cache` 的含义是
   "用之前先校验"而不是"不要缓存"，配合 ETag 仍能返回 304。
"""

from __future__ import annotations

from typing import Any

from starlette.staticfiles import StaticFiles

#: 长期缓存时长（一年）。仅用于带版本参数的 URL。
IMMUTABLE_MAX_AGE = 31_536_000

_DOCUMENT_SUFFIXES = (".html", ".htm")


class ContentStaticFiles(StaticFiles):
    def __init__(self, *, cors_origin: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.cors_origin = cors_origin

    async def get_response(self, path: str, scope: dict[str, Any]):
        response = await super().get_response(path, scope)

        response.headers["Access-Control-Allow-Origin"] = self.cors_origin

        if self._is_versioned(scope):
            response.headers["Cache-Control"] = f"public, max-age={IMMUTABLE_MAX_AGE}, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"

        return response

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
