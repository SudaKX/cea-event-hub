"""桥接脚本的注入与托管行为。

两条约束必须同时成立：
  - 活动页**自己写了**引用时，不得重复注入（幂等）
  - 活动页**没写**时，返回的 HTML 里必须有那一行

以及一条容易写错的：注入会让内容变长，**不能沿用原来的 Content-Length**，
否则响应会被截断。
"""

from __future__ import annotations

import pytest

from app.core.enums import EventStatus
from app.db.models import Event
from app.services.content_inject import (
    SDK_ELEMENT_ID,
    ensure_sdk,
    has_sdk_reference,
    inject_sdk_tag,
    sdk_tag,
)

API = "/api/v1"
ADMIN = f"{API}/admin"


def _seed_event(test_db, event_id="spring-2026", **overrides) -> None:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.LIVE.value,
    }
    defaults.update(overrides)
    with test_db.session() as session:
        session.add(Event(**defaults))  # type: ignore[arg-type]


def _write(content_root, event_id: str, name: str, body: str) -> None:
    target = content_root / "content" / event_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


class TestPureInjection:
    def test_tag_carries_the_id(self) -> None:
        # id 与 class 都是全局属性、都合法。这里选 id 是因为语义更准：
        # 一份文档里只该有一个桥接脚本，id 的"唯一"正好对上 class 的"可多个"。
        tag = sdk_tag("/sdk/v1/cea.js")
        assert f'id="{SDK_ELEMENT_ID}"' in tag
        assert 'src="/sdk/v1/cea.js"' in tag

    def test_detects_id_reference(self) -> None:
        assert has_sdk_reference(f'<script src="/other.js" id="{SDK_ELEMENT_ID}"></script>')

    def test_src_alone_is_not_a_reference(self) -> None:
        """**判断只看 id。**

        路径可能变（/sdk/v1/ → /sdk/v2/），按 src 判断的话，SDK 换路径后所有
        老页面都会被判定成"没有引用"。所以只带 src 的标签不算数 —— 不做迁移，
        老页面由人工改成带 id 的形态。
        """
        assert not has_sdk_reference('<script src="/sdk/v1/cea.js"></script>')
        assert not has_sdk_reference('<script src="/sdk/v1/cea.js?v=2"></script>')

    def test_src_pointing_elsewhere_is_also_not_a_reference(self) -> None:
        assert not has_sdk_reference('<script src="/sdk/v1/other.js"></script>')

    def test_plain_page_has_no_reference(self) -> None:
        assert not has_sdk_reference("<html><head></head><body>hi</body></html>")

    # ------------------------------------------------------------------
    # 这两组是"用解析器而不是正则"的全部理由。正则会把插入点放进注释或脚本
    # 字符串里 —— 标签等于没插，SDK 不加载，而页面看上去一切正常。
    # ------------------------------------------------------------------

    def test_head_close_inside_a_comment_is_ignored(self) -> None:
        html = (
            "<html><head>"
            "<!-- 说明：</head> 这串字出现在注释里 -->"
            "<title>t</title>"
            "</head><body>hi</body></html>"
        )
        result = inject_sdk_tag(html, src="/sdk/v1/cea.js")

        # 必须插在真正的 </head> 之前，也就是注释结束之后
        assert result.index(SDK_ELEMENT_ID) > result.index("-->")
        assert result.index(SDK_ELEMENT_ID) < result.rindex("</head>")
        assert "<!-- 说明：</head> 这串字出现在注释里 -->" in result

    def test_head_close_inside_a_script_string_is_ignored(self) -> None:
        html = (
            "<html><head>"
            "<script>var s = \"</head>\";</script>"
            "<title>t</title>"
            "</head><body>hi</body></html>"
        )
        result = inject_sdk_tag(html, src="/sdk/v1/cea.js")

        # 插在真正的 </head> 前，而不是脚本字符串那个假的之后
        assert result.index(SDK_ELEMENT_ID) < result.rindex("</head>")
        assert result.index(SDK_ELEMENT_ID) > result.index("</script>")

    def test_reference_only_in_a_comment_still_injects(self) -> None:
        """注释里提了一句不算引用。

        按子串判断会因此**跳过注入**，页面拿不到 SDK —— 这个方向的误判代价更大。
        """
        html = (
            "<html><head>"
            "<!-- 记得写 <script src=\"/sdk/v1/cea.js\"></script> -->"
            "</head><body>hi</body></html>"
        )
        assert not has_sdk_reference(html)
        assert SDK_ELEMENT_ID in inject_sdk_tag(html, src="/sdk/v1/cea.js")

    def test_id_reference_in_a_comment_does_not_count(self) -> None:
        html = f'<html><head><!-- id="{SDK_ELEMENT_ID}" --></head><body>x</body></html>'
        assert not has_sdk_reference(html)

    def test_reference_with_query_string_is_detected(self) -> None:
        assert has_sdk_reference(
            f'<script src="/sdk/v1/cea.js?v=2" id="{SDK_ELEMENT_ID}"></script>'
        )

    def test_unrelated_script_is_not_a_reference(self) -> None:
        assert not has_sdk_reference('<script src="/sdk/v1/other.js"></script>')

    def test_src_only_page_gets_a_second_tag(self) -> None:
        """不做迁移的直接后果，写下来免得日后被当成 bug。

        只带 src 的老写法会被判定成"没有引用"，因而得到一个注入的新标签 ——
        结果是 SDK 加载两次。这是刻意接受的：与其在代码里维护一条迁移路径，
        不如让老页面显式改过来（示例内容已经改好）。
        """
        html = '<html><head><script src="/sdk/v1/cea.js"></script></head><body>x</body></html>'
        result = ensure_sdk(html, src="/sdk/v1/cea.js")

        assert result.count("cea.js") == 2
        assert f'id="{SDK_ELEMENT_ID}"' in result

    def test_inserts_before_head_close(self) -> None:
        html = "<html><head><title>t</title></head><body>hi</body></html>"
        result = inject_sdk_tag(html, src="/sdk/v1/cea.js")

        assert result.index(SDK_ELEMENT_ID) < result.index("</head>")
        # 原内容一字不动
        assert "<title>t</title>" in result
        assert result.endswith("</body></html>")

    def test_head_close_is_case_insensitive(self) -> None:
        result = inject_sdk_tag("<HTML><HEAD></HEAD><BODY>x</BODY></HTML>", src="/s.js")
        assert SDK_ELEMENT_ID in result
        assert result.upper().index("CEA-SDK") < result.upper().index("</HEAD>")

    def test_falls_back_to_body_open(self) -> None:
        # 片段式页面没有 head
        result = inject_sdk_tag("<body class='x'>hi</body>", src="/s.js")
        assert result.startswith("<body class='x'>")
        assert SDK_ELEMENT_ID in result

    def test_falls_back_to_prepend(self) -> None:
        # 畸形到 head/body 都没有：前置而不是追加 —— SDK 必须先于活动页自己的
        # 脚本就位，否则 CEA.ready 会在 window.CEA 定义之前求值
        result = inject_sdk_tag("<div>hi</div>", src="/s.js")
        assert result.startswith("<script")
        assert result.endswith("<div>hi</div>")

    def test_ensure_is_idempotent(self) -> None:
        html = (
            '<html><head><script src="/sdk/v1/cea.js" id="cea-sdk"></script></head>'
            "<body>hi</body></html>"
        )
        assert ensure_sdk(html, src="/sdk/v1/cea.js") == html

    def test_ensure_injects_once(self) -> None:
        html = "<html><head></head><body>hi</body></html>"
        once = ensure_sdk(html, src="/sdk/v1/cea.js")
        twice = ensure_sdk(once, src="/sdk/v1/cea.js")
        assert once == twice
        assert once.count(SDK_ELEMENT_ID) == 1


