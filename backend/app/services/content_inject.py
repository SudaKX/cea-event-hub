"""活动内容 HTML 的桥接脚本注入。

**为什么在服务端做，而不是让宿主注入。** 宿主够不到 iframe 内部：不透明源下
`iframe.contentDocument` 抛 SecurityError，所以它无法替活动页插入 `<script>`。
postMessage 也自举不了 —— 要"收到地址再加载"，活动页得先有代码在监听。因此唯一
能去掉"作者必须记得写一行"这个约定的位置，是**托管内容的那一层**。

这不违反"宿主不得向 iframe 注入脚本"那条约束：注入发生在内容服务端，返回的是
另一份字节；宿主仍然碰不到 iframe 的文档，沙箱边界没有任何变化。

**幂等。** 活动页自己写了 `<script src="/sdk/v1/cea.js">`（或任何带
`id="cea-sdk"` 的标签）就不再注入，两种写法都能工作。

**用 `id` 而不是 `class` 标记注入的标签。** 两者都是全局属性、都合法；这里选
`id` 是因为语义更准：一份文档里只该有一个桥接脚本，`id` 的"唯一"正好对上
`class` 的"可多个"。它同时给活动页一个稳定的抓手 —— `getElementById('cea-sdk')`
可以判断 SDK 是否就位。
"""

from __future__ import annotations

import re

#: 注入的标签会带上这个 id。既是幂等判断的依据，也让作者在 DevTools 里一眼
#: 看出这一行不是自己写的。
SDK_ELEMENT_ID = "cea-sdk"

_HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)
_BODY_OPEN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def has_sdk_reference(html: str) -> bool:
    """活动页是否已经自己引用了桥接脚本。

    用宽松的子串判断而不是严格解析：**误判为"已引用"会让页面拿不到 SDK**，
    所以这里宁可偏向"检测到就算已引用"。作者如果真的写了这两串字符，那基本
    就是引用；注释里恰好写到的概率可以忽略。
    """
    lowered = html.lower()
    return SDK_ELEMENT_ID in lowered or "/sdk/v1/cea.js" in lowered


def sdk_tag(src: str) -> str:
    return f'<script src="{src}" id="{SDK_ELEMENT_ID}"></script>'


def inject_sdk_tag(html: str, *, src: str) -> str:
    """把桥接脚本插进 HTML。

    插入位置按可靠性依次退让：
      1. `</head>` 之前 —— 最常见，也让 SDK 尽早开始监听
      2. `<body ...>` 之后 —— 没有 head 的片段式页面
      3. 直接前置 —— 畸形到两者都没有

    第 3 种情况下前置而不是追加：SDK 要在活动页自己的脚本之前就位，否则活动页
    里的 `CEA.ready` 会在 SDK 定义 `window.CEA` 之前求值。
    """
    tag = sdk_tag(src)

    head_close = _HEAD_CLOSE.search(html)
    if head_close:
        return html[: head_close.start()] + tag + html[head_close.start() :]

    body_open = _BODY_OPEN.search(html)
    if body_open:
        return html[: body_open.end()] + tag + html[body_open.end() :]

    return tag + html


def ensure_sdk(html: str, *, src: str) -> str:
    """需要时注入；已经有引用则原样返回。"""
    if has_sdk_reference(html):
        return html
    return inject_sdk_tag(html, src=src)


__all__ = [
    "SDK_ELEMENT_ID",
    "ensure_sdk",
    "has_sdk_reference",
    "inject_sdk_tag",
    "sdk_tag",
]
