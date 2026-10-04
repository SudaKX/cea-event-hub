"""邮件正文模板：从磁盘读入、在内存里渲染。

正文放在 `app/templates/email/` 下的**独立文件**里，而不是 Python 字符串里 —— 改
一处排版不必在读代码时数引号，编辑器也能给 HTML 高亮；`var/preview_emails.py`
把它们组装成完整邮件导出，可以直接在浏览器里看。

每个文件的分工：

```
shell.html                  外壳：结构、配色、排版。三封信共用，只此一份
registration.html           正文段落（嵌进外壳的 $body）
registration.txt            纯文本版本
```

**变量用 `string.Template` 的 `$name`**，不用 `str.format` —— 后者要写 `{{` 与 `}}`
转义，HTML 里花括号一多就非常容易出错。

**浅色为主。** 站点是近黑底（`--bg: #0b0b0d`），但邮件客户端对深色背景不友好：
Outlook（Windows）忽略 `background-color`，浅色文字落在白底上几乎看不见；Gmail 与
Apple Mail 的暗色模式还可能整封反色。品牌感改由**品牌红与等宽字体**承担。

**不放图片。** 多数客户端默认不加载远程图片，放了只是多一个空洞；邮件里的外链请求
本身还是个隐私面。**布局用表格**：Outlook 至今是 Word 的渲染引擎，单列卡片写成
`div` + flex 在那边会散架。

**这里只做字符串，不碰数据库也不碰 fastapi**（分层规则）：调用方把拼好的链接与
展示名传进来，模板只管排版。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html import escape
from pathlib import Path
from string import Template
from textwrap import indent

logger = logging.getLogger(__name__)

#: 模板目录。放在**包内**（而不是仓库根的 `templates/`），打包时才会跟着走
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"

#: 需要载入的文件清单。写死而不是扫目录：漏掉一个文件应当在启动时就炸，
#: 而不是等到某封信要发的时候
TEMPLATE_FILES = (
    "shell.html",
    "registration.html",
    "registration.txt",
    "password_reset.html",
    "password_reset.txt",
    "email_verification.html",
    "email_verification.txt",
)

#: 已读入的模板。**进程内只读一次**，启动时由 `load_all()` 填满
_cache: dict[str, str] = {}


class TemplateMissing(RuntimeError):
    """模板文件读不到。

    这是**打包或部署**的问题，不是运行期配置问题 —— 让它在启动时就炸，好过等到有人
    注册时才发现信发不出去。
    """


@dataclass(frozen=True)
class EmailContent:
    """一封信的两个版本。

    **两个都要发。** 只发 HTML 会被反垃圾系统扣分，而纯文本客户端的读者会看到一片
    空白；两半内容必须一致，链接也必须两边都有。
    """

    subject: str
    text: str
    html: str


def load_all() -> dict[str, str]:
    """把模板全部读进内存。启动时调用一次。

    读不到就抛 `TemplateMissing`，**不吞异常**：缺模板意味着每一封邮件都发不出去，
    而那正是"发信失败不影响接口结果"这条取舍最难查的一面（接口照常返回 202）。
    """
    loaded: dict[str, str] = {}
    for name in TEMPLATE_FILES:
        path = TEMPLATE_DIR / name
        try:
            # 显式 utf-8：模板里有中文，靠平台默认编码在 Windows 上会读成乱码
            loaded[name] = path.read_text(encoding="utf-8")
        except OSError as error:
            raise TemplateMissing(f"读不到邮件模板 {path}") from error

    _cache.clear()
    _cache.update(loaded)
    logger.debug("邮件模板已载入内存：%d 个", len(loaded))
    return loaded


def _get(name: str) -> str:
    """取模板。没载入过就现载一次（便于脚本与测试直接用）。"""
    if not _cache:
        load_all()
    try:
        return _cache[name]
    except KeyError as error:  # pragma: no cover - load_all 保证了齐全
        raise TemplateMissing(f"模板 {name} 未载入") from error


def _fill(name: str, values: dict[str, str]) -> str:
    """替换 `$name`。

    用 `substitute` 而不是 `safe_substitute`：模板里写错一个变量名应当当场炸出来，
    而不是在用户收到的信里留下一个 `$minuts`。
    """
    return Template(_get(name)).substitute(values)


def _wrap(*, subject: str, heading: str, body: str, link: str,
          button_label: str, footer: str) -> str:
    """把正文段落嵌进共用外壳。"""
    return _fill(
        "shell.html",
        {
            "subject": escape(subject),
            "heading": heading,
            "body": indent(body.strip(), "      "),
            "link": escape(link, quote=True),
            "button_label": button_label,
            "footer": footer,
        },
    )


def registration_email(*, display_name: str, link: str, minutes: int) -> EmailContent:
    """注册核销。这封信是两阶段注册的第二步所依赖的那一步。"""
    subject = "完成注册"
    # **展示名是用户自己填的**，进 HTML 前必须转义；纯文本那半用原始值 ——
    # 那里 `&lt;` 会原样显示出来，反而更糟
    html = _wrap(
        subject=subject,
        heading="完成注册",
        body=_fill(
            "registration.html",
            {
                "display_name": escape(display_name),
                "link": escape(link, quote=True),
                "minutes": str(minutes),
            },
        ),
        link=link,
        button_label="完成注册",
        footer="不是你发起的？忽略本邮件即可，账号不会被创建。",
    )
    text = _fill(
        "registration.txt",
        {"display_name": display_name, "link": link, "minutes": str(minutes)},
    )
    return EmailContent(subject=subject, text=text, html=html)


def password_reset_email(*, display_name: str, link: str, hours: int) -> EmailContent:
    """密码重置。"""
    subject = "重置密码"
    html = _wrap(
        subject=subject,
        heading="重置密码",
        body=_fill(
            "password_reset.html",
            {
                "display_name": escape(display_name),
                "link": escape(link, quote=True),
                "hours": str(hours),
            },
        ),
        link=link,
        button_label="设置新密码",
        footer="不是你发起的？忽略本邮件即可，密码不会改变。",
    )
    text = _fill(
        "password_reset.txt",
        {"display_name": display_name, "link": link, "hours": str(hours)},
    )
    return EmailContent(subject=subject, text=text, html=html)


def email_verification_email(*, display_name: str, link: str) -> EmailContent:
    """邮箱绑定验证。"""
    subject = "确认邮箱"
    html = _wrap(
        subject=subject,
        heading="确认邮箱",
        body=_fill(
            "email_verification.html",
            {
                "display_name": escape(display_name),
                "link": escape(link, quote=True),
            },
        ),
        link=link,
        button_label="确认邮箱",
        footer="不是你发起的？忽略本邮件即可。",
    )
    text = _fill(
        "email_verification.txt", {"display_name": display_name, "link": link}
    )
    return EmailContent(subject=subject, text=text, html=html)


__all__ = [
    "TEMPLATE_DIR",
    "TEMPLATE_FILES",
    "EmailContent",
    "TemplateMissing",
    "email_verification_email",
    "load_all",
    "password_reset_email",
    "registration_email",
]
