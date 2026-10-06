"""以 `python -m app` 启动后端。

## 为什么需要这个入口

uvicorn 的 `--host` / `--port` 是它的 **CLI 参数**，而它的
`auto_envvar_prefix="UVICORN"` 只从**进程环境**取值 —— **它不会去读 `backend/.env`**。
pydantic 会读那份 `.env`。两者叠加就是一个很容易踩的错位：

    把 UVICORN_PORT=9000 写进 .env
      -> 应用读得到它，uvicorn 读不到
      -> 服务照旧监听 8000，而且不报任何错

所以监听地址交给应用自己的配置（`API_HOST` / `API_PORT`），由这里显式传给 uvicorn：

    python -m app

开发时仍可用 CLI（`uvicorn app.main:app --reload`）—— 那条路不走这里，也就不受
`.env` 里的监听配置影响。

## 三个参数刻意写死在这里，不从 .env 取值

| 参数 | 理由 |
|---|---|
| `workers=1` | 应用层限流是**进程内内存**实现。多进程会让每个进程各持一份计数，实际阈值被放大到 N 倍。SQLite 的写入本来就串行在单写者锁上，多进程只增锁争用 |
| `proxy_headers=True` | 不读转发头，限流键永远是 nginx 的地址 |
| `forwarded_allow_ips` | 只信 nginx 写的转发头。不限定来源，客户端自报一个 `X-Forwarded-For` 就能把限流键算到伪造地址上 |

它们是**硬约束而不是可调参数**，理由见 `docs/deployment.md` 的「启动参数不是可选项」。
把它们做成可配置，等于把一个"绕过自己防线"的开关交出去。

`forwarded_allow_ips` 从 `TRUSTED_PROXY_IPS` **推导**而不是另写一份：部署文档要求这两处
一致，而"要求一致"的下一站就是"某天它们不再一致"。由前者推出后者，这条约束就不再依赖
人记得同步。
"""

from __future__ import annotations

import uvicorn

from app.core.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=",".join(settings.TRUSTED_PROXY_IPS),
    )


if __name__ == "__main__":
    main()
