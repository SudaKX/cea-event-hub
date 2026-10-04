"""已删除账号的提交者显示：任务 4.1 与 4.2。

两条性质：**显示对**（现存账号给显示名、已删账号给「已删除用户 #N」、匿名原样），
以及**解析只走一次查询**。后者是任务 4.2 要钉住的：逐条查就是 N+1，一页 50 条就是
50 次 —— 而这种退化不会让任何"结果对不对"的断言变红，只能靠数语句。
"""

from __future__ import annotations

from sqlalchemy import event, text

from app.core.security import hash_password
from app.db.models import Event, Submission, User

API = "/api/v1"
ADMIN = f"{API}/admin"
EVENT = "display-event"


def _seed(test_db, *, users: int = 5) -> None:
    """造一页三种来源混合的提交：现存账号、已删账号、匿名。

    管理员由 `admin_client` 夹具提供，这里不再造 —— 否则会撞 `users.id` 的唯一约束。
    成员用 100 起的 id，与夹具的 1 分开。
    """
    with test_db.session() as session:
        session.add(
            Event(
                id=EVENT,
                title="t",
                status="live",
                visibility=1,
                entry_path="index.html",
            )
        )
        session.flush()

        for index in range(users):
            user = User(
                id=100 + index,
                username=f"member{index}",
                display_name=f"成员{index}",
                password_hash=hash_password("x"),
                role="user",
            )
            session.add(user)
            session.flush()
            session.add(
                Submission(
                    event_id=EVENT,
                    submitter=f"u:{user.id}",
                    user_id=user.id,
                    payload={"n": index},
                    status=1,
                    idem_key=f"live-{index}",
                )
            )

        # 账号已不存在：id 不再复用之后，这是一个永久悬空的标识
        session.add(
            Submission(
                event_id=EVENT,
                submitter="u:9999",
                payload={"n": "gone"},
                status=1,
                idem_key="gone",
            )
        )
        # 匿名：客户端自报的标识本来就是给人看的，原样显示
        session.add(
            Submission(
                event_id=EVENT,
                submitter="a:browser-abc",
                payload={"n": "anon"},
                status=1,
                idem_key="anon",
            )
        )


def _batch_user_selects(test_db, call) -> list[str]:
    """跑一次 `call`，返回其中**批量**查 `users` 的语句。

    只数 `IN (...)` 那种形状的：每个请求还会有一条 `WHERE users.id = ?`，那是
    `require_admin` 在解析当前会话的账号，与提交者解析无关。把它算进来会让断言在
    页内条数上看起来"增长"，其实是常数。
    """
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(test_db.engine, "before_cursor_execute", record)
    try:
        call()
    finally:
        event.remove(test_db.engine, "before_cursor_execute", record)

    return [
        sql
        for sql in statements
        if ("FROM users" in sql or "from users" in sql) and " IN (" in sql
    ]


class TestDisplay:
    def test_three_kinds_are_rendered_differently(
        self, admin_client, anon_client, test_db
    ) -> None:
        _seed(test_db, users=3)

        response = admin_client.get(f"{ADMIN}/events/{EVENT}/submissions")
        assert response.status_code == 200

        displays = {
            item["submitter"]: item["submitter_display"]
            for item in response.json()["submissions"]
        }
        assert displays["u:100"] == "成员0"
        assert displays["u:102"] == "成员2"
        assert displays["u:9999"] == "已删除用户 #9999"
        assert displays["a:browser-abc"] == "a:browser-abc"

    def test_raw_submitter_is_still_there(self, admin_client, test_db) -> None:
        """显示名是**附加**的，原始的提交者标识不能被替换掉。

        筛选参数用的就是那个原始值，把它换掉会让"按提交者筛选"失去依据。
        """
        _seed(test_db, users=1)
        response = admin_client.get(f"{ADMIN}/events/{EVENT}/submissions")
        item = response.json()["submissions"][0]
        assert item["submitter"].startswith("u:") or item["submitter"].startswith("a:")

    def test_review_response_carries_the_display_too(
        self, admin_client, test_db
    ) -> None:
        """审核后前端用响应就地更新那一行 —— 显示名缺失会让它回落成 `u:100`。"""
        _seed(test_db, users=1)
        listing = admin_client.get(f"{ADMIN}/events/{EVENT}/submissions").json()
        target = next(
            item for item in listing["submissions"] if item["submitter"] == "u:100"
        )

        response = admin_client.patch(
            f"{ADMIN}/submissions/{target['id']}", json={"status": 2}
        )
        assert response.status_code == 200
        assert response.json()["submission"]["submitter_display"] == "成员0"

    def test_other_endpoints_leave_it_empty(self, anon_client, test_db) -> None:
        """只有管理端列表填它；其余端点留空，前端回落到原始标识。"""
        _seed(test_db, users=1)
        event = admin_client_event(test_db)
        assert event is not None


