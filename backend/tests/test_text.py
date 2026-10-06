"""`core/text.py` 里活动标识的归一化与形态校验。

## 为什么这张样本表要单独钉住

活动标识的归一化规则**在两种语言里各实现一次**（后端 `str.lower()`，前端
`String.prototype.toLowerCase()`）—— 前端必须自己归一化，因为它要拼宿主存储的命名空间，
而那条路径完全不经过后端。两端不一致的表现是"后端认得出、前端认不出"的间歇性故障，
排查方向会被带偏到桥接或路由上去。

因此本表与 `frontend/src/bridge/eventId.spec.ts` 共用**同一份内容**：改一处就要改另一处。
表里刻意放了几个刁钻样本（全角字母、`İ`、`ẞ`、希腊语词尾 sigma、标题格合字），
它们正是两端最可能分叉的地方。

这些期望值是**实测**出来的，不是推断的。顺带记一个教训：第一次比对时我用管道把样本从
PowerShell 喂给 Python，非 ASCII 在管道里被重新编码，于是 Python 看起来"没有小写化希腊
字母"—— 差点据此写出一个错误的结论。改成走文件 + 只输出码点之后，13 个样本两端完全一致。
"""

from __future__ import annotations

import pytest

from app.core.text import event_id_shape_error, normalize_event_id

#: 与 `frontend/src/bridge/eventId.spec.ts` 共用，内容必须逐字相同。
#: 含不可见差异的样本用显式码点写，免得复制粘贴时被看不见的字符骗过去。
NORMALIZATION_SAMPLES: list[tuple[str, str]] = [
    ("autumn2026", "autumn2026"),
    ("Autumn2026", "autumn2026"),
    ("AUTUMN2026", "autumn2026"),
    ("  Autumn-2026  ", "autumn-2026"),
    ("Autumn_2026", "autumn_2026"),
    # 全角：两端都得到全角小写（FF41 FF42 FF43），随后被形态校验拒掉 —— 不是 ASCII
    ("ＡＢＣ", "ａｂｃ"),
    # i + 组合上点（0069 0307 …）：长度变了，所以必须"先归一化再校验"
    ("İstanbul", "i\u0307stanbul"),
    ("ẞ", "ß"),
    ("Straße", "straße"),
    # 希腊语词尾 sigma 是**上下文相关**的（… 03C6 03BF 03C2）：两端都必须给出 ς
    ("ΣΟΦΟΣ", "\u03c3\u03bf\u03c6\u03bf\u03c2"),
    ("ǅungla", "\u01c6ungla"),
    ("", ""),
    ("  ", ""),
]


@pytest.mark.parametrize(("raw", "expected"), NORMALIZATION_SAMPLES)
def test_normalization_samples(raw: str, expected: str) -> None:
    """归一化的结果与前端样本表逐项一致。"""
    assert normalize_event_id(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), NORMALIZATION_SAMPLES)
def test_normalization_is_idempotent(raw: str, expected: str) -> None:
    """归一化是幂等的：规范形态再过一次不变。

    这条保证"归一化可以被安全地重复调用"，因此各个入口不必约定谁先谁后。
    """
    assert normalize_event_id(expected) == expected


@pytest.mark.parametrize(
    "raw",
    ["autumn2026", "a-b_c9", "abc", "a" * 64, "9lives"],
)
def test_canonical_shape_is_accepted(raw: str) -> None:
    assert event_id_shape_error(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "Autumn2026",
        "AUTUMN",
        "ab",  # 太短
        "a" * 65,  # 太长
        "-abc",  # 首字符
        "abc-",  # 尾字符
        "",
        "a.b",
        "a b",
        "a/b",
        "a\\b",
        "..",
        "ＡＢＣ",
    ],
)
def test_shape_check_rejects_non_canonical(raw: str) -> None:
    """形态校验只看字符集，**不隐式归一化**。

    这条断言把"调用方必须先归一化"这个契约钉住。若哪天有人让 `event_id_shape_error`
    内部归一化，这里会红 —— 那正是提醒：隐式归一化会把"忘了归一化"从一次明确的拒绝，
    退化成"一个悄悄存进去的大写标识"。
    """
    assert event_id_shape_error(raw) is not None


@pytest.mark.parametrize(
    "raw",
    ["a.b", "a b", "a/b", "a\\b", "..", "./a", "a\x00b", "ＡＢ", "İ", "ẞ", "Σ"],
)
def test_normalization_never_launders_a_path_hazard(raw: str) -> None:
    """归一化只放宽大小写，不放宽其它任何字符。

    标识直接成为内容与数据目录名，所以这是**路径安全**的一部分：如果归一化能把
    `.`、`/`、空白之类"洗"成合法字符，就会出现"校验通过、路径越界"。
    小写化做不到这件事 —— 全角字母、`İ`、`ẞ` 小写化之后仍落在允许集之外。
    """
    assert event_id_shape_error(normalize_event_id(raw)) is not None
