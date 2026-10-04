"""邮件正文模板。

三封信共用一套外壳，差别只在标题、正文、按钮文案与落款。

**浅色为主。** 站点是近黑底（`--bg: #0b0b0d`），但邮件客户端对深色背景不友好：
Outlook（Windows）会忽略 `background-color`，于是浅色文字落在白底上几乎看不见；
Gmail 与 Apple Mail 的暗色模式还可能把整封信自动反色。品牌感因此改由**品牌红与
等宽字体**承担 —— 这两样在任何客户端都稳。

**不放图片。** 多数客户端默认不加载远程图片，放了也只是多一个空洞；站点那套点阵
纹理在邮件里也做不出来（不支持 `background-image`，而且项目本身禁渐变）。排版、
颜色与一条细边已经足够。

**布局用表格。** 不是为了兼容 2005 年，而是因为 Outlook 至今用 Word 的渲染引擎：
单列卡片用 `<table>` 是最省心的写法，用 `div` + flex 在那边会散架。

**这里只做字符串，不碰数据库也不碰 fastapi**（分层规则）：调用方把已经拼好的链接
与展示名传进来，模板只负责排版。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

#: 与前端设计令牌同源。邮件里只能用字面量 —— 客户端不认识 CSS 变量
RED = "#d0202f"
INK = "#1a1a1c"
BODY = "#3f4249"
MUTE = "#8b8e97"
LINE = "#e4e4e7"
SOFT_LINE = "#ececee"
PAGE = "#f2f2f3"

FONT = (
    "-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',"
    "'Hiragino Sans GB','Microsoft YaHei',sans-serif"
)
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Courier New',monospace"

#: 邮件宽度上限。600px 是长期以来的稳妥值：再宽在阅读窗格里会被裁
WIDTH = 600


@dataclass(frozen=True)
class EmailContent:
    """一封信的两个版本。

    **两个都要发。** 只发 HTML 会被反垃圾系统扣分，而纯文本客户端的读者会看到一片
    空白；两半内容必须一致，链接也必须两边都有。
    """

    subject: str
    text: str
    html: str


def _shell(
    *,
    subject: str,
    heading: str,
    paragraphs: list[str],
    button_label: str,
    link: str,
    footer: str,
) -> str:
    """套上共用外壳。

    `paragraphs` 与 `footer` 里的文本由调用方保证已是安全的 HTML —— 全是本模块
    自己写的字面量。**任何来自用户的值都必须先经 `escape`**，见
    `registration_email` 里对展示名的处理。
    """
    safe_link = escape(link, quote=True)
    body = "\n".join(
        f'<p style="margin:0 0 12px;font-size:14px;line-height:1.75;color:{BODY};">'
        f"{paragraph}</p>"
        for paragraph in paragraphs
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:24px 12px;background:{PAGE};font-family:{FONT};color:{INK};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
<tr><td align="center">
  <table role="presentation" width="{WIDTH}" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:{WIDTH}px;border-collapse:collapse;background:#ffffff;border:1px solid {LINE};border-radius:10px;">
    <tr><td style="padding:26px 30px 0;">
      <p style="margin:0;font-size:12px;letter-spacing:.14em;color:{MUTE};">CEA<span style="color:{RED};">/</span> 社团活动平台</p>
    </td></tr>
    <tr><td style="padding:16px 30px 0;">
      <h1 style="margin:0;font-size:21px;line-height:1.45;font-weight:600;color:{INK};">{heading}</h1>
    </td></tr>
    <tr><td style="padding:14px 30px 0;">
      {body}
    </td></tr>
    <tr><td style="padding:10px 30px 0;">
      <a href="{safe_link}" style="display:inline-block;padding:12px 22px;background:{RED};color:#ffffff;font-size:14px;font-weight:600;text-decoration:none;border-radius:7px;">{button_label}</a>
    </td></tr>
    <tr><td style="padding:18px 30px 0;font-size:12px;line-height:1.7;color:{MUTE};">
      按钮点不动就复制这个地址：<br>
      <span style="font-family:{MONO};font-size:12px;color:{BODY};word-break:break-all;">{safe_link}</span>
    </td></tr>
    <tr><td style="padding:18px 30px 24px;">
      <div style="border-top:1px solid {SOFT_LINE};padding-top:14px;font-size:12px;line-height:1.7;color:{MUTE};">
        {footer}
      </div>
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>
"""


def registration_email(
    *, display_name: str, link: str, minutes: int
) -> EmailContent:
    """注册核销。这封信是两阶段注册的第二步所依赖的那一步。"""
    # **展示名是用户自己填的**，进 HTML 前必须转义。纯文本时它只是排版问题，
    # 有了 HTML 就是注入问题
    name = escape(display_name)
    subject = "完成注册"
    text = (
        f"你好 {display_name}：\n\n"
        "请打开下面的链接完成注册：\n\n"
        f"{link}\n\n"
        f"链接 {minutes} 分钟内有效，且只能使用一次。"
        "在链接被打开之前，账号尚未创建。\n"
        "如果不是你发起的，忽略本邮件即可。\n"
    )
    html = _shell(
        subject=subject,
        heading="完成注册",
        paragraphs=[
            f"你好 {name}：请点下面的按钮确认这个邮箱，账号就建好了。",
            f"链接 <strong>{minutes} 分钟内</strong>有效，且只能使用一次。"
            "在链接被打开之前，账号尚未创建。",
        ],
        button_label="完成注册",
        link=link,
        footer="不是你发起的？忽略本邮件即可，账号不会被创建。",
    )
    return EmailContent(subject=subject, text=text, html=html)


def password_reset_email(
    *, display_name: str, link: str, hours: int
) -> EmailContent:
    """口令重置。"""
    name = escape(display_name)
    subject = "重置密码"
    text = (
        f"你好 {display_name}：\n\n"
        "有人请求重置该账号的密码。如果这是你本人，请打开下面的链接：\n\n"
        f"{link}\n\n"
        f"链接 {hours} 小时内有效，且只能使用一次。"
        "如果不是你发起的，忽略本邮件即可。\n"
    )
    html = _shell(
        subject=subject,
        heading="重置密码",
        paragraphs=[
            f"你好 {name}：有人请求重置这个账号的密码。",
            f"链接 <strong>{hours} 小时内</strong>有效，且只能使用一次。",
        ],
        button_label="设置新密码",
        link=link,
        footer="不是你发起的？忽略本邮件即可，密码不会改变。",
    )
    return EmailContent(subject=subject, text=text, html=html)


def email_verification_email(*, display_name: str, link: str) -> EmailContent:
    """邮箱绑定验证。"""
    name = escape(display_name)
    subject = "确认邮箱"
    text = (
        f"你好 {display_name}：\n\n"
        "请打开下面的链接确认该邮箱地址：\n\n"
        f"{link}\n\n"
        "如果不是你发起的，忽略本邮件即可。\n"
    )
    html = _shell(
        subject=subject,
        heading="确认邮箱",
        paragraphs=[f"你好 {name}：请点下面的按钮确认这个邮箱地址。"],
        button_label="确认邮箱",
        link=link,
        footer="不是你发起的？忽略本邮件即可。",
    )
    return EmailContent(subject=subject, text=text, html=html)


__all__ = [
    "EmailContent",
    "email_verification_email",
    "password_reset_email",
    "registration_email",
]