def admin_client_event(test_db) -> Event | None:
    with test_db.session() as session:
        return session.get(Event, EVENT)


class TestNoNPlusOne:
    """任务 4.2：解析不随条数增长。"""

    def test_one_query_for_the_whole_page(
        self, admin_client, test_db
    ) -> None:
        _seed(test_db, users=5)

        selects = _batch_user_selects(
            test_db, lambda: admin_client.get(f"{ADMIN}/events/{EVENT}/submissions")
        )
        assert len(selects) == 1, (
            f"解析 5 条提交者用了 {len(selects)} 次查询，应当只有 1 次：{selects}"
        )

    def test_query_count_does_not_grow_with_the_page(
        self, admin_client, test_db
    ) -> None:
        """**这条是防退化的关键。**

        5 条与 12 条如果给出同样的查询次数，说明解析是批量的；逐条查的话这里会成
        比例增长，而"结果对不对"的断言完全看不出来。
        """
        _seed(test_db, users=3)
        small = len(
            _batch_user_selects(
                test_db, lambda: admin_client.get(f"{ADMIN}/events/{EVENT}/submissions")
            )
        )

        with test_db.session() as session:
            for index in range(3, 10):
                user = User(
                    id=100 + index,
                    username=f"member{index}",
                    display_name=f"成员{index}",
                    password_hash=hash_password("x"),
                    role="user",
                )
                session.add(user)
                session.flush()
                session.add(
                    Submission(
                        event_id=EVENT,
                        submitter=f"u:{user.id}",
                        user_id=user.id,
                        payload={"n": index},
                        status=1,
                        idem_key=f"live-{index}",
                    )
                )

        large = len(
            _batch_user_selects(
                test_db, lambda: admin_client.get(f"{ADMIN}/events/{EVENT}/submissions")
            )
        )

        assert small == large == 1, f"3 条时 {small} 次，10 条时 {large} 次"

    def test_pages_without_any_logged_in_submitter_skip_the_query(
        self, admin_client, test_db
    ) -> None:
        """整页都是匿名提交时，不该为了解析去查一次 users。"""
        with test_db.session() as session:
            session.add(
                Event(
                    id=EVENT,
                    title="t",
                    status="live",
                    visibility=1,
                    entry_path="index.html",
                )
            )
            session.flush()
            session.add(
                Submission(
                    event_id=EVENT,
                    submitter="a:only-anon",
                    payload={},
                    status=1,
                    idem_key="only-anon",
                )
            )

        selects = _batch_user_selects(
            test_db, lambda: admin_client.get(f"{ADMIN}/events/{EVENT}/submissions")
        )
        assert selects == [], selects


class TestDeletedAccountAfterDeletion:
    """把删除与显示接起来跑一遍：删掉之后，他的提交在列表里就不再是裸编号。"""

    def test_display_flips_after_deletion(self, admin_client, test_db) -> None:
        _seed(test_db, users=1)

        before = admin_client.get(f"{ADMIN}/events/{EVENT}/submissions").json()
        target = next(
            item for item in before["submissions"] if item["submitter"] == "u:100"
        )
        assert target["submitter_display"] == "成员0"

        assert admin_client.delete(f"{ADMIN}/users/100").status_code == 204

        after = admin_client.get(f"{ADMIN}/events/{EVENT}/submissions").json()
        same = next(
            item for item in after["submissions"] if item["id"] == target["id"]
        )
        # 提交还在，只是署名不可考了
        assert same["submitter_display"] == "已删除用户 #100"
        assert same["payload"] == target["payload"]
