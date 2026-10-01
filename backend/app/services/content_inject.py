"""活动内容 HTML 的桥接脚本注入。

**为什么在服务端做，而不是让宿主注入。** 宿主够不到 iframe 内部：不透明源下
`iframe.contentDocument` 抛 SecurityError，所以它无法替活动页插入 `<script>`。
postMessage 也自举不了 —— 要"收到地址再加载"，活动页得先有代码在监听。因此唯一
能去掉"作者必须记得写一行"这个约定的位置，是**托管内容的那一层**。

这不违反"宿主不得向 iframe 注入脚本"那条约束：注入发生在内容服务端，返回的是
另一份字节；宿主仍然碰不到 iframe 的文档，沙箱边界没有任何变化。

## 为什么用 HTMLParser 定位，而不是正则，也不是 XML 解析

**不用 XML 解析**（`ElementTree` / `lxml` 树模式）：

1. 活动页是手写 HTML5，不是 XML。`<br>`、未加引号的属性值、布尔属性都是合法
   HTML 但非法 XML，XML 解析器会直接拒绝整份文档。
2. 树模式必须把文档序列化回去，而序列化会**重写作者的文件** —— 属性顺序、引号
   风格、自闭合写法、内联脚本的空白都可能被改动。本模块只插入一个子串，从不改写
   其它字节，失败模式是非破坏性的；序列化失败是毁掉作者的页面。

**不用正则**：`</head>` 可能出现在注释或内联脚本的字符串里，正则会把插入点放到
那里面，等于没插 —— SDK 不加载，而页面看上去一切正常。`HTMLParser` 会把注释交给
`handle_comment`、把 `<script>` 内容按 CDATA 处理，两种情况都不会误判。

`HTMLParser` 是分词器而非验证器，不重建文档，正好取两者之长：定位准确，且不改写。

## 幂等

活动页自己写了 `<script src="/sdk/v1/cea.js">`（或任何带 `id="cea-sdk"` 的脚本
标签）就不再注入，两种写法都能工作。判断同样走解析器 —— 只看真正的 `<script>`
标签，因此"在注释里提了一句"不会被误判成已引用。

**用 `id` 而不是 `class` 标记注入的标签。** 两者都是全局属性、都合法；这里选
`id` 是因为语义更准：一份文档里只该有一个桥接脚本，`id` 的"唯一"正好对上
`class` 的"可多个"。它同时给活动页一个稳定的抓手 —— `getElementById('cea-sdk')`。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

#: 注入的标签会带上这个 id。既是幂等判断的依据，也让作者在 DevTools 里一眼
#: 看出这一行不是自己写的。
SDK_ELEMENT_ID = "cea-sdk"

#: 解析器不可用时的兜底。只在 `_scan` 返回 None 时使用。
_HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)
_BODY_OPEN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def sdk_tag(src: str) -> str:
    return f'<script src="{src}" id="{SDK_ELEMENT_ID}"></script>'


def _line_starts(text: str) -> list[int]:
    """每行起始的绝对偏移。

    与 `HTMLParser.getpos()` 的口径对齐：它按 `\\n` 分行，列号从行首算起。
    因此这里也只在 `\\n` 之后断开（CRLF 里的 `\\r` 归属上一行，与标准库一致）。
    """
    starts = [0]
    for index, char in enumerate(text):
        if char == "\n":
            starts.append(index + 1)
    return starts


class _DocumentScan(HTMLParser):
    """只做定位与判定，**不重建文档**。

    产出三个信息：
      - `head_end`：`</head>` 的绝对偏移，插入点首选
      - `body_content`：`<body ...>` 之后的内容起点，次选
      - `has_sdk_script`：是否已经存在真正的桥接脚本标签
    """

    def __init__(self, text: str, src: str) -> None:
        # convert_charrefs=False：不合并字符引用，避免影响偏移计算
        super().__init__(convert_charrefs=False)
        self._starts = _line_starts(text)
        self._src = src
        self.head_end: int | None = None
        self.body_content: int | None = None
        self.has_sdk_script = False

    def _offset(self) -> int:
        line, column = self.getpos()
        # getpos() 的行号是 1-based、列号是 0-based
        if 1 <= line <= len(self._starts):
            return self._starts[line - 1] + column
        return 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag == "script":
            if self._looks_like_sdk(attrs):
                self.has_sdk_script = True
            return

        if tag == "body" and self.body_content is None:
            raw = self.get_starttag_text() or ""
            self.body_content = self._offset() + len(raw)

    def handle_startendtag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "head" and self.head_end is None:
            # getpos() 在结束标签上指向 `<`，正是要插入的位置
            self.head_end = self._offset()

    def _looks_like_sdk(self, attrs) -> bool:  # type: ignore[no-untyped-def]
        """只看真正的 script 标签属性，因此注释里提到不算。"""
        expected = self._src.split("?")[0]
        for name, value in attrs:
            if name == "id" and value == SDK_ELEMENT_ID:
                return True
            if name == "src" and value:
                # 允许带查询串（?v=…），因此比较去掉查询串后的路径
                if value.split("?")[0].endswith(expected):
                    return True
        return False


def _scan(html: str, src: str) -> _DocumentScan | None:
    """跑一遍解析器。失败时返回 None，由调用方走兜底路径。"""
    scan = _DocumentScan(html, src)
    try:
        scan.feed(html)
        scan.close()
    except Exception:
        # HTMLParser 相当宽容，理论上不会走到这里；真走到了也不能让整页 500
        return None
    return scan


def has_sdk_reference(html: str, *, src: str = "/sdk/v1/cea.js") -> bool:
    """活动页是否已经自己引用了桥接脚本。

    走解析器而不是子串匹配：注释里写一句 `<script src="/sdk/v1/cea.js">` 不算
    引用，按子串判断会因此**跳过注入**，结果页面拿不到 SDK —— 而那个方向的
    误判代价更大。
    """
    scan = _scan(html, src)
    if scan is not None:
        return scan.has_sdk_script

    # 解析器都跑不起来时退回过松判断：宁可"以为已引用"也不重复插入
    lowered = html.lower()
    return SDK_ELEMENT_ID in lowered or src.lower() in lowered


def inject_sdk_tag(html: str, *, src: str) -> str:
    """把桥接脚本插进 HTML。

    插入位置按可靠性依次退让：
      1. `</head>` 之前 —— 最常见，也让 SDK 尽早开始监听
      2. `<body ...>` 之后 —— 没有 head 的片段式页面
      3. 正则兜底 —— 解析器没能给出位置
      4. 直接前置 —— 畸形到连兜底都匹配不上

    第 4 种情况下前置而不是追加：SDK 要在活动页自己的脚本之前就位，否则活动页
    里的 `CEA.ready` 会在 SDK 定义 `window.CEA` 之前求值。
    """
    tag = sdk_tag(src)
    scan = _scan(html, src)

    if scan is not None:
        if scan.head_end is not None:
            return html[: scan.head_end] + tag + html[scan.head_end :]
        if scan.body_content is not None:
            return html[: scan.body_content] + tag + html[scan.body_content :]
        return tag + html

    # 解析器不可用时的兜底，保持与旧实现一致
    head_close = _HEAD_CLOSE.search(html)
    if head_close:
        return html[: head_close.start()] + tag + html[head_close.start() :]

    body_open = _BODY_OPEN.search(html)
    if body_open:
        return html[: body_open.end()] + tag + html[body_open.end() :]

    return tag + html


def ensure_sdk(html: str, *, src: str) -> str:
    """需要时注入；已经有引用则原样返回。"""
    if has_sdk_reference(html, src=src):
        return html
    return inject_sdk_tag(html, src=src)


__all__ = [
    "SDK_ELEMENT_ID",
    "ensure_sdk",
    "has_sdk_reference",
    "inject_sdk_tag",
    "sdk_tag",
]
