"""2026-09-18 新增：STS 签发自检端点（`GET /api/v1/files/sts-health`）单测。

覆盖 `service/sts.StsService.sts_health`：

- 成功路径 → fake SDK 返合法 creds，断言响应 `status="ok"` + probe_prefix
  满足 `tmp/__sts_healthcheck__/<32-hex>/probe` 形态 + `expired_at` 与 SDK
  返回的 `expiredTime` 对齐；同时断言 SDK 收到 `duration_seconds=60` +
  `allow_prefix=[f"{probe_prefix}/*"]`。
- SDK 抛错 → `BizError(BIZ_STS_GRANT_FAILED, 502)` 透传，不被 service 层
  二次包装。
- 连续两次调用 → probe_prefix 不同（uuid4 防重）。
- 命名空间隔离断言：probe_prefix 命中现有 schema 校验（`tmp/` 开头 +
  含子目录段 + 无通配字符），未来若 schema / service 校验收紧应同步调整。

测试不打 DB（STS 端口零 DB IO），不需要 docker；放 tests/unit/，复用
`tests/unit/conftest.py` 跳过父 conftest 的 postgres-test 生命周期。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import pytest

import core.sts as sts_core
from core.error_code import ErrCode
from core.exception import BizError

pytestmark = pytest.mark.asyncio


# ============================================================
# Mock SDK
# ============================================================
@dataclass
class _FakeStsCall:
    """记录 SDK 一次调用的 config 副本，供单测断言。"""

    config: dict[str, Any]


@dataclass
class _FakeStsClient:
    config: dict[str, Any]
    recorder: _FakeStsRecorder

    def get_credential(self) -> dict[str, Any]:
        self.recorder.calls.append(_FakeStsCall(config=dict(self.config)))
        start = 1700000000
        return {
            "credentials": {
                "tmpSecretId": "STS.fake-id",
                "tmpSecretKey": "fake-key",
                "sessionToken": "fake-token",
            },
            "startTime": start,
            "expiredTime": start + self.config["duration_seconds"],
        }


@dataclass
class _FakeStsRecorder:
    calls: list[_FakeStsCall] = field(default_factory=list)


@pytest.fixture
def fake_sts(monkeypatch: pytest.MonkeyPatch) -> _FakeStsRecorder:
    """把 `core.sts._CosSts` 替换成 `_FakeStsClient` 工厂。"""

    recorder = _FakeStsRecorder()

    def _factory(config: dict[str, Any]) -> _FakeStsClient:
        return _FakeStsClient(config=config, recorder=recorder)

    monkeypatch.setattr(sts_core, "_CosSts", _factory)
    return recorder


# 2026-09-18 review：probe prefix 形态固定断言——既保证「满足现有 schema /
# service 校验」（未来收紧可同步发现），又显式声明设计意图。
_PROBE_PREFIX_RE = re.compile(r"^tmp/__sts_healthcheck__/[0-9a-f]{32}/probe$")


# ============================================================
# 成功路径
# ============================================================
class TestStsHealthSuccess:
    """2026-09-18：mock SDK 返合法 creds → 响应 status="ok" + probe_prefix
    + expired_at，且 SDK 收到正确的 policy config。"""

    async def test_returns_ok_with_probe_prefix_and_expired_at(
        self, fake_sts: _FakeStsRecorder
    ) -> None:
        from service.sts import StsService

        svc = StsService()
        resp = await svc.sts_health()

        # ---- 响应契约 ----
        assert resp.status == "ok"
        assert _PROBE_PREFIX_RE.match(resp.probe_prefix), (
            f"probe_prefix {resp.probe_prefix!r} 不匹配命名空间约定"
        )
        assert resp.expired_at == 1700000060  # 1700000000 + duration_seconds=60

        # ---- SDK 收到的 config ----
        assert len(fake_sts.calls) == 1
        cfg = fake_sts.calls[0].config
        # 2026-09-18：healthcheck 必须用 60s TTL（短 TTL，凭证即使未被销毁
        # 也很快失效）。
        assert cfg["duration_seconds"] == 60
        # policy resource 收口必须对齐 probe_prefix。
        assert cfg["allow_prefix"] == [f"{resp.probe_prefix}/*"]
        assert "policy" in cfg
        stmt = cfg["policy"]["statement"][0]
        # policy.resource 形如 `qcs::cos:{region}:uid/{appid}:{bucket}/{prefix}*`
        # ——包含 probe_prefix。
        assert any(resp.probe_prefix in r for r in stmt["resource"])


# ============================================================
# SDK 抛错路径
# ============================================================
class TestStsHealthSdkFailure:
    """2026-09-18：SDK 抛错 → BizError 透传（自带 http_status=502 + 业务码
    BIZ_STS_GRANT_FAILED），service 层不重新包装。"""

    async def test_sdk_exception_propagates_as_biz_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _FakeStsRecorder()

        def _boom(config: dict[str, Any]) -> _FakeStsClient:
            class _Boom:
                def __init__(self, cfg: dict[str, Any]) -> None:
                    self.cfg = cfg

                def get_credential(self) -> dict[str, Any]:
                    recorder.calls.append(_FakeStsCall(config=dict(self.cfg)))
                    raise RuntimeError("network boom")

            return _Boom(config)

        monkeypatch.setattr(sts_core, "_CosSts", _boom)

        from service.sts import StsService

        svc = StsService()
        with pytest.raises(BizError) as exc:
            await svc.sts_health()
        # BizError 透传：业务码 + http_status 必须与 core 层一致（502）。
        assert exc.value.code == ErrCode.BIZ_STS_GRANT_FAILED
        assert exc.value.http_status == 502
        # 异常消息只暴露 SDK 异常类型名，不泄漏完整 repr（含凭证 / policy 痕迹）。
        assert "RuntimeError" in exc.value.message
        assert "network boom" not in exc.value.message


# ============================================================
# probe_prefix 唯一性
# ============================================================
class TestStsHealthUniqueProbePrefix:
    """2026-09-18：连续两次调用 → probe_prefix 不同（uuid4 防重）。"""

    async def test_two_consecutive_calls_have_different_probe_prefixes(
        self, fake_sts: _FakeStsRecorder
    ) -> None:
        from service.sts import StsService

        svc = StsService()
        resp_a = await svc.sts_health()
        resp_b = await svc.sts_health()

        assert resp_a.probe_prefix != resp_b.probe_prefix
        # 两次调用都成功，且两次都实际打到 SDK（验证 unique 不是 mock 跳过）。
        assert len(fake_sts.calls) == 2
        # 两次 SDK config 的 allow_prefix 也必须不同。
        assert (
            fake_sts.calls[0].config["allow_prefix"]
            != (fake_sts.calls[1].config["allow_prefix"])
        )
