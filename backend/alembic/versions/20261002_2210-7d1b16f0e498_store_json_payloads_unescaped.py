"""store json payloads unescaped

把 `submissions.payload` 里既有的 `\\uXXXX` 转义还原成原始 UTF-8。

**为什么需要它**：默认的 `json.dumps` 会把非 ASCII 转义。这样一来对内容做文本
搜索时，搜中文永远匹配不上 —— 库里存的是 `\\u5f20\\u4e09`，而人搜的是"张三"。
引擎侧已改为 `ensure_ascii=False`，但那只对**新写入**的行生效，既有行得在这里
就地还原，否则搜索会变成"老数据搜不到、新数据搜得到"。

顺带消除一处早已存在的不一致：体积校验用 `canonical_json`（不转义）算字节数，
而库里存的是转义形式 —— 同一份内容两处算法不同。

读回来完全一样，所以这是一次纯粹的存储形态修正，不改变任何语义。

Revision ID: 7d1b16f0e498
Revises: 24e091511e4a
Create Date: 2026-10-02 22:10:12.272819

"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7d1b16f0e498"
down_revision: Union[str, Sequence[str], None] = "24e091511e4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rewrite(ensure_ascii: bool) -> None:
    """逐行重写 payload 的序列化形式。

    用 `json.loads` / `json.dumps` 而不是字符串替换：只有解析成对象再重新序列化，
    才能保证结果一定是合法 JSON，也不会误伤本来就长得像转义序列的普通文本。
    """
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, payload FROM submissions")).fetchall()

    updates: list[dict[str, object]] = []
    for row in rows:
        payload = row.payload
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            # 读不出来的行原样留着：迁移不该吞掉数据，也不该猜它本来是什么
            continue
        rewritten = json.dumps(parsed, ensure_ascii=ensure_ascii, separators=(",", ":"))
        if rewritten != payload:
            updates.append({"sid": row.id, "body": rewritten})

    for update in updates:
        connection.execute(
            sa.text("UPDATE submissions SET payload = :body WHERE id = :sid"),
            update,
        )


def upgrade() -> None:
    """转义形式 -> 原始 UTF-8。"""
    _rewrite(ensure_ascii=False)


def downgrade() -> None:
    """原始 UTF-8 -> 转义形式。

    降级会把中文重新写成 `\\uXXXX`，于是**内容搜索对中文重新失效**。
    这是这个迁移的固有代价：降级回到的那个版本本来就不支持中文搜索。
    """
    _rewrite(ensure_ascii=True)
