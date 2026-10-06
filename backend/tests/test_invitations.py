"""邀请码的仓储与服务（任务 2.1 / 2.2，并顺带验 1.3 与 1.5 的模型侧）。

这里盯的是三类容易"写对了但会失效"的性质：

1. **扣次数是一次条件更新**，四种不合格（不存在 / 过期 / 失效 / 用尽）都拒绝；
2. **两条额度依据持久状态**（同时一张未使用、每 24 小时一张）；
3. **准入的失败提示不区分原因** —— 全部归到同一个异常，否则注册接口成了枚举器。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.enums import PlatformSwitchKey, UserRole
from app.core.exceptions import (
    BadRequest,
    Forbidden,
    InvitationInvalid,
    InvitationIssuancePaused,
    InvitationNotDeletable,
    InvitationQuota,
)
from app.core.security import hash_password
from app.db.models import (
    InvitationCode,
    InvitationRedemption,
    PendingRegistration,
    User,
)
from app.db.session import Database
from conftest import verification_token
from app.repositories.invitations import (
    InvitationRedemptionRepository,
    InvitationRepository,
    PlatformSwitchRepository,
)
from app.services.invitations import (
    USER_CODE_INTERVAL_HOURS,
    USER_CODE_MAX_USES,
    USER_CODE_TTL_DAYS,
    InvitationService,
)


def _user(username: str = "alice", role: str = UserRole.USER.value) -> User:
    return User(
        username=username,
        display_name=username.title(),
        password_hash=hash_password("correct-horse"),
        role=role,
        is_active=True,
    )


def _code(
    token: str = "INVITE0001",
    *,
    owner_id: int | None = None,
    max_uses: int = 1,
    used_count: int = 0,
    days: int = 7,
    revoked: bool = False,
) -> InvitationCode:
    now = datetime.now(UTC)
    return InvitationCode(
        token=token,
        name="给张三",
        owner_id=owner_id,
        max_uses=max_uses,
        used_count=used_count,
        expires_at=now + timedelta(days=days),
        revoked_at=now if revoked else None,
    )


@pytest.fixture
def service() -> InvitationService:
    return InvitationService()


@pytest.fixture
def owner(test_db: Database) -> User:
    with test_db.session() as session:
        user = _user()
        session.add(user)
        session.flush()
        session.expunge(user)
    return user


class TestRepository:
    """2.1：仓储只回答"存了什么"。"""

    def test_claim_use_succeeds_while_uses_remain(self, test_db: Database) -> None:
        repo = InvitationRepository()
        with test_db.session() as session:
            code = repo.add(session, _code(max_uses=2))
            assert repo.claim_use(session, code_id=code.id, now=datetime.now(UTC))
        with test_db.session() as session:
            assert session.get(InvitationCode, code.id).used_count == 1

    @pytest.mark.parametrize(
        "bad",
        [
            pytest.param(_code(used_count=1), id="已用尽"),
            pytest.param(_code(days=-1), id="已过期"),
            pytest.param(_code(revoked=True), id="已失效"),
        ],
    )
    def test_claim_use_refuses_unusable_codes(
        self, test_db: Database, bad: InvitationCode
    ) -> None:
        """四种不合格都由**同一条语句**挡下 —— 这就是 CAS 的形状。"""
        repo = InvitationRepository()
        with test_db.session() as session:
            code = repo.add(session, bad)
            assert repo.claim_use(session, code_id=code.id, now=datetime.now(UTC)) is False

    def test_claim_use_refuses_unknown_id(self, test_db: Database) -> None:
        with test_db.session() as session:
            assert (
                InvitationRepository().claim_use(
                    session, code_id=999999, now=datetime.now(UTC)
                )
                is False
            )

    def test_latest_created_at_is_per_owner(self, test_db: Database) -> None:
        repo = InvitationRepository()
        with test_db.session() as session:
            alice = _user("alice")
            bob = _user("bob")
            session.add_all([alice, bob])
            session.flush()
            repo.add(session, _code("A1", owner_id=alice.id))
            assert repo.latest_created_at(session, alice.id) is not None
            assert repo.latest_created_at(session, bob.id) is None

    def test_count_unused_ignores_everything_that_is_not_usable(
        self, test_db: Database
    ) -> None:
        """"未使用"= 未用尽 **且** 未失效 **且** 未删除 **且** 未过期。

        漏掉过期那一项时，一张废码会占着"同时只能有一张未使用"的名额，把用户锁住
        （design.md 决策 10）—— 这是实现时测试抓到的洞。
        """
        repo = InvitationRepository()
        now = datetime.now(UTC)
        with test_db.session() as session:
            alice = _user()
            session.add(alice)
            session.flush()
            repo.add(session, _code("USED", owner_id=alice.id, used_count=1))
            repo.add(session, _code("REVOKED", owner_id=alice.id, revoked=True))
            repo.add(session, _code("EXPIRED", owner_id=alice.id, days=-1))
            assert repo.count_unused_for_owner(session, alice.id, now=now) == 0

            fresh = repo.add(session, _code("FRESH", owner_id=alice.id))
            assert repo.count_unused_for_owner(session, alice.id, now=now) == 1

            repo.soft_delete(session, fresh, now=now)
            assert repo.count_unused_for_owner(session, alice.id, now=now) == 0

    def test_redemptions_are_fetched_in_one_query(self, test_db: Database) -> None:
        repo = InvitationRepository()
        redemptions = InvitationRedemptionRepository()
        with test_db.session() as session:
            alice = _user()
            session.add(alice)
            session.flush()
            codes = [repo.add(session, _code(f"C{i}")) for i in range(3)]
            for code in codes:
                redemptions.add(
                    session,
                    InvitationRedemption(code_id=code.id, user_id=alice.id),
                )
            got = redemptions.list_for_codes(session, [c.id for c in codes])
            assert len(got) == 3
            assert redemptions.list_for_codes(session, []) == []


class TestSwitches:
    """1.3：开关"没有行就是关闭"，写入后可读回。"""

    def test_missing_row_means_disabled(self, test_db: Database) -> None:
        repo = PlatformSwitchRepository()
        with test_db.session() as session:
            assert repo.is_enabled(session, PlatformSwitchKey.INVITATIONS_PAUSED) is False
            assert repo.all_states(session) == {
                PlatformSwitchKey.INVITATIONS_PAUSED.value: False,
                PlatformSwitchKey.INVITATION_ISSUANCE_PAUSED.value: False,
            }

    def test_set_then_read_back(self, test_db: Database) -> None:
        repo = PlatformSwitchRepository()
        with test_db.session() as session:
            repo.set_enabled(session, PlatformSwitchKey.INVITATIONS_PAUSED, enabled=True)
        with test_db.session() as session:
            assert repo.is_enabled(session, PlatformSwitchKey.INVITATIONS_PAUSED) is True
            # 另一个开关不受影响
            assert (
                repo.is_enabled(session, PlatformSwitchKey.INVITATION_ISSUANCE_PAUSED)
                is False
            )

    def test_set_is_idempotent(self, test_db: Database) -> None:
        repo = PlatformSwitchRepository()
        with test_db.session() as session:
            repo.set_enabled(session, PlatformSwitchKey.INVITATIONS_PAUSED, enabled=True)
            repo.set_enabled(session, PlatformSwitchKey.INVITATIONS_PAUSED, enabled=True)
        with test_db.session() as session:
            rows = session.scalars(select(InvitationCode)).all()
            assert rows == []  # 没顺手建出别的东西
            assert repo.is_enabled(session, PlatformSwitchKey.INVITATIONS_PAUSED) is True


class TestRegistrationGate:
    """2.2 的准入判定。**所有失败必须是同一个异常。**"""

    def test_valid_code_passes(self, test_db: Database, service: InvitationService) -> None:
        with test_db.session() as session:
            InvitationRepository().add(session, _code("GOODCODE01"))
        with test_db.session() as session:
            code = service.ensure_registration_allowed(session, token="GOODCODE01")
            assert code.token == "GOODCODE01"

    @pytest.mark.parametrize(
        "token,seed",
        [
            pytest.param(None, None, id="没给码"),
            pytest.param("", None, id="空字符串"),
            pytest.param("   ", None, id="只有空白"),
            pytest.param("NOPE", None, id="不存在的码"),
            pytest.param("EXPIRED", _code("EXPIRED", days=-1), id="已过期"),
            pytest.param("REVOKED", _code("REVOKED", revoked=True), id="已失效"),
            pytest.param("USEDUP", _code("USEDUP", used_count=1), id="已用尽"),
        ],
    )
    def test_every_failure_is_the_same_error(
        self,
        test_db: Database,
        service: InvitationService,
        token: str | None,
        seed: InvitationCode | None,
    ) -> None:
        """**这条是安全边界。** 区分原因等于给匿名接口一个邀请码枚举器。"""
        if seed is not None:
            with test_db.session() as session:
                InvitationRepository().add(session, seed)
        with test_db.session() as session:
            with pytest.raises(InvitationInvalid) as caught:
                service.ensure_registration_allowed(session, token=token)
        assert caught.value.message == "邀请码不可用"
        assert caught.value.code == "invitation_invalid"

    def test_paused_rejects_even_valid_codes(
        self, test_db: Database, service: InvitationService
    ) -> None:
        """暂停邀请必须覆盖**存量**码 —— 只挡新码等于没刹车。"""
        with test_db.session() as session:
            InvitationRepository().add(session, _code("STILLGOOD1"))
            PlatformSwitchRepository().set_enabled(
                session, PlatformSwitchKey.INVITATIONS_PAUSED, enabled=True
            )
        with test_db.session() as session:
            with pytest.raises(InvitationInvalid):
                service.ensure_registration_allowed(session, token="STILLGOOD1")

    def test_paused_does_not_affect_issuance_flag(
        self, test_db: Database, service: InvitationService
    ) -> None:
        """两个开关互不牵连：暂停邀请不挡申请，反之亦然。"""
        with test_db.session() as session:
            InvitationRepository().add(session, _code("KEEPALIVE1"))
            PlatformSwitchRepository().set_enabled(
                session, PlatformSwitchKey.INVITATION_ISSUANCE_PAUSED, enabled=True
            )
        with test_db.session() as session:
            # 申请被挡
            assert service.issuance_paused(session) is True
            # 但已发出的码照常可用
            assert (
                service.ensure_registration_allowed(session, token="KEEPALIVE1").token
                == "KEEPALIVE1"
            )


class TestConsume:
    """2.2 的消耗。"""

    def test_consume_increments_and_records(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        with test_db.session() as session:
            code = InvitationRepository().add(session, _code("CONSUME001", max_uses=3))
        with test_db.session() as session:
            service.consume(session, code_id=code.id, user_id=owner.id)
        with test_db.session() as session:
            assert session.get(InvitationCode, code.id).used_count == 1
            record = session.scalars(select(InvitationRedemption)).one()
            assert record.code_id == code.id
            assert record.user_id == owner.id

    def test_consume_fails_when_exhausted(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        """用尽的码在核销时必须失败 —— 建号会让次数与账号数不符。"""
        with test_db.session() as session:
            code = InvitationRepository().add(session, _code("RACE", used_count=1))
        with test_db.session() as session:
            with pytest.raises(InvitationInvalid):
                service.consume(session, code_id=code.id, user_id=owner.id)

    def test_consume_without_a_code_is_a_noop(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        """升级前建立的占位没有码，仍应能正常核销（1.5 的模型侧）。"""
        with test_db.session() as session:
            service.consume(session, code_id=None, user_id=owner.id)
        with test_db.session() as session:
            assert session.scalars(select(InvitationRedemption)).all() == []


class TestIssuance:
    """2.2 的两条额度。"""

    def test_issue_gives_the_spec_shape(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        with test_db.session() as session:
            code = service.issue_for_user(session, owner=owner, name="给张三")
        assert code.max_uses == USER_CODE_MAX_USES == 1
        assert code.owner_id == owner.id
        assert code.name == "给张三"
        assert code.token and len(code.token) == 10
        delta = code.expires_at - datetime.now(UTC)
        assert timedelta(days=USER_CODE_TTL_DAYS - 1) < delta <= timedelta(
            days=USER_CODE_TTL_DAYS
        )

    def test_only_one_unused_at_a_time(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        with test_db.session() as session:
            service.issue_for_user(session, owner=owner, name="第一张")
        with test_db.session() as session:
            with pytest.raises(InvitationQuota):
                service.issue_for_user(session, owner=owner, name="第二张")

    def test_interval_is_enforced(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        """删掉之后**仍受 24 小时限制** —— 额度看的是"最近一次申请"，不是"当前有没有"。"""
        with test_db.session() as session:
            code = service.issue_for_user(session, owner=owner, name="第一张")
            service.delete_own(session, owner=owner, code_id=code.id)
        with test_db.session() as session:
            with pytest.raises(InvitationQuota):
                service.issue_for_user(session, owner=owner, name="第二张")

    def test_interval_is_based_on_stored_state_not_memory(
        self, test_db: Database, owner: User
    ) -> None:
        """**换一个服务实例（相当于进程重启）之后额度仍然成立。**

        这是"24 小时落库、不用限流器"的全部理由：限流器是内存态，重启即清零。
        """
        first = InvitationService()
        with test_db.session() as session:
            code = first.issue_for_user(session, owner=owner, name="第一张")
            first.delete_own(session, owner=owner, code_id=code.id)

        reborn = InvitationService()  # 全新的实例，没有任何内存状态
        with test_db.session() as session:
            with pytest.raises(InvitationQuota):
                reborn.issue_for_user(session, owner=owner, name="重启后")

    def test_interval_expires(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        with test_db.session() as session:
            session.add(
                _code(
                    "OLD",
                    owner_id=owner.id,
                    days=-1,  # 已经过期，因此不算"未使用"
                )
            )
        # 把创建时间挪到 25 小时前
        with test_db.session() as session:
            code = session.scalars(select(InvitationCode)).one()
            code.created_at = datetime.now(UTC) - timedelta(
                hours=USER_CODE_INTERVAL_HOURS + 1
            )
        with test_db.session() as session:
            fresh = service.issue_for_user(session, owner=owner, name="过了 24 小时")
            assert fresh.id is not None

    def test_issuance_paused_blocks_users(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        with test_db.session() as session:
            PlatformSwitchRepository().set_enabled(
                session, PlatformSwitchKey.INVITATION_ISSUANCE_PAUSED, enabled=True
            )
        with test_db.session() as session:
            with pytest.raises(InvitationIssuancePaused):
                service.issue_for_user(session, owner=owner, name="不该成功")

    def test_name_is_required(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        for bad in ("", "   "):
            with test_db.session() as session:
                with pytest.raises(BadRequest):
                    service.issue_for_user(session, owner=owner, name=bad)


class TestOwnership:
    def test_cannot_delete_someone_elses_code(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        with test_db.session() as session:
            other = _user("bob")
            session.add(other)
            session.flush()
            code = InvitationRepository().add(session, _code("BOBS", owner_id=other.id))
        with test_db.session() as session:
            with pytest.raises(BadRequest):
                service.delete_own(session, owner=owner, code_id=code.id)

    def test_cannot_delete_a_used_code(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        """已用过的码是使用记录的归属 —— 删了它，"谁邀请了他"就没了。"""
        with test_db.session() as session:
            code = InvitationRepository().add(
                session, _code("USEDONE", owner_id=owner.id, used_count=1)
            )
        with test_db.session() as session:
            with pytest.raises(InvitationNotDeletable):
                service.delete_own(session, owner=owner, code_id=code.id)

    def test_delete_hides_the_code_but_keeps_the_row(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        """**软删**：对用户不可见，但行留下当额度证据。

        物理删除会让"每 24 小时只能申请一张"失效 —— 用户只要"申请 → 删除 → 申请"
        就能无限制造邀请码（design.md 决策 9）。
        """
        with test_db.session() as session:
            code = service.issue_for_user(session, owner=owner, name="给张三")
            code_id = code.id
            service.delete_own(session, owner=owner, code_id=code_id)
        with test_db.session() as session:
            row = session.get(InvitationCode, code_id)
            assert row is not None, "行被物理删掉了 —— 额度证据随之消失"
            assert row.deleted_at is not None
            # 用户的列表里看不到它
            assert service.list_own(session, owner=owner) == []


class TestAdminManagement:
    def test_create_accepts_explicit_fields(
        self, test_db: Database, service: InvitationService
    ) -> None:
        with test_db.session() as session:
            admin = _user("root", role=UserRole.ADMIN.value)
            session.add(admin)
            session.flush()
            code = service.create(
                session,
                actor=admin,
                token="CUSTOM-TOKEN",
                name="给社团",
                days=30,
                max_uses=10,
            )
        assert code.token == "CUSTOM-TOKEN"
        assert code.max_uses == 10
        assert code.owner_id is None, "管理员的码不该归个人（决策 8）"

    def test_create_generates_a_token_when_omitted(
        self, test_db: Database, service: InvitationService
    ) -> None:
        with test_db.session() as session:
            admin = _user("root", role=UserRole.ADMIN.value)
            session.add(admin)
            session.flush()
            code = service.create(
                session, actor=admin, token=None, name="自动", days=7, max_uses=1
            )
        assert code.token and len(code.token) == 10

    def test_admin_is_not_subject_to_user_quotas(
        self, test_db: Database, service: InvitationService
    ) -> None:
        """连建多张都成功 —— 管理员建码是运营动作，不是个人分享（决策 8）。"""
        with test_db.session() as session:
            admin = _user("root", role=UserRole.ADMIN.value)
            session.add(admin)
            session.flush()
            for index in range(5):
                service.create(
                    session,
                    actor=admin,
                    token=f"BULK{index}",
                    name=f"第 {index} 张",
                    days=7,
                    max_uses=1,
                )
        with test_db.session() as session:
            assert len(session.scalars(select(InvitationCode)).all()) == 5

    def test_duplicate_token_is_rejected(
        self, test_db: Database, service: InvitationService
    ) -> None:
        with test_db.session() as session:
            admin = _user("root", role=UserRole.ADMIN.value)
            session.add(admin)
            session.flush()
            service.create(
                session, actor=admin, token="SAME", name="一", days=7, max_uses=1
            )
        with test_db.session() as session:
            with pytest.raises(BadRequest):
                service.create(
                    session, actor=admin, token="SAME", name="二", days=7, max_uses=1
                )

    @pytest.mark.parametrize(
        "days,uses",
        [(0, 1), (-1, 1), (99999, 1), (7, 0), (7, -5), (7, 10**9)],
    )
    def test_out_of_range_values_are_rejected(
        self, test_db: Database, service: InvitationService, days: int, uses: int
    ) -> None:
        with test_db.session() as session:
            admin = _user("root", role=UserRole.ADMIN.value)
            session.add(admin)
            session.flush()
            with pytest.raises(BadRequest):
                service.create(
                    session,
                    actor=admin,
                    token=None,
                    name="越界",
                    days=days,
                    max_uses=uses,
                )

    def test_non_admin_cannot_manage(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        with test_db.session() as session:
            with pytest.raises(Forbidden):
                service.create(
                    session,
                    actor=owner,
                    token=None,
                    name="偷建",
                    days=7,
                    max_uses=1,
                )
        with test_db.session() as session:
            with pytest.raises(Forbidden):
                service.set_switch(
                    session,
                    actor=owner,
                    key=PlatformSwitchKey.INVITATIONS_PAUSED,
                    enabled=True,
                )

    def test_revoke_keeps_records(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        """失效而不删除 —— 已产生的使用记录必须留住归属（决策 9）。"""
        with test_db.session() as session:
            admin = _user("root", role=UserRole.ADMIN.value)
            session.add(admin)
            session.flush()
            code = InvitationRepository().add(session, _code("TOREVOKE", used_count=1))
            InvitationRedemptionRepository().add(
                session, InvitationRedemption(code_id=code.id, user_id=owner.id)
            )
            service.revoke(session, actor=admin, code_id=code.id)
        with test_db.session() as session:
            assert session.get(InvitationCode, code.id).revoked_at is not None
            assert len(session.scalars(select(InvitationRedemption)).all()) == 1


class TestRegistrationGateThroughApi:
    """2.3 / 2.4 / 2.5：准入接进两阶段注册之后的行为。

    走 API 而不是直接调服务：这三条里有两条是关于**响应体**的（失败提示必须一致、
    失败必须落在字段上），只有从端点上才看得到。
    """

    API = "/api/v1"

    def _payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "username": "newcomer",
            "password": "correct-horse",
            "email": "newcomer@example.com",
        }
        base.update(overrides)
        return base

    def test_missing_code_is_rejected_and_leaves_nothing(
        self, client, test_db: Database, sent_emails
    ) -> None:
        """不合格的人不该进到"等邮件"那一步 —— 不建占位、不发信。"""
        response = client.post(f"{self.API}/auth/register", json=self._payload())

        assert response.status_code == 422
        assert response.json()["error"]["fields"]["invitation_code"] == "邀请码不可用"
        with test_db.session() as session:
            assert session.scalars(select(PendingRegistration)).all() == []
        assert sent_emails.sent == []

    def test_valid_code_creates_a_pending_that_carries_it(
        self, client, test_db: Database, invitation_code, sent_emails
    ) -> None:
        token = invitation_code()
        response = client.post(
            f"{self.API}/auth/register",
            json=self._payload(invitation_code=token),
        )

        assert response.status_code == 202
        with test_db.session() as session:
            pending = session.scalars(select(PendingRegistration)).one()
            code = session.scalars(select(InvitationCode)).one()
            assert pending.invitation_code_id == code.id
            # 一阶段**不消耗**：次数要等核销建号才扣
            assert code.used_count == 0

    def test_every_rejection_looks_identical(
        self, client, test_db: Database, invitation_code
    ) -> None:
        """**安全边界**：四种原因 + 平台暂停，响应体必须逐字相同。

        区分它们等于把注册端点变成邀请码枚举器 —— 而且失效时没有任何报错。
        """
        invitation_code(token="EXPIREDONE", days=-1)
        invitation_code(token="USEDUPONE", max_uses=1)
        with test_db.session() as session:
            code = session.scalars(
                select(InvitationCode).where(InvitationCode.token == "USEDUPONE")
            ).one()
            code.used_count = 1
        invitation_code(token="REVOKEDONE")
        with test_db.session() as session:
            code = session.scalars(
                select(InvitationCode).where(InvitationCode.token == "REVOKEDONE")
            ).one()
            code.revoked_at = datetime.now(UTC)

        bodies = []
        for token in ("NOPE", "EXPIREDONE", "USEDUPONE", "REVOKEDONE"):
            response = client.post(
                f"{self.API}/auth/register",
                json=self._payload(username=f"u{token[:4]}", invitation_code=token),
            )
            bodies.append(response.json())

        # 暂停之后，连一张**本来有效**的码也给同一句
        invitation_code(token="STILLOKAY1")
        with test_db.session() as session:
            PlatformSwitchRepository().set_enabled(
                session, PlatformSwitchKey.INVITATIONS_PAUSED, enabled=True
            )
        paused = client.post(
            f"{self.API}/auth/register",
            json=self._payload(username="paused", invitation_code="STILLOKAY1"),
        )
        bodies.append(paused.json())

        assert all(body == bodies[0] for body in bodies), bodies

    def test_verification_consumes_the_code(
        self, client, test_db: Database, invitation_code, sent_emails
    ) -> None:
        """2.4：核销建号时扣次数，并写下使用记录。"""
        token = invitation_code()
        assert (
            client.post(
                f"{self.API}/auth/register",
                json=self._payload(invitation_code=token),
            ).status_code
            == 202
        )
        with test_db.session() as session:
            assert session.scalars(select(InvitationCode)).one().used_count == 0

        link = verification_token(sent_emails, "newcomer@example.com")
        assert (
            client.post(
                f"{self.API}/auth/register/verify", json={"token": link}
            ).status_code
            == 204
        )

        with test_db.session() as session:
            code = session.scalars(select(InvitationCode)).one()
            assert code.used_count == 1
            record = session.scalars(select(InvitationRedemption)).one()
            assert record.code_id == code.id
            user = session.scalars(select(User).where(User.username == "newcomer")).one()
            assert record.user_id == user.id

    def test_failed_account_creation_does_not_consume(
        self, client, test_db: Database, invitation_code, sent_emails
    ) -> None:
        """2.4 + 1.5：建号失败时次数不扣、**占位仍在**、链接仍可重试。

        这条同时是 1.5 的验证：升级前建立的占位（`invitation_code_id` 为空）也走
        同一条路，因此不会因为多了一列而核销不了。
        """
        token = invitation_code()
        assert (
            client.post(
                f"{self.API}/auth/register",
                json=self._payload(invitation_code=token),
            ).status_code
            == 202
        )
        link = verification_token(sent_emails, "newcomer@example.com")

        # 占位存续期间，有人抢注了同一个用户名
        with test_db.session() as session:
            session.add(_user("newcomer"))

        failed = client.post(f"{self.API}/auth/register/verify", json={"token": link})
        assert failed.status_code == 409

        with test_db.session() as session:
            assert session.scalars(select(InvitationCode)).one().used_count == 0
            assert session.scalars(select(PendingRegistration)).one() is not None

    def test_legacy_pending_without_a_code_still_verifies(
        self, client, test_db: Database, sent_emails
    ) -> None:
        """1.5：升级前建立的占位（没有码）仍然核销得了。

        准入是在**提交注册**那一刻判定的，不该回头把已经在等邮件的人挡掉。
        """
        from app.core.security import generate_token, hash_token

        with test_db.session() as session:
            plain = generate_token()
            session.add(
                PendingRegistration(
                    username="legacy",
                    email="legacy@example.com",
                    password_hash=hash_password("correct-horse"),
                    display_name="Legacy",
                    token_hash=hash_token(plain),
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                    invitation_code_id=None,
                )
            )
        assert (
            client.post(
                f"{self.API}/auth/register/verify", json={"token": plain}
            ).status_code
            == 204
        )
        with test_db.session() as session:
            assert session.scalars(select(User).where(User.username == "legacy")).one()


class TestConcurrency:
    """6.1：并发抢最后一格。

    **必须真并发。** 顺序调用区分不出"单语句条件更新"与"先查再写" —— 两者在单线程
    下的结果完全一样。因此这里用两个线程、两个会话、一道栅栏，让它们尽可能同时进入
    扣减那一步。
    """

    def test_only_one_thread_gets_the_last_slot(self, test_db: Database) -> None:
        repo = InvitationRepository()
        with test_db.session() as session:
            code = repo.add(session, _code("LASTONE001", max_uses=1))
            code_id = code.id

        barrier = threading.Barrier(2, timeout=10)
        results: list[bool] = []
        lock = threading.Lock()

        def grab() -> None:
            with test_db.session() as session:
                barrier.wait()  # 尽量让两边同时进入
                won = repo.claim_use(session, code_id=code_id, now=datetime.now(UTC))
            with lock:
                results.append(won)

        threads = [threading.Thread(target=grab) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert sorted(results) == [False, True], f"两个线程的结果：{results}"
        with test_db.session() as session:
            assert session.get(InvitationCode, code_id).used_count == 1, (
                "一张一次性的码被用出了不止一次"
            )

    def test_used_count_never_exceeds_max(self, test_db: Database) -> None:
        """四个线程抢两次可用次数：恰好两个成功，且计数不超过上限。"""
        repo = InvitationRepository()
        with test_db.session() as session:
            code_id = repo.add(session, _code("FOUROF2", max_uses=2)).id

        barrier = threading.Barrier(4, timeout=10)
        results: list[bool] = []
        lock = threading.Lock()

        def grab() -> None:
            with test_db.session() as session:
                barrier.wait()
                won = repo.claim_use(session, code_id=code_id, now=datetime.now(UTC))
            with lock:
                results.append(won)

        threads = [threading.Thread(target=grab) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert results.count(True) == 2, f"四个线程的结果：{results}"
        with test_db.session() as session:
            assert session.get(InvitationCode, code_id).used_count == 2


class TestIntervalSurvivesRestart:
    """6.2：24 小时间隔跨进程重启仍在。

    **这条是"落库而不是用限流器"的全部理由。** 限流器是进程内内存态，换一个实例
    （相当于重启）就清零 —— 那样一条业务规则可以被一次重启绕过。
    """

    def test_a_fresh_service_still_refuses(
        self, test_db: Database, owner: User
    ) -> None:
        first = InvitationService()
        with test_db.session() as session:
            code = first.issue_for_user(session, owner=owner, name="重启前")
            first.delete_own(session, owner=owner, code_id=code.id)

        # 全新的服务实例：没有任何内存状态
        reborn = InvitationService()
        with test_db.session() as session:
            with pytest.raises(InvitationQuota):
                reborn.issue_for_user(session, owner=owner, name="重启后")

    def test_the_evidence_is_still_in_the_database(
        self, test_db: Database, owner: User
    ) -> None:
        """删掉的码仍然留着当证据 —— 这是软删的存在理由（design 决策 9）。"""
        service = InvitationService()
        with test_db.session() as session:
            code = service.issue_for_user(session, owner=owner, name="重启前")
            code_id = code.id
            service.delete_own(session, owner=owner, code_id=code_id)

        with test_db.session() as session:
            row = session.get(InvitationCode, code_id)
            assert row is not None, "行被物理删掉了，24 小时限制随之失效"
            assert row.deleted_at is not None
            # 而它对用户不可见
            assert InvitationRepository().list_for_owner(session, owner.id) == []


class TestUsageReporting:
    """使用信息：被哪个 username、在何时。"""

    def test_usages_carry_username_and_time(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        with test_db.session() as session:
            code = InvitationRepository().add(
                session, _code("REPORT", owner_id=owner.id, used_count=1)
            )
            InvitationRedemptionRepository().add(
                session,
                InvitationRedemption(
                    code_id=code.id,
                    user_id=owner.id,
                    used_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
                ),
            )
        with test_db.session() as session:
            views = service.list_own(session, owner=owner)
        assert len(views) == 1
        assert [u.username for u in views[0].usages] == ["alice"]
        assert views[0].usages[0].used_at == datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    def test_deleted_user_still_leaves_the_record(
        self, test_db: Database, service: InvitationService, owner: User
    ) -> None:
        """使用者注销后记录仍在，只是用户名变成 None —— 不假装有人用过，也不抹掉。"""
        with test_db.session() as session:
            guest = _user("guest")
            session.add(guest)
            session.flush()
            code = InvitationRepository().add(
                session, _code("GONE", owner_id=owner.id, used_count=1)
            )
            InvitationRedemptionRepository().add(
                session, InvitationRedemption(code_id=code.id, user_id=guest.id)
            )
        with test_db.session() as session:
            session.delete(
                session.scalars(select(User).where(User.username == "guest")).one()
            )
        with test_db.session() as session:
            views = service.list_own(session, owner=owner)
        assert len(views[0].usages) == 1
        assert views[0].usages[0].username is None

    def test_remaining_is_clamped(self, test_db: Database) -> None:
        from app.services.invitations import CodeView

        assert CodeView(code=_code(max_uses=1, used_count=1)).remaining == 0
        assert CodeView(code=_code(max_uses=1, used_count=3)).remaining == 0
        assert CodeView(code=_code(max_uses=5, used_count=2)).remaining == 3
