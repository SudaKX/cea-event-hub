"""账号删除：任务 3.1 与 3.2。

这一组盯的核心是**删除不会伤及社团收集的数据**，以及**新账号不会继承被删账号的东西**。
后者依赖 `users.id` 不再复用（迁移 `4d594b75179e`），因此那条断言同时是那个迁移的
端到端守门人。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.core.security import hash_password
from app.db.models import Event, Submission, SubmitterQuota, User, UserSession

API = "/api/v1"
ADMIN = f"{API}/admin"


def _seed_target(test_db, *, username: str = "victim", role: str = "user") -> int:
    """造一个可删的账号，并让它留下会话、提交与配额计数。"""
    with test_db.session() as session:
        user = User(
            username=username,
            display_name=username,
            password_hash=hash_password("correct-horse"),
            email=f"{username}@example.com",
            role=role,
        )
        session.add(user)
        session.flush()
        user_id = user.id

        event = session.scalar(select(Event))
        if event is None:
            event = Event(
                id="del-event", title="t", status="live", entry_path="index.html"
            )
            session.add(event)
            session.flush()
        # 该用户创建了这个活动、做过一次审核，并提交过一条
        event.owner_id = user_id
        session.add(
            Submission(
                event_id=event.id,
                submitter=f"u:{user_id}",
                user_id=user_id,
                reviewed_by=user_id,
                payload={"n": 1},
                status=1,
            )
        )
        session.add(
            SubmitterQuota(event_id=event.id, submitter=f"u:{user_id}", used=1)
        )
        session.flush()
    return user_id


def _snapshot(test_db) -> dict:
    with test_db.session() as session:
        return {
            "users": session.scalar(select(text("COUNT(*)")).select_from(User)),
            "submissions": session.scalar(
                select(text("COUNT(*)")).select_from(Submission)
            ),
            "quotas": [
                (q.event_id, q.submitter, q.used)
                for q in session.scalars(select(SubmitterQuota))
            ],
            "submitters": [s.submitter for s in session.scalars(select(Submission))],
            "owner_ids": [e.owner_id for e in session.scalars(select(Event))],
            "reviewed_by": [s.reviewed_by for s in session.scalars(select(Submission))],
        }


class TestDeletion:
    def test_deleted_user_cannot_log_in(self, admin_client, client, test_db) -> None:
        target_id = _seed_target(test_db)
        assert client.post(
            f"{API}/auth/login",
            json={"username": "victim", "password": "correct-horse"},
        ).status_code == 200
        client.post(f"{API}/auth/logout")

        response = admin_client.delete(f"{ADMIN}/users/{target_id}")
        assert response.status_code == 204

        assert client.post(
            f"{API}/auth/login",
            json={"username": "victim", "password": "correct-horse"},
        ).status_code == 401

    def test_existing_session_dies_immediately(
        self, admin_client, client, test_db
    ) -> None:
        """会话随账号消失（`ON DELETE CASCADE`），不需要等它自然过期。"""
        target_id = _seed_target(test_db)
        assert client.post(
            f"{API}/auth/login",
            json={"username": "victim", "password": "correct-horse"},
        ).status_code == 200
        assert client.get(f"{API}/auth/me").status_code == 200

        assert admin_client.delete(f"{ADMIN}/users/{target_id}").status_code == 204

        assert client.get(f"{API}/auth/me").status_code == 401

    def test_session_rows_are_gone(self, admin_client, test_db) -> None:
        target_id = _seed_target(test_db)
        with test_db.session() as session:
            session.add(
                UserSession(
                    token_hash="somehash",
                    user_id=target_id,
                    expires_at=__import__("app.core.clock", fromlist=["utcnow"]).utcnow(),
                )
            )

        assert admin_client.delete(f"{ADMIN}/users/{target_id}").status_code == 204

        with test_db.session() as session:
            assert session.scalars(
                select(UserSession).where(UserSession.user_id == target_id)
            ).all() == []

    def test_submissions_survive_intact(self, admin_client, test_db) -> None:
        """**这是整条决策的核心断言。**

        提交是社团收集的数据，与"这个人还在不在"是两件事。删除只让署名不可考，
        不能动内容。
        """
        target_id = _seed_target(test_db)
        before = _snapshot(test_db)

        assert admin_client.delete(f"{ADMIN}/users/{target_id}").status_code == 204

        after = _snapshot(test_db)
        assert after["submissions"] == before["submissions"]
        assert after["submitters"] == before["submitters"]

    def test_audit_columns_are_nulled_not_removed(
        self, admin_client, test_db
    ) -> None:
        """他创建的活动与做过的审核**都还在**，只是不再归属于任何人。"""
        target_id = _seed_target(test_db)

        assert admin_client.delete(f"{ADMIN}/users/{target_id}").status_code == 204

        after = _snapshot(test_db)
        assert after["owner_ids"] == [None]
        assert after["reviewed_by"] == [None]
        # 活动本身还在
        with test_db.session() as session:
            assert session.scalars(select(Event)).all()

    def test_quota_rows_are_cleaned(self, admin_client, test_db) -> None:
        """计数行没有外键承托，得显式清 —— 否则一致性检查会一直报"计数 N、实际 0"。"""
        target_id = _seed_target(test_db)
        with test_db.session() as session:
            assert session.scalars(
                select(SubmitterQuota).where(
                    SubmitterQuota.submitter == f"u:{target_id}"
                )
            ).all()

        assert admin_client.delete(f"{ADMIN}/users/{target_id}").status_code == 204

        with test_db.session() as session:
            assert session.scalars(
                select(SubmitterQuota).where(
                    SubmitterQuota.submitter == f"u:{target_id}"
                )
            ).all() == []

    def test_new_account_does_not_inherit(
        self, admin_client, client, test_db, register, sent_emails
    ) -> None:
        """**这条是迁移 `4d594b75179e` 的端到端守门人。**

        id 一旦复用，新注册的人就会拿到 `u:{同一个编号}`，于是从数据角度"继承"前者的
        提交与配额 —— 而且没有任何报错。任务 4.1 的列表显示会让他直接在界面上看见别人
        的提交。
        """
        target_id = _seed_target(test_db)
        with test_db.session() as session:
            # 确保它是当前最大的 id，"复用"才会真的发生
            assert session.scalar(select(text("MAX(id)")).select_from(User)) == target_id

        assert admin_client.delete(f"{ADMIN}/users/{target_id}").status_code == 204

        assert register(client, username="newcomer").status_code == 204
        with test_db.session() as session:
            newcomer = session.scalar(select(User).where(User.username == "newcomer"))

        assert newcomer is not None
        assert newcomer.id != target_id, "新账号拿到了被删账号的 id"
        with test_db.session() as session:
            # 被删者那条提交的署名没有被新人"接手"
            assert session.scalars(
                select(Submission).where(Submission.submitter == f"u:{newcomer.id}")
            ).all() == []


class TestGuards:
    def test_cannot_delete_yourself(self, admin_client, admin_id, test_db) -> None:
        """删自己会被自删守卫拦下 —— 而它也顺带覆盖了"删最后一个管理员"。

        **通过 API 走不到 `last_admin_protected`。** 能通过管理员校验的操作者本身就是
        一个可用管理员，因此"除目标之外还有几个可用管理员"永远至少是 1。要在这条路径
        上删掉最后一个可用管理员，唯一可能是删自己，而那已经被 400 拦下了。这条守卫
        因此只在服务层可达，见下面那条用例 —— 与批量改用户那里"停用最后一个管理员"
        的处境相同。
        """
        _seed_target(test_db, username="spare-admin", role="admin")

        response = admin_client.delete(f"{ADMIN}/users/{admin_id}")
        assert response.status_code == 400
        # 目标（自己）当然还在
        with test_db.session() as session:
            assert session.get(User, admin_id) is not None

    def test_last_admin_guard_holds_at_the_service_level(self, test_db) -> None:
        """服务层的最后管理员守卫：**停用的管理员不算可用。**

        API 到不了这里（见上），但守卫本身必须成立 —— 它是"系统不能失去管理能力"这条
        不变量的最后一处落点。构造方式：两个管理员，其中一个已停用，然后**以那个停用
        账号的身份**去删另一个。

        用户先在一个事务里建好并提交，删除尝试另开一个事务：守卫在被拒绝时于任何改动
        **之前**抛出，因此下面只需确认它没有留下副作用。
        """
        from app.core.config import settings
        from app.core.exceptions import LastAdminProtected
        from app.services.auth import AuthService

        with test_db.session() as session:
            active = User(
                username="only-active-admin",
                display_name="a",
                password_hash=hash_password("x"),
                role="admin",
                is_active=True,
            )
            dormant = User(
                username="dormant-admin",
                display_name="d",
                password_hash=hash_password("x"),
                role="admin",
                is_active=False,
            )
            session.add_all([active, dormant])
            session.flush()
            active_id, dormant_id = active.id, dormant.id

        with test_db.session() as session:
            actor = session.get(User, dormant_id)
            service = AuthService(settings)
            with pytest.raises(LastAdminProtected):
                service.delete_user(session, actor=actor, target_id=active_id)

        with test_db.session() as session:
            assert session.get(User, active_id) is not None, "被拒绝时不能产生部分生效"
            assert session.get(User, dormant_id) is not None

    def test_can_delete_an_admin_when_another_remains(
        self, admin_client, test_db
    ) -> None:
        other = _seed_target(test_db, username="second-admin", role="admin")
        assert admin_client.delete(f"{ADMIN}/users/{other}").status_code == 204

    def test_unknown_id_is_404(self, admin_client) -> None:
        assert admin_client.delete(f"{ADMIN}/users/999999").status_code == 404

    def test_normal_user_is_forbidden(self, user_client, test_db) -> None:
        target_id = _seed_target(test_db)
        assert user_client.delete(f"{ADMIN}/users/{target_id}").status_code == 403

    def test_anonymous_is_unauthorized(self, client, test_db) -> None:
        target_id = _seed_target(test_db)
        assert client.delete(f"{ADMIN}/users/{target_id}").status_code == 401

    def test_guard_leaves_the_target_untouched(
        self, admin_client, admin_id, test_db
    ) -> None:
        """被拒绝时 MUST NOT 产生部分生效。"""
        before = _snapshot(test_db)
        assert admin_client.delete(f"{ADMIN}/users/{admin_id}").status_code == 400
        assert _snapshot(test_db) == before
