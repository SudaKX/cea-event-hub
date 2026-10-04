"""邮件模板：任务 16.8 与 16.9。

样式本身靠肉眼看（`var/preview_emails.py` 导出、无头 Chrome 截图），断言管的是
那些**看不出来但会出事**的性质：两个版本都在、链接两边都有、用户填的东西被转义、
不引用任何外部资源，以及模板确实是从磁盘读进内存的。
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
        assert "如果按钮无法使用" in content.html
        assert "复制下方链接" in content.html

    def test_duration_appears_in_both_versions(self) -> None:
        # 有效期是用户唯一需要判断"还来不来得及"的信息，两边都得说
        content = templates.registration_email(
            display_name="张三", link=LINK, minutes=10
        )
        assert "10 分钟" in content.text
        assert "10 分钟" in content.html


class TestLoadingFromDisk:
    """任务 16.9：正文从独立文件读入内存。

    这一组盯的是"读入内存"这四个字本身 —— 它既是这次重构的目的（单独查看与编辑），
    也是一个会静默失效的性质（每次都读盘、或读了一次但读的是旧路径）。
    """

    def test_every_declared_file_exists(self) -> None:
        """清单里写死的文件必须都在。

        写死而不是扫目录：漏掉一个应当在启动时就炸，而不是等到某封信要发的时候。
        """
        for name in templates.TEMPLATE_FILES:
            path = templates.TEMPLATE_DIR / name
            assert path.is_file(), f"缺少模板文件 {path}"

    def test_load_all_returns_every_file(self) -> None:
        loaded = templates.load_all()
        assert set(loaded) == set(templates.TEMPLATE_FILES)
        # 逐个非空：空文件也会"成功载入"，然后发出一封空信
        for name, body in loaded.items():
            assert body.strip(), f"{name} 是空的"

    def test_templates_live_inside_the_package(self) -> None:
        """模板必须在**包内**。

        放到仓库根的 `templates/` 会在源码运行时正常、装成 wheel 之后找不到 ——
        那种失败只会在部署环境出现。
        """
        assert "app" in templates.TEMPLATE_DIR.parts
        assert templates.TEMPLATE_DIR.is_dir()

    def test_content_is_cached_in_memory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """载入之后改盘上的文件**不应**影响输出 —— 那才叫读进内存。"""
        templates.load_all()
        original = templates._get("registration.html")

        # 换掉盘上的内容，但不重新 load
        monkeypatch.setitem(templates._cache, "registration.html", original)
        rendered_before = templates.registration_email(
            display_name="张三", link=LINK, minutes=10
        ).html

        # 直接改缓存才有效果 —— 证明渲染走的是内存，不是每次读盘
        templates._cache["registration.html"] = "<p>换过了</p>"
        try:
            rendered_after = templates.registration_email(
                display_name="张三", link=LINK, minutes=10
            ).html
        finally:
            templates.load_all()

        assert "请点击下面的按钮以确认邮箱并完成注册" in rendered_before
        assert "换过了" in rendered_after
        assert "请点击下面的按钮以确认邮箱并完成注册" not in rendered_after

    def test_missing_file_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """缺文件必须抛错，不能悄悄少一封信。

        这里**不需要**在用例末尾重新载入：`load_all` 只在全部读成功之后才动缓存，
        失败的调用不会污染它。反过来，如果在 monkeypatch 尚未撤销时调用 `load_all`
        去"还原"，它只会照着被改过的清单再抛一次 —— 那是同义反复，不是还原。
        """
        monkeypatch.setattr(
            templates, "TEMPLATE_FILES", (*templates.TEMPLATE_FILES, "nope.html")
        )
        with pytest.raises(templates.TemplateMissing, match="nope.html"):
            templates.load_all()
        # 缓存没被破坏：原来的模板还在
        assert "请点击下面的按钮以确认邮箱并完成注册" in templates._get("registration.html")

    def test_startup_fails_loudly_when_a_template_is_missing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """装配应用时就炸，而不是等到有人注册。

        发信失败是被 `_safe_send` 吞掉的，接口照常返回 202 —— 缺模板如果拖到运行期
        才发现，表现就是"用户永远收不到信"，极难查。
        """
        from app.main import create_app

        monkeypatch.setattr(templates, "TEMPLATE_DIR", tmp_path)
        with pytest.raises(templates.TemplateMissing):
            create_app()
        # 同上：缓存仍是好的
        assert "请点击下面的按钮以确认邮箱并完成注册" in templates._get("registration.html")


class TestPlaceholders:
    """模板里写的变量必须都能被填上，且不留残渣。"""

    @pytest.mark.parametrize(
        "slug,values",
        [
            (
                "registration",
                {"display_name": "张三", "link": LINK, "minutes": "10"},
            ),
            (
                "password_reset",
                {"display_name": "张三", "link": LINK, "hours": "24"},
            ),
            ("email_verification", {"display_name": "张三", "link": LINK}),
        ],
    )
    def test_content_templates_substitute_cleanly(
        self, slug: str, values: dict[str, str]
    ) -> None:
        """写错一个变量名会在 `substitute` 时抛 `KeyError`，而不是留在信里。

        这正是用 `substitute` 而不是 `safe_substitute` 的目的 —— 用户不该收到一封
        写着 `$minuts` 的邮件。
        """
        filled = templates._fill(f"{slug}.html", values)
        assert "$" not in filled, f"{slug}.html 里还有没被替换的占位符"

    def test_shell_has_no_leftover_placeholders(self) -> None:
        html = templates.registration_email(
            display_name="张三", link=LINK, minutes=10
        ).html
        # 样式里的 `#d0202f` 之类不含 `$`；出现 `$` 就说明有占位符没填
        assert "$" not in html

    @pytest.mark.parametrize(
        "slug,values",
        [
            ("registration", {"display_name": "张三", "link": LINK, "minutes": "10"}),
            ("password_reset", {"display_name": "张三", "link": LINK, "hours": "24"}),
            ("email_verification", {"display_name": "张三", "link": LINK}),
        ],
    )
    def test_text_templates_substitute_cleanly(
        self, slug: str, values: dict[str, str]
    ) -> None:
        filled = templates._fill(f"{slug}.txt", values)
        assert "$" not in filled

    def test_a_dollar_in_a_value_is_not_re_substituted(self) -> None:
        """值里带 `$` 不该被当成占位符再替换一遍。

        `Template.substitute` 不会回头扫替换进去的内容 —— 这一条把那个前提钉住，
        因为一旦行为变了，用户填个 `$link` 之类的展示名就能把别的变量注进去。
        """
        content = templates.registration_email(
            display_name="$minutes $link", link=LINK, minutes=10
        )
        assert "$minutes $link" in content.text
        # HTML 里是转义后的原文，没被二次替换
        assert "$minutes $link" in content.html

    def test_all_three_messages_carry_the_link_from_the_shell(self) -> None:
        """链接由外壳统一渲染 —— 三封信都不该各自拼一遍。"""
        for content in (
            templates.registration_email(display_name="张三", link=LINK, minutes=10),
            templates.password_reset_email(display_name="张三", link=LINK, hours=24),
            templates.email_verification_email(display_name="张三", link=LINK),
        ):
            assert f'href="{LINK}"' in content.html
