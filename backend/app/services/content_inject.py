"""活动内容 HTML 的桥接脚本注入。

**为什么在服务端做，而不是让宿主注入。** 宿主够不到 iframe 内部：不透明源下
`iframe.contentDocument` 抛 SecurityError，所以它无法替活动页插入 `<script>`。
postMessage 也自举不了 —— 要"收到地址再加载"，活动页得先有代码在监听。因此唯一
能去掉"作者必须记得写一行"这个约定的位置，是**托管内容的那一层**。

这不违反"宿主不得向 iframe 注入脚本"那条约束：注入发生在内容服务端，返回的是
另一份字节；宿主仍然碰不到 iframe 的文档，沙箱边界没有任何变化。

## 判断只看 `id`

一份文档里"桥接脚本是否就位"的**唯一判据**是存在 `id="cea-sdk"` 的 `<script>`。

`src` **不参与判断**。理由是路径可能变（`/sdk/v1/` → `/sdk/v2/`），而 id 是稳定的：
按 src 判断的话，SDK 换路径后所有老页面都会被判定成"没有引用"。

**不做迁移。** 早期指南里写的是 `<script src="/sdk/v1/cea.js"></script>`（没有 id），
这种形态**不特殊处理** —— 它会被判定成"没有引用"，因而得到一个注入的新标签。要避免
重复加载，就把老页面手工改成带 id 的形态；示例内容已经改好。

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
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

#: 桥接脚本标签的 id。它是"SDK 是否就位"的唯一判据，也让作者在 DevTools 里
#: 一眼看出这一行不是自己写的。
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
    """只做定位与判定，**不重建文档**。"""

    def __init__(self, text: str, src: str) -> None:
        # convert_charrefs=False：不合并字符引用，避免影响偏移计算
        super().__init__(convert_charrefs=False)
        self._starts = _line_starts(text)
        self._src = src

        #: `</head>` 的绝对偏移 —— 插入新标签的首选位置
        self.head_end: int | None = None
        #: `<body ...>` 之后的内容起点 —— 次选
        self.body_content: int | None = None
        #: 是否存在 id="cea-sdk" 的脚本。**这是唯一的判断依据。**
        self.has_sdk_id = False
        #: 存在 src 指向 SDK 的脚本。仅用于诊断提示，**不参与判断**。
        self.sdk_by_src = False

    def _offset(self) -> int:
        line, column = self.getpos()
        # getpos() 的行号是 1-based、列号是 0-based
        if 1 <= line <= len(self._starts):
            return self._starts[line - 1] + column
        return 0

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        if tag == "script":
            self._inspect_script(attrs)
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

    def _inspect_script(self, attrs) -> None:  # type: ignore[no-untyped-def]
        element_id: str | None = None
        matches_src = False
        expected = self._src.split("?")[0]

        for name, value in attrs:
            if name == "id":
                element_id = value
            elif name == "src" and value:
                # 允许带查询串（?v=…），因此比较去掉查询串后的路径
                if value.split("?")[0].endswith(expected):
                    matches_src = True

        if element_id == SDK_ELEMENT_ID:
            self.has_sdk_id = True
            return

        # 记下来只为了在需要时报出更有用的信息；判断不依赖它
        if matches_src:
            self.sdk_by_src = True


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
    """桥接脚本是否已就位。

    **只看 `id="cea-sdk"`**，不看 src。理由见模块开头：路径会变，id 不会。
    """
    scan = _scan(html, src)
    if scan is not None:
        return scan.has_sdk_id

    # 解析器都跑不起来时的退路：按 id 做子串匹配。仍然不看 src。
    return SDK_ELEMENT_ID in html.lower()


def ensure_sdk(html: str, *, src: str) -> str:
    """确保 HTML 里有带 id 的桥接脚本标签。"""
    scan = _scan(html, src)

    if scan is not None:
        if scan.has_sdk_id:
            # 已就位，原样返回
            return html
        return _insert(html, scan.head_end, scan.body_content, src)

    # 解析器不可用：退回到不解析的做法
    if SDK_ELEMENT_ID in html.lower():
        return html
    head_close = _HEAD_CLOSE.search(html)
    if head_close:
        return _insert(html, head_close.start(), None, src)
    body_open = _BODY_OPEN.search(html)
    if body_open:
        return _insert(html, None, body_open.end(), src)
    return sdk_tag(src) + html


def _insert(
    html: str, head_end: int | None, body_content: int | None, src: str
) -> str:
    """插入新标签。

    位置按可靠性依次退让：`</head>` 之前 -> `<body ...>` 之后 -> 直接前置。

    最后一种前置而不是追加：SDK 要在活动页自己的脚本之前就位，否则活动页里的
    `CEA.ready` 会在 SDK 定义 `window.CEA` 之前求值。
    """
    tag = sdk_tag(src)
    if head_end is not None:
        return html[:head_end] + tag + html[head_end:]
    if body_content is not None:
        return html[:body_content] + tag + html[body_content:]
    return tag + html


def inject_sdk_tag(html: str, *, src: str) -> str:
    """无条件确保有带 id 的标签（不做"是否已引用"的判断）。

    保留这个名字是因为测试与文档都在用；语义上它就是 `ensure_sdk` 的别名。
    """
    return ensure_sdk(html, src=src)


__all__ = [
    "SDK_ELEMENT_ID",
    "ensure_sdk",
    "has_sdk_reference",
    "inject_sdk_tag",
    "sdk_tag",
]
