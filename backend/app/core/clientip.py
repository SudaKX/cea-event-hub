"""客户端地址解析，含转发头的信任边界。

**只信任受信代理写入的转发头。** 不设这条边界，客户端自己发一个
`X-Forwarded-For` 就能把限流键算到伪造地址上，应用层限流形同虚设
（design.md 决策 10 的三个坑之一）。

本模块刻意保持**框架无关**（只收原始字符串），因此这段判定逻辑可以脱离 HTTP
直接单测；把 `Request` 拆开的那一步在 `core/deps.py` 里。
"""

from __future__ import annotations

from collections.abc import Sequence

FORWARDED_HEADER = "X-Forwarded-For"


def resolve_client_ip(
    *,
    peer: str,
    forwarded_for: str | None,
    trusted_proxies: Sequence[str],
) -> str:
    """返回用于限流与审计的客户端地址。

    取转发头里**最右侧**那一项：nginx 用 `$proxy_add_x_forwarded_for` 会把直连
    它的对端地址追加到末尾，因此最右项才是 nginx 实际看到的地址；左边全部是
    客户端自己塞的，不可信。取最左项是这里最常见的写错方式。
    """
    if not peer or peer not in trusted_proxies:
        # 非受信来源：完全忽略它声称的地址
        return peer

    if not forwarded_for:
        return peer

    parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    return parts[-1] if parts else peer


__all__ = ["FORWARDED_HEADER", "resolve_client_ip"]