class TestServedHtml:
    def test_script_is_injected_when_absent(
        self, client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        _write(content_root, "spring-2026", "index.html", "<html><head></head><body>hi</body></html>")

        body = client.get("/content/spring-2026/index.html").text
        assert SDK_ELEMENT_ID in body
        assert "/sdk/v1/cea.js" in body

    def test_explicit_reference_is_left_alone(
        self, client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        # 显式引用必须带 id —— 判断只看 id
        explicit = (
            '<html><head><script src="/sdk/v1/cea.js" id="cea-sdk"></script>'
            "</head><body>hi</body></html>"
        )
        _write(content_root, "spring-2026", "index.html", explicit)

        body = client.get("/content/spring-2026/index.html").text
        assert body == explicit
        # 只出现一次，没有被重复注入
        assert body.count("cea.js") == 1
        assert body.count(SDK_ELEMENT_ID) == 1

    def test_injection_can_be_disabled(
        self, test_db, content_root, monkeypatch
    ) -> None:
        """关掉就回到"必须显式引用"的旧行为。

        注意开关是**在 create_app 时读取**的（挂载时把值传给了 ContentStaticFiles），
        所以必须在打补丁**之后**再建 app —— 对已经建好的 app 打补丁不会有任何效果。
        """
        from fastapi.testclient import TestClient

        from app.core.config import settings as global_settings
        from app.main import create_app

        _seed_event(test_db)
        _write(content_root, "spring-2026", "index.html", "<html><head></head><body>hi</body></html>")

        monkeypatch.setattr(global_settings, "CONTENT_SDK_INJECT", False)
        application = create_app()
        application.state.database = test_db
        with TestClient(application) as plain:
            body = plain.get("/content/spring-2026/index.html").text
        assert SDK_ELEMENT_ID not in body

    def test_non_html_is_untouched(self, client, test_db, content_root) -> None:
        _seed_event(test_db)
        _write(content_root, "spring-2026", "data.json", '{"a": 1}')

        body = client.get("/content/spring-2026/data.json").text
        assert body == '{"a": 1}'

    def test_content_length_matches_injected_body(
        self, client, test_db, content_root
    ) -> None:
        """注入会让内容变长；沿用原来的 Content-Length 会把响应截断。"""
        _seed_event(test_db)
        _write(content_root, "spring-2026", "index.html", "<html><head></head><body>hi</body></html>")

        response = client.get("/content/spring-2026/index.html")
        declared = int(response.headers["content-length"])
        assert declared == len(response.content)
        assert declared > len("<html><head></head><body>hi</body></html>")

    def test_etag_is_preserved_for_conditional_requests(
        self, client, test_db, content_root
    ) -> None:
        _seed_event(test_db)
        _write(content_root, "spring-2026", "index.html", "<html><head></head><body>hi</body></html>")

        response = client.get("/content/spring-2026/index.html")
        assert response.headers.get("etag")

    def test_cors_and_cache_headers_survive_rebuild(
        self, client, test_db, content_root
    ) -> None:
        """重建响应后，两条托管策略的头必须仍然在。"""
        _seed_event(test_db)
        _write(content_root, "spring-2026", "index.html", "<html><head></head><body>hi</body></html>")

        plain = client.get("/content/spring-2026/index.html")
        assert plain.headers["access-control-allow-origin"] == "*"
        assert plain.headers["cache-control"] == "no-cache"

        versioned = client.get("/content/spring-2026/index.html?v=3")
        assert "immutable" in versioned.headers["cache-control"]
        assert SDK_ELEMENT_ID in versioned.text

    def test_utf8_content_is_not_mangled(self, client, test_db, content_root) -> None:
        _seed_event(test_db)
        _write(
            content_root,
            "spring-2026",
            "index.html",
            "<html><head><title>春季招新</title></head><body>你好</body></html>",
        )

        body = client.get("/content/spring-2026/index.html").text
        assert "春季招新" in body
        assert "你好" in body
        assert SDK_ELEMENT_ID in body

    def test_missing_file_still_404s(self, client, test_db) -> None:
        _seed_event(test_db)
        assert client.get("/content/spring-2026/nope.html").status_code == 404

    def test_deployed_package_gets_injected_end_to_end(
        self, admin_client, client, test_db
    ) -> None:
        """走真实投放路径：上传的 zip 里没有 SDK，取回来必须有。"""
        import io
        import zipfile

        _seed_event(test_db)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "index.html",
                "<html><head><title>招新</title></head><body><form></form></body></html>",
            )
        deployed = admin_client.post(
            f"{ADMIN}/events/spring-2026/content",
            files={"file": ("c.zip", buffer.getvalue(), "application/zip")},
        )
        assert deployed.status_code == 201

        body = client.get("/content/spring-2026/index.html").text
        assert SDK_ELEMENT_ID in body
        assert "<title>招新</title>" in body
        assert "<form></form>" in body
