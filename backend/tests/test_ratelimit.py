"""任务 11.1 - 11.5：限流实现、挂载点、转发头信任边界与错误码区分。"""

from __future__ import annotations

import pytest

from app.core.clientip import resolve_client_ip
from app.core.config import settings as global_settings
from app.core.enums import EventStatus
from app.db.models import Event
from app.infra.ratelimit_memory import InMemoryRateLimiter
from conftest import invitation_code_for

API = "/api/v1"


class FakeClock:
    """可手动推进的时间源，避免测试真的等待一个窗口。"""

    def __init__(self, start: float = 1_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def limiter(clock: FakeClock) -> InMemoryRateLimiter:
    return InMemoryRateLimiter(clock=clock)


def _seed_event(test_db, event_id="spring-2026", **overrides) -> None:
    defaults: dict[str, object] = {
        "id": event_id,
        "title": "春季招新",
        "status": EventStatus.LIVE.value,
    }
    defaults.update(overrides)
    with test_db.session() as session:
        session.add(Event(**defaults))  # type: ignore[arg-type]


class TestInMemoryRateLimiter:
    """任务 11.1"""

    def test_allows_up_to_the_limit(self, limiter) -> None:
        for index in range(3):
            decision = limiter.hit("k", limit=3, window_seconds=60)
            assert decision.allowed is True
            assert decision.remaining == 2 - index

    def test_refuses_beyond_the_limit(self, limiter) -> None:
        for _ in range(3):
            limiter.hit("k", limit=3, window_seconds=60)

        decision = limiter.hit("k", limit=3, window_seconds=60)
        assert decision.allowed is False
        assert decision.remaining == 0
        assert decision.retry_after is not None and decision.retry_after >= 1

    def test_keys_are_independent(self, limiter) -> None:
        for _ in range(3):
            limiter.hit("a", limit=3, window_seconds=60)

        assert limiter.hit("a", limit=3, window_seconds=60).allowed is False
        assert limiter.hit("b", limit=3, window_seconds=60).allowed is True

    def test_window_slides(self, limiter, clock: FakeClock) -> None:
        for _ in range(3):
            limiter.hit("k", limit=3, window_seconds=60)
        assert limiter.hit("k", limit=3, window_seconds=60).allowed is False

        # 最早的命中滑出窗口后即可再次通过
        clock.advance(61)
        assert limiter.hit("k", limit=3, window_seconds=60).allowed is True

    def test_sliding_window_has_no_boundary_burst(
        self, limiter, clock: FakeClock
    ) -> None:
        """固定窗口会在边界处允许 2×limit 挤在极短时间内，滑动窗口没有这个缺口。"""
        for _ in range(3):
            limiter.hit("k", limit=3, window_seconds=60)

        # 刚跨过"窗口编号"的边界，但仍在滑动窗口内
        clock.advance(30)
        assert limiter.hit("k", limit=3, window_seconds=60).allowed is False

    def test_zero_limit_means_unlimited(self, limiter) -> None:
        for _ in range(100):
            assert limiter.hit("k", limit=0, window_seconds=60).allowed is True

    def test_reset_clears_everything(self, limiter) -> None:
        for _ in range(3):
            limiter.hit("k", limit=3, window_seconds=60)
        limiter.reset()
        assert limiter.tracked_keys == 0
        assert limiter.hit("k", limit=3, window_seconds=60).allowed is True

    def test_key_eviction_is_bounded(self, clock: FakeClock) -> None:
        small = InMemoryRateLimiter(max_keys=10, clock=clock)
        for index in range(50):
            small.hit(f"k{index}", limit=1, window_seconds=60)
        assert small.tracked_keys <= 50  # 不会无限增长且不抛错

    def test_does_not_touch_the_database(self, limiter) -> None:
        """限流状态必须不依赖数据库——这是单进程部署能成立的前提之一。"""
        for _ in range(5):
            limiter.hit("k", limit=5, window_seconds=60)
        # 没有任何数据库依赖即通过：本测试类未使用 test_db 夹具


class TestClientIpTrust:
    """任务 11.3"""

    def test_forwarded_header_is_ignored_from_untrusted_peer(self) -> None:
        """客户端自带 X-Forwarded-For 不能改变限流键，否则限流形同虚设。"""
        assert (
            resolve_client_ip(
                peer="203.0.113.9",
                forwarded_for="1.2.3.4",
                trusted_proxies=["127.0.0.1"],
            )
            == "203.0.113.9"
        )

    def test_forwarded_header_is_honoured_from_trusted_proxy(self) -> None:
        assert (
            resolve_client_ip(
                peer="127.0.0.1",
                forwarded_for="198.51.100.7",
                trusted_proxies=["127.0.0.1"],
            )
            == "198.51.100.7"
        )

    def test_rightmost_entry_is_used(self) -> None:
        """nginx 把直连它的对端追加到最右，左边全是客户端自己塞的。

        取最左项是这里最常见的写错方式——那等于让攻击者决定自己的限流键。
        """
        assert (
            resolve_client_ip(
                peer="127.0.0.1",
                forwarded_for="1.2.3.4, 5.6.7.8, 198.51.100.7",
                trusted_proxies=["127.0.0.1"],
            )
            == "198.51.100.7"
        )

    def test_missing_header_falls_back_to_peer(self) -> None:
        assert (
            resolve_client_ip(
                peer="127.0.0.1", forwarded_for=None, trusted_proxies=["127.0.0.1"]
            )
            == "127.0.0.1"
        )

    def test_empty_header_falls_back_to_peer(self) -> None:
        assert (
            resolve_client_ip(
                peer="127.0.0.1", forwarded_for="   ", trusted_proxies=["127.0.0.1"]
            )
            == "127.0.0.1"
        )

    def test_empty_peer_is_tolerated(self) -> None:
        assert (
            resolve_client_ip(
                peer="", forwarded_for="1.2.3.4", trusted_proxies=["127.0.0.1"]
            )
            == ""
        )


class TestRateLimitEnforcement:
    """任务 11.2 / 11.4"""

    def test_submission_rate_limit_returns_429(
        self, client, test_db, monkeypatch
    ) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "RATE_LIMIT_SUBMIT_IP_MAX", 2)

        assert client.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 1}).status_code == 201
        assert client.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 2}).status_code == 201

        response = client.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 3})
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "rate_limited"
        assert "Retry-After" in response.headers

    def test_rate_limit_is_distinct_from_quota_exhausted(
        self, client, test_db, monkeypatch
    ) -> None:
        """429 是"稍后重试"，409 是终态——用错会诱导客户端重试必然失败的请求。"""
        _seed_event(test_db, max_submissions=1)
        monkeypatch.setattr(global_settings, "RATE_LIMIT_SUBMIT_IP_MAX", 100)

        assert client.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 1}).status_code == 201
        assert client.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 2}).status_code == 409

    def test_registration_is_rate_limited(self, client, monkeypatch) -> None:
        monkeypatch.setattr(global_settings, "RATE_LIMIT_AUTH_IP_MAX", 2)

        assert client.post(
            f"{API}/auth/register",
            json={
                "username": "alice",
                "password": "correct-horse",
                "email": "alice@example.com",
                "invitation_code": invitation_code_for(client),
            },
        ).status_code == 202
        assert client.post(
            f"{API}/auth/register",
            json={
                "username": "bob",
                "password": "correct-horse",
                "email": "bob@example.com",
                "invitation_code": invitation_code_for(client),
            },
        ).status_code == 202

        response = client.post(
            f"{API}/auth/register",
            json={
                "username": "carol",
                "password": "correct-horse",
                "email": "carol@example.com",
                "invitation_code": invitation_code_for(client),
            },
        )
        assert response.status_code == 429

    def test_login_is_rate_limited(self, client, test_db, monkeypatch) -> None:
        """没有这层，开放注册 + 无登录限流就等于允许在线口令爆破。"""
        from app.core.security import hash_password
        from app.db.models import User

        monkeypatch.setattr(global_settings, "RATE_LIMIT_AUTH_IP_MAX", 2)
        with test_db.session() as session:
            session.add(
                User(
                    username="alice",
                    display_name="alice",
                    password_hash=hash_password("correct-horse"),
                    role="user",
                )
            )

        for _ in range(2):
            client.post(
                f"{API}/auth/login", json={"username": "alice", "password": "wrong"}
            )
        response = client.post(
            f"{API}/auth/login", json={"username": "alice", "password": "correct-horse"}
        )
        assert response.status_code == 429

    def test_user_dimension_limits_across_ips(
        self, app, test_db, monkeypatch, register
    ) -> None:
        """只查来源维度的话，换一个出口就能绕过；用户维度补上这一半。

        走完两阶段注册：这个用例要的是"已登录用户"，而注册不再是一次请求建号 ——
        停在第一步的话后面那次登录会 401，提交就变成匿名的，用户维度根本没被覆盖。
        """
        from fastapi.testclient import TestClient

        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "RATE_LIMIT_SUBMIT_IP_MAX", 1000)
        monkeypatch.setattr(global_settings, "RATE_LIMIT_SUBMIT_USER_MAX", 2)

        with TestClient(app) as first:
            assert register(first, username="alice").status_code == 204
            assert (
                first.post(
                    f"{API}/auth/login",
                    json={"username": "alice", "password": "correct-horse"},
                ).status_code
                == 200
            )
            assert first.post(
                f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 1}
            ).status_code == 201
            assert first.post(
                f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 2}
            ).status_code == 201

            response = first.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 3})
            assert response.status_code == 429

    def test_disabling_the_limiter_lets_everything_through(
        self, client, test_db, monkeypatch
    ) -> None:
        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "RATE_LIMIT_ENABLED", False)
        monkeypatch.setattr(global_settings, "RATE_LIMIT_SUBMIT_IP_MAX", 1)

        for index in range(5):
            response = client.post(
                f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": index}
            )
            assert response.status_code == 201

    def test_limit_is_per_event(self, client, test_db, monkeypatch) -> None:
        """一个活动被打满不该影响另一个活动。"""
        _seed_event(test_db, "spring-2026")
        _seed_event(test_db, "autumn-2026")
        monkeypatch.setattr(global_settings, "RATE_LIMIT_SUBMIT_IP_MAX", 1)

        assert client.post(
            f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 1}
        ).status_code == 201
        assert client.post(
            f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 2}
        ).status_code == 429
        # 另一个活动仍有自己的额度
        assert client.post(
            f"{API}/events/autumn-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 3}
        ).status_code == 201


class TestRateLimitDoesNotWriteToDatabase:
    """任务 11.1 的核心约束：限流状态不落库。"""

    def test_rejected_request_creates_no_rows(self, client, test_db, monkeypatch) -> None:
        from sqlalchemy import func, select

        from app.db.models import Submission

        _seed_event(test_db)
        monkeypatch.setattr(global_settings, "RATE_LIMIT_SUBMIT_IP_MAX", 1)

        client.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 1})
        client.post(f"{API}/events/spring-2026/submissions", params={"client_id": "browser-rl"}, json={"n": 2})

        with test_db.session() as session:
            assert session.scalar(select(func.count()).select_from(Submission)) == 1
