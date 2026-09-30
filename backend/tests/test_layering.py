"""任务 3.2：分层纪律由测试强制，而不是停留在口头约定。

依赖方向必须是单向的：`api -> services -> repositories -> models`。
任何一层反向依赖都会让"能脱离 HTTP 单测"这个性质悄悄消失。
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
APP_DIR = BACKEND_DIR / "app"

WEB_FRAMEWORKS = {"fastapi", "starlette"}


def _imported_roots(path: Path) -> set[str]:
    """收集一个模块顶层 import 的包名（只看绝对导入的根包）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def _relatives(path: Path) -> str:
    return str(path.relative_to(BACKEND_DIR)).replace("\\", "/")


def test_services_do_not_import_web_framework() -> None:
    """service 抛领域异常，由 api 层的处理器翻译成 HTTP。

    一旦 service import 了 fastapi，它就会开始抛 HTTPException，业务规则与传输
    协议也就绑死了。
    """
    offenders = [
        f"{_relatives(path)}: {sorted(bad)}"
        for path in sorted((APP_DIR / "services").rglob("*.py"))
        if (bad := _imported_roots(path) & WEB_FRAMEWORKS)
    ]
    assert not offenders, "services 层不允许依赖 Web 框架 -> " + "; ".join(offenders)


def test_repositories_do_not_import_web_framework() -> None:
    offenders = [
        f"{_relatives(path)}: {sorted(bad)}"
        for path in sorted((APP_DIR / "repositories").rglob("*.py"))
        if (bad := _imported_roots(path) & WEB_FRAMEWORKS)
    ]
    assert not offenders, "repositories 层不允许依赖 Web 框架 -> " + "; ".join(offenders)


def test_services_do_not_import_api_layer() -> None:
    """反向依赖：service 不该知道路由长什么样。"""
    offenders = [
        _relatives(path)
        for path in sorted((APP_DIR / "services").rglob("*.py"))
        if "app.api" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "services 不允许 import api 层 -> " + "; ".join(offenders)


def test_repositories_do_not_import_services() -> None:
    offenders = [
        _relatives(path)
        for path in sorted((APP_DIR / "repositories").rglob("*.py"))
        if "app.services" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, "repositories 不允许 import services -> " + "; ".join(offenders)


def test_domain_exceptions_are_framework_free() -> None:
    """这条是分层纪律能成立的结构性前提。

    领域异常与 service 共用这个模块；它一旦引入 fastapi，service 就会通过传递
    依赖被绑上 Web 框架，而 AST 检查（只看直接 import）发现不了。
    """
    roots = _imported_roots(APP_DIR / "core" / "exceptions.py")
    assert not (roots & WEB_FRAMEWORKS), f"core/exceptions.py 引入了 {sorted(roots)}"


def test_core_clock_and_enums_are_framework_free() -> None:
    for name in ("clock.py", "enums.py", "text.py", "security.py", "ports.py", "config.py"):
        roots = _imported_roots(APP_DIR / "core" / name)
        assert not (roots & WEB_FRAMEWORKS), f"core/{name} 引入了 {sorted(roots)}"


def test_only_http_boundary_modules_import_fastapi() -> None:
    """允许 import fastapi 的模块应当是一份**短名单**。

    名单变长通常意味着业务逻辑正在渗进 HTTP 层。
    """
    allowed = {
        "app/core/deps.py",
        "app/core/errors.py",
        "app/main.py",
    }
    found = {
        _relatives(path)
        for path in sorted(APP_DIR.rglob("*.py"))
        if _imported_roots(path) & WEB_FRAMEWORKS
    }
    # api/ 整层都是 HTTP 边界，允许依赖框架
    unexpected = {
        path
        for path in found
        if path not in allowed and not path.startswith("app/api/")
    }
    assert not unexpected, (
        "以下模块不应直接依赖 Web 框架，请通过依赖注入或把它移到 api/ 层 -> "
        + "; ".join(sorted(unexpected))
    )
