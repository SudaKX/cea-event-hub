"""邀请码的接口层（任务 3.1 / 3.2 / 3.3）。

服务层的规则已经由 `test_invitations.py` 覆盖；这里验的是**从外面看**的行为：
权限边界（谁能做什么）、额度的 HTTP 形态，以及开关是否真的立刻生效。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.clock import utcnow
from app.db.models import InvitationCode, InvitationRedemption, User

API = "/api/v1"
MINE = f"{API}/invitations"
ADMIN = f"{API}/admin"


class TestUserEndpoints:
    """3.1：自己的码 —— 申请、删除、查看。"""

    def test_issue_then_list(self, user_client) -> None:
        issued = user_client.post(MINE, json={"name": "给张三"})
        assert issued.status_code == 201
        body = issued.json()["invitation"]
        assert body["name"] == "给张三"
        assert len(body["token"]) == 10
        assert body["max_uses"] == 1 and body["used_count"] == 0
        assert body["is_platform"] is False

        listed = user_client.get(MINE)
        assert listed.status_code == 200
        assert [c["id"] for c in listed.json()["invitations"]] == [body["id"]]

    def test_second_issue_is_rejected(self, user_client) -> None:
        assert user_client.post(MINE, json={"name": "第一张"}).status_code == 201
        second = user_client.post(MINE, json={"name": "第二张"})
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "invitation_quota"

    def test_name_is_required(self, user_client) -> None:
        assert user_client.post(MINE, json={"name": ""}).status_code == 422

    def test_delete_an_unused_code(self, user_client) -> None:
        code_id = user_client.post(MINE, json={"name": "给张三"}).json()["invitation"]["id"]
        assert user_client.delete(f"{MINE}/{code_id}").status_code == 204
        assert user_client.get(MINE).json()["invitations"] == []

    def test_delete_after_use_is_rejected(self, user_client, test_db) -> None:
        """已用过的码是使用记录的归属 —— 界面上不该提供这个动作。"""
        code_id = user_client.post(MINE, json={"name": "给张三"}).json()["invitation"]["id"]
        with test_db.session() as session:
            code = session.get(InvitationCode, code_id)
            code.used_count = 1

        response = user_client.delete(f"{MINE}/{code_id}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "invitation_not_deletable"

    def test_cannot_touch_someone_elses_code(self, user_client, test_db) -> None:
        """别人的码既删不掉也看不到 —— 而且"不存在"与"不是你的"给同一个答复。"""
        with test_db.session() as session:
            other = User(
                username="someoneelse",
                display_name="Other",
                password_hash="x",
                role="user",
                is_active=True,
            )
            session.add(other)
            session.flush()
            code = InvitationCode(
                token="NOTYOURS01",
                name="别人的",
                owner_id=other.id,
                max_uses=1,
                used_count=0,
                expires_at=utcnow() + timedelta(days=7),
            )
            session.add(code)
            session.flush()
            code_id = code.id

        assert user_client.delete(f"{MINE}/{code_id}").status_code == 400
        assert user_client.get(MINE).json()["invitations"] == []

    def test_anonymous_is_rejected(self, anon_client) -> None:
        assert anon_client.get(MINE).status_code == 401
        assert anon_client.post(MINE, json={"name": "偷申请"}).status_code == 401

    def test_usage_info_is_visible(self, user_client, test_db) -> None:
        """被哪个 username、在何时 —— 这是这张卡存在的意义之一。"""
        code_id = user_client.post(MINE, json={"name": "给张三"}).json()["invitation"]["id"]
        with test_db.session() as session:
            code = session.get(InvitationCode, code_id)
            code.used_count = 1
            guest = User(
                username="invitee",
                display_name="Invitee",
                password_hash="x",
                role="user",
                is_active=True,
            )
            session.add(guest)
            session.flush()
            session.add(
                InvitationRedemption(code_id=code_id, user_id=guest.id)
            )

        usages = user_client.get(MINE).json()["invitations"][0]["usages"]
        assert [u["username"] for u in usages] == ["invitee"]


class TestAdminEndpoints:
    """3.2：管理员的创建、列出与失效。"""

    def test_create_with_explicit_fields(self, admin_client) -> None:
        created = admin_client.post(
            f"{ADMIN}/invitations",
            json={"token": "CUSTOM-TOKEN", "name": "给社团", "days": 30, "max_uses": 10},
        )
        assert created.status_code == 201
        body = created.json()["invitation"]
        assert body["token"] == "CUSTOM-TOKEN"
        assert body["max_uses"] == 10
        assert body["is_platform"] is True

    def test_admin_is_not_subject_to_user_quotas(self, admin_client) -> None:
        """连建多张都成功 —— 那是运营动作，不是个人分享（design.md 决策 8）。"""
        for index in range(4):
            assert (
                admin_client.post(
                    f"{ADMIN}/invitations",
                    json={
                        "token": f"BULK{index}",
                        "name": f"第 {index} 张",
                        "days": 7,
                        "max_uses": 1,
                    },
                ).status_code
                == 201
            )

    def test_out_of_range_values_are_rejected(self, admin_client) -> None:
        for payload in (
            {"name": "x", "days": 0, "max_uses": 1},
            {"name": "x", "days": 7, "max_uses": 0},
            {"name": "", "days": 7, "max_uses": 1},
        ):
            assert admin_client.post(f"{ADMIN}/invitations", json=payload).status_code == 422

    def test_duplicate_token_is_rejected(self, admin_client) -> None:
        payload = {"token": "SAME-TOKEN", "name": "一", "days": 7, "max_uses": 1}
        assert admin_client.post(f"{ADMIN}/invitations", json=payload).status_code == 201
        assert admin_client.post(f"{ADMIN}/invitations", json=payload).status_code == 400

    def test_list_shows_everything(self, admin_client, user_client) -> None:
        """管理端看到**全部** —— 包括普通用户申请的那些。

        注意 `user_client` 夹具本身已经走过一次注册，那会消耗一张夹具造的码；
        因此这里断言的是"包含"，不是"恰好两张"。
        """
        issued = user_client.post(MINE, json={"name": "用户的"}).json()["invitation"]
        admin_client.post(
            f"{ADMIN}/invitations",
            json={"token": "PLATFORM01", "name": "平台的", "days": 7, "max_uses": 1},
        )

        listed = admin_client.get(f"{ADMIN}/invitations").json()["invitations"]
        by_token = {c["token"]: c for c in listed}
        assert "PLATFORM01" in by_token
        assert issued["token"] in by_token
        assert by_token["PLATFORM01"]["is_platform"] is True
        assert by_token[issued["token"]]["is_platform"] is False

    def test_revoke_keeps_records(self, admin_client, user_client, test_db) -> None:
        code_id = user_client.post(MINE, json={"name": "给张三"}).json()["invitation"]["id"]
        with test_db.session() as session:
            code = session.get(InvitationCode, code_id)
            code.used_count = 1
            session.add(InvitationRedemption(code_id=code_id, user_id=None))

        revoked = admin_client.post(f"{ADMIN}/invitations/{code_id}/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["invitation"]["revoked_at"] is not None

        with test_db.session() as session:
            records = session.scalars(
                select(InvitationRedemption).where(
                    InvitationRedemption.code_id == code_id
                )
            ).all()
        assert len(records) == 1, "失效把使用记录也弄丢了"

    def test_revoked_code_cannot_be_used_to_register(
        self, admin_client, client, invitation_code, test_db
    ) -> None:
        """管理员失效之后，那张码立刻不能用于注册 —— 与"暂停"是同一条路径。"""
        token = invitation_code(token="REVOKEME01")
        with test_db.session() as session:
            code_id = session.scalars(
                select(InvitationCode).where(InvitationCode.token == token)
            ).one().id

        assert admin_client.post(f"{ADMIN}/invitations/{code_id}/revoke").status_code == 200

        blocked = client.post(
            f"{API}/auth/register",
            json={
                "username": "afterrevoke",
                "password": "correct-horse",
                "email": "afterrevoke@example.com",
                "invitation_code": token,
            },
        )
        assert blocked.status_code == 422
        assert blocked.json()["error"]["fields"]["invitation_code"] == "邀请码不可用"

    def test_normal_user_cannot_manage(self, user_client) -> None:
        assert user_client.get(f"{ADMIN}/invitations").status_code == 403
        assert (
            user_client.post(
                f"{ADMIN}/invitations",
                json={"name": "偷建", "days": 7, "max_uses": 1},
            ).status_code
            == 403
        )

    def test_anonymous_cannot_manage(self, anon_client) -> None:
        assert anon_client.get(f"{ADMIN}/invitations").status_code == 401


class TestSwitches:
    """3.3：两个开关 —— 权限与"立刻生效"。"""

    def test_defaults_are_off(self, admin_client) -> None:
        assert admin_client.get(f"{ADMIN}/switches").json() == {
            "invitations_paused": False,
            "invitation_issuance_paused": False,
        }

    def test_write_then_read_back(self, admin_client) -> None:
        response = admin_client.put(
            f"{ADMIN}/switches",
            json={"key": "invitations_paused", "enabled": True},
        )
        assert response.status_code == 200
        assert response.json()["invitations_paused"] is True
        # 另一个开关不受影响
        assert response.json()["invitation_issuance_paused"] is False
        assert admin_client.get(f"{ADMIN}/switches").json()["invitations_paused"] is True

    def test_unknown_key_is_rejected(self, admin_client) -> None:
        response = admin_client.put(
            f"{ADMIN}/switches", json={"key": "not_a_switch", "enabled": True}
        )
        assert response.status_code == 400

    def test_normal_user_cannot_touch_switches(self, user_client) -> None:
        assert user_client.get(f"{ADMIN}/switches").status_code == 403
        assert (
            user_client.put(
                f"{ADMIN}/switches",
                json={"key": "invitations_paused", "enabled": True},
            ).status_code
            == 403
        )

    def test_pause_takes_effect_immediately(
        self, admin_client, client, invitation_code
    ) -> None:
        """**不重启**就生效 —— 这正是它不能放配置项的理由（design.md 决策 4）。"""
        token = invitation_code(token="BEFOREPAUSE")

        accepted = client.post(
            f"{API}/auth/register",
            json={
                "username": "before",
                "password": "correct-horse",
                "email": "before@example.com",
                "invitation_code": token,
            },
        )
        assert accepted.status_code == 202

        admin_client.put(
            f"{ADMIN}/switches", json={"key": "invitations_paused", "enabled": True}
        )

        blocked = client.post(
            f"{API}/auth/register",
            json={
                "username": "after",
                "password": "correct-horse",
                "email": "after@example.com",
                "invitation_code": token,
            },
        )
        assert blocked.status_code == 422
        assert blocked.json()["error"]["fields"]["invitation_code"] == "邀请码不可用"

    def test_issuance_pause_blocks_new_codes_only(
        self, admin_client, user_client
    ) -> None:
        admin_client.put(
            f"{ADMIN}/switches",
            json={"key": "invitation_issuance_paused", "enabled": True},
        )
        blocked = user_client.post(MINE, json={"name": "暂停期间"})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "invitation_issuance_paused"
