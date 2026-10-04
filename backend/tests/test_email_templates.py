"""邮件模板：任务 16.8。

样式本身靠肉眼看（`var/preview_emails.py` 导出、无头 Chrome 截图），断言管的是
那些**看不出来但会出事**的性质：两个版本都在、链接两边都有、用户填的东西被转义、
以及不引用任何外部资源。
"""

from __future__ import annotations

import re

import pytest

from app.services import email_templates as templates

LINK = "http://localhost:5173/verify-registration?token=abc123"


def all_templates() -> list[tuple[str, templates.EmailContent]]:
    """三封信各来一封，供参数化的整体性断言使用。"""
    return [
        (
            "注册",
            templates.registration_email(display_name="张三", link=LINK, minutes=10),
        ),
        (
            "重置",
            templates.password_reset_email(display_name="张三", link=LINK, hours=24),
        ),
        (
            "验证",
            templates.email_verification_email(display_name="张三", link=LINK),
        ),
    ]


class TestBothVersionsExist:
    @pytest.mark.parametrize("name,content", all_templates())
    def test_has_text_and_html(self, name: str, content: templates.EmailContent) -> None:
        """**两个都要有。**

        只发 HTML 会被反垃圾系统扣分，而纯文本客户端的读者会看到一片空白。
        """
        assert content.subject
        assert content.text.strip()
        assert "<html" in content.html

    @pytest.mark.parametrize("name,content", all_templates())
    def test_link_is_in_both_versions(
        self, name: str, content: templates.EmailContent
    ) -> None:
        """链接是这封信**唯一**的用处，两边都必须有。"""
        assert LINK in content.text
        assert LINK in content.html


class TestEscaping:
    def test_display_name_cannot_inject_markup(self) -> None:
        """展示名是用户自己填的。

        纯文本时它只是排版问题；有了 HTML 就是注入 —— 用户能在平台发出的邮件里
        塞进任意标记。
        """
        content = templates.registration_email(
            display_name='<img src=x onerror=alert(1)>',
            link=LINK,
            minutes=10,
        )
        assert "<img src=x onerror=alert(1)>" not in content.html
        assert "&lt;img src=x onerror=alert(1)&gt;" in content.html

    def test_script_tag_is_neutralised(self) -> None:
        content = templates.registration_email(
            display_name="<script>alert(1)</script>", link=LINK, minutes=10
        )
        assert "<script>" not in content.html
        assert "&lt;script&gt;" in content.html

    def test_text_version_keeps_the_raw_name(self) -> None:
        """纯文本不做转义 —— 那里 `&lt;` 会原样显示出来，反而更糟。"""
        content = templates.registration_email(
            display_name="<b>张三</b>", link=LINK, minutes=10
        )
        assert "<b>张三</b>" in content.text

    def test_quotes_in_the_link_do_not_break_the_attribute(self) -> None:
        content = templates.registration_email(
            display_name="张三", link='http://x/?t="onmouseover="alert(1)', minutes=10
        )
        assert '"onmouseover="alert(1)' not in content.html
        assert "&quot;onmouseover=&quot;alert(1)" in content.html


class TestNoExternalResources:
    """不引用任何外部资源。

    多数客户端默认不加载远程图片，引用了也只是多一个空洞；而邮件里的外链请求还是
    一个隐私面（追踪像素就是这么工作的）。这一条把"不放图"的决定钉住。
    """

    @pytest.mark.parametrize("name,content", all_templates())
    def test_no_images_or_stylesheets(
        self, name: str, content: templates.EmailContent
    ) -> None:
        lowered = content.html.lower()
        assert "<img" not in lowered
        assert "<link" not in lowered
        assert "background-image" not in lowered
        assert "@import" not in lowered
        assert "<script" not in lowered

    @pytest.mark.parametrize("name,content", all_templates())
    def test_style_is_inline_only(
        self, name: str, content: templates.EmailContent
    ) -> None:
        """只用行内样式。

        客户端（尤其 Gmail）会剥掉 `<style>` 块与外部样式表，靠类名的排版到了那边
        就没了 —— 那是一种"本地看着好好的"的坏法。
        """
        assert "<style" not in content.html.lower()
        assert "style=" in content.html

    @pytest.mark.parametrize("name,content", all_templates())
    def test_the_only_url_is_the_action_link(
        self, name: str, content: templates.EmailContent
    ) -> None:
        # 允许 elia.cn 之类的品牌域名出现在文字里，但 href 只能有一个
        hrefs = re.findall(r'href="([^"]+)"', content.html)
        assert hrefs == [LINK]


class TestLayout:
    @pytest.mark.parametrize("name,content", all_templates())
    def test_uses_a_presentation_table(
        self, name: str, content: templates.EmailContent
    ) -> None:
        """单列卡片用表格。

        Outlook（Windows）至今用 Word 的渲染引擎，`div` + flex 在那边会散架。
        `role="presentation"` 让读屏软件知道这不是数据表格。
        """
        assert '<table role="presentation"' in content.html

    @pytest.mark.parametrize("name,content", all_templates())
    def test_declares_language_and_charset(
        self, name: str, content: templates.EmailContent
    ) -> None:
        assert 'lang="zh-CN"' in content.html
        assert '<meta charset="utf-8">' in content.html

    @pytest.mark.parametrize("name,content", all_templates())
    def test_has_a_copyable_fallback_url(
        self, name: str, content: templates.EmailContent
    ) -> None:
        """按钮点不动时得有条退路 —— 把地址原样写在下面。"""
        assert "复制这个地址" in content.html

    def test_duration_appears_in_both_versions(self) -> None:
        # 有效期是用户唯一需要判断"还来不来得及"的信息，两边都得说
        content = templates.registration_email(
            display_name="张三", link=LINK, minutes=10
        )
        assert "10 分钟" in content.text
        assert "10 分钟" in content.html
