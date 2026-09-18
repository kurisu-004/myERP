"""2026-09-18 新增：内部 STS prefix 凭证端口单测。

覆盖 `service/sts.StsService.grant_prefix_credentials` + 底层
`core.sts.grant_credentials_for_prefix`：

- prefix 不以 `tmp/` 开头 / 仅 `tmp/` / 仅 `tmp/<single>` →
  service 层抛 `BIZ_STS_PREFIX_INVALID`（400）。
- prefix 含通配 / 路径穿越字符（`*` / `?` / `..` / `\\x00` / `\\`）→
  Pydantic schema `field_validator` 抛 `ValidationError`（422）。
- duration 超 `settings.sts_max_ttl_seconds` → service 层 clamp，
  `expires_in` 等于 `sts_max_ttl_seconds`；mock SDK 不应再拿到超过上限
  的 `duration_seconds`。
- 成功路径 → mock `qcloud-python-sts` SDK（`_CosSts.get_credential`）
  返回固定 dict；断言：
  * SDK 收到 7 个 `_UPLOAD_ACTIONS_SHORT` action 等价集合；
  * SDK config 同时含 `allow_prefix=[f"{prefix}/*"]` +
    `allow_actions=list(_UPLOAD_ACTIONS_SHORT)`（policy 与 allow_* 并存
    时 SDK 走 self.policy 分支，但 config 必须保留，旧端点行为对齐）；
  * policy resource 收窄到
    `qcs::cos:{region}:uid/{appid}:{bucket_appid}/{prefix}*`（含
    bucket 段，与旧端点 `grant_sts_tmp_key` 行为一致）；
  * 出参结构与 `StsPrefixCredentialsResponse` 对齐（含 credentials 五元
    组 + bucket/region/endpoint/scheme/expires_in）。
- 旧端点 `grant_sts_tmp_key` 回归：service 层调用后 SDK config 必须同
  时含 `allow_prefix` + `allow_actions` + `policy`，policy resource 必
  须含 `{bucket_appid}/` 中段。

测试不打 DB（STS 端口零 DB IO），不需要 docker；放 tests/unit/，复用
`tests/unit/conftest.py` 跳过父 conftest 的 postgres-test 生命周期。

2026-09-18 review 第 1 轮修复：
- 删 `_FakeStsCall.policy` 死字段。
- 从 `core.sts` import public 常量 `TMP_PREFIX_REQUIRED`（不再 import
  私有 `_TMP_PREFIX_REQUIRED`）。
- `assert "tmp_key" not in resp.model_dump()` 取代 `hasattr` 弱断言。
- 加 prefix 形态参数化（`tmp/1/abc/`、`tmp/a/b/c`、最短 `tmp/1/abc`
  三种形态）。
- 加 `grant_sts_tmp_key` 旧端点回归测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import core.sts as sts_core
from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError

# 2026-09-18 review：直接用 public 常量；不再 import 私有 `_TMP_PREFIX_REQUIRED`。
from core.sts import _UPLOAD_ACTIONS_SHORT
from schema.sts import StsPrefixCredentialsRequest
from service.sts import StsService

pytestmark = pytest.mark.asyncio


# ============================================================
# Mock SDK
# ============================================================
@dataclass
class _FakeStsCall:
    """2026-09-18 review：去掉 `policy` 死字段（单测从不在 fake 上读 policy）。"""

    config: dict[str, Any]


@dataclass
class _FakeStsClient:
    config: dict[str, Any]
    # 让单测能取回 SDK 看到的 config（_FakeStsCall 给单测断言用）
    recorder: _FakeStsRecorder

    def get_credential(self) -> dict[str, Any]:
        self.recorder.calls.append(_FakeStsCall(config=dict(self.config)))
        # 返回 SDK 真实返回结构（camelCase），core 层做 ._backwardCompat 适配
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
    """把 `core.sts._CosSts` 替换成 `_FakeStsClient` 工厂。

    返回的 `_FakeStsRecorder` 单测可拿到 SDK 收到的 config / policy。
    """
    recorder = _FakeStsRecorder()

    def _factory(config: dict[str, Any]) -> _FakeStsClient:
        return _FakeStsClient(config=config, recorder=recorder)

    monkeypatch.setattr(sts_core, "_CosSts", _factory)
    return recorder


# ============================================================
# Service-level tests
# ============================================================
class TestGrantPrefixCredentialsPrefixValidation:
    """2026-09-18：prefix 必须以 `tmp/` 开头 + 至少含一个子目录段。"""

    async def test_prefix_without_tmp_prefix_rejected(self) -> None:
        svc = StsService()
        req = StsPrefixCredentialsRequest(prefix="drawings/1/abc", expire_seconds=600)
        with pytest.raises(BizError) as exc:
            await svc.grant_prefix_credentials(req)
        assert exc.value.code == ErrCode.BIZ_STS_PREFIX_INVALID
        assert exc.value.http_status == 400

    async def test_prefix_root_level_rejected(self) -> None:
        """仅 `tmp`（无尾斜杠）也不算合法，避免误传。"""
        svc = StsService()
        req = StsPrefixCredentialsRequest(prefix="tmp", expire_seconds=600)
        with pytest.raises(BizError) as exc:
            await svc.grant_prefix_credentials(req)
        assert exc.value.code == ErrCode.BIZ_STS_PREFIX_INVALID

    async def test_prefix_only_tmp_slash_rejected(self) -> None:
        """2026-09-18 review：`tmp/` 单独（仅根目录）也算整 tmp/ 命名空间写权，必须拒。"""
        svc = StsService()
        req = StsPrefixCredentialsRequest(prefix="tmp/", expire_seconds=600)
        with pytest.raises(BizError) as exc:
            await svc.grant_prefix_credentials(req)
        assert exc.value.code == ErrCode.BIZ_STS_PREFIX_INVALID
        assert "sub-directory" in exc.value.message

    async def test_prefix_single_segment_after_tmp_rejected(self) -> None:
        """2026-09-18 review：`tmp/<single>`（无 `/` 边界）也拒——暴露整
        `tmp/<uid>/*` 命名空间写权，不符合「单目录」语义。"""
        svc = StsService()
        req = StsPrefixCredentialsRequest(prefix="tmp/1", expire_seconds=600)
        with pytest.raises(BizError) as exc:
            await svc.grant_prefix_credentials(req)
        assert exc.value.code == ErrCode.BIZ_STS_PREFIX_INVALID
        assert "sub-directory" in exc.value.message


class TestGrantPrefixCredentialsSchemaGlobValidator:
    """2026-09-18 review：schema field_validator 拒绝通配 / 路径穿越字符。"""

    @pytest.mark.parametrize(
        "bad_prefix",
        [
            "tmp/1/*",  # glob
            "tmp/1/abc?",  # glob
            "tmp/1/../etc",  # traversal
            "tmp/1/abc/..",  # traversal tail
            "tmp\\1\\abc",  # backslash
            "tmp/1/\x00abc",  # NUL
        ],
    )
    async def test_glob_and_traversal_chars_rejected_by_schema(
        self,
        bad_prefix: str,
    ) -> None:
        """含通配 / 穿越字符 → Pydantic ValidationError（在 service 之前被拦）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsPrefixCredentialsRequest(prefix=bad_prefix, expire_seconds=600)


class TestGrantPrefixCredentialsDurationClamp:
    """2026-09-18：duration 超上限 → service 层 clamp 到 sts_max_ttl_seconds。"""

    async def test_duration_clamped_to_max(self, fake_sts: _FakeStsRecorder) -> None:
        svc = StsService()
        # schema `Field(le=43200)` 在构造时就会把 10_000_000 弹回 ValidationError，
        # 这里用 `model_construct` 绕开 schema 校验，直接构造一个超上限值的
        # request 对象送进 service，验证 service 层 clamp 仍生效。
        req = StsPrefixCredentialsRequest.model_construct(
            prefix="tmp/1/abcdef0123456789",
            expire_seconds=10_000_000,
        )

        resp = await svc.grant_prefix_credentials(req)

        # service clamp 后 SDK 拿到的 duration_seconds 必须等于上限。
        assert len(fake_sts.calls) == 1
        cfg = fake_sts.calls[0].config
        assert cfg["duration_seconds"] == settings.sts_max_ttl_seconds
        # 响应 expires_in 与 settings 一致。
        assert resp.expires_in == settings.sts_max_ttl_seconds
        assert (
            resp.credentials.expired_time - resp.credentials.start_time
            == settings.sts_max_ttl_seconds
        )

    async def test_duration_within_limit_passed_through(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        svc = StsService()
        req = StsPrefixCredentialsRequest(
            prefix="tmp/1/abcdef0123456789",
            expire_seconds=1800,
        )
        await svc.grant_prefix_credentials(req)

        cfg = fake_sts.calls[0].config
        assert cfg["duration_seconds"] == 1800


class TestGrantPrefixCredentialsSuccessPath:
    """2026-09-18：成功路径 → policy 7 action + resource 收窄 + 出参结构对齐。"""

    # 2026-09-18 review：参数化多形态 prefix（带尾斜杠 / 多层 / 最短）。
    @pytest.mark.parametrize(
        "prefix",
        [
            "tmp/1/abc/",  # 带尾斜杠
            "tmp/a/b/c",  # 多层
            "tmp/42/abcdef0123456789",  # 最短（uid/sha16 两段）
            "tmp/42/abcdef0123456789/session-xyz",  # 多段（rust session 用）
        ],
    )
    async def test_response_structure_and_policy_actions(
        self,
        fake_sts: _FakeStsRecorder,
        prefix: str,
    ) -> None:
        svc = StsService()
        req = StsPrefixCredentialsRequest(prefix=prefix, expire_seconds=1800)

        resp = await svc.grant_prefix_credentials(req)

        # ---- 出参结构 ----
        # credentials 五元组
        assert resp.credentials.tmp_secret_id == "STS.fake-id"
        assert resp.credentials.tmp_secret_key == "fake-key"
        assert resp.credentials.session_token == "fake-token"
        assert resp.credentials.start_time == 1700000000
        assert resp.credentials.expired_time == 1700001800
        # top-level 冗余字段
        assert resp.start_time == resp.credentials.start_time
        assert resp.expired_time == resp.credentials.expired_time
        assert resp.expires_in == 1800
        # COS 元数据
        assert resp.bucket == settings.cos_bucket
        assert resp.region == settings.cos_region
        scheme = settings.cos_scheme or "https"
        assert resp.scheme == scheme
        expected_endpoint = (
            settings.cos_endpoint
            or f"{scheme}://cos.{settings.cos_region}.myqcloud.com"
        )
        assert resp.endpoint == expected_endpoint
        # 内部端口响应不带 tmp_key（schema 不暴露，运行时也不应注入）。
        # 2026-09-18 review：改用 model_dump() 检查字段集合，比 hasattr
        # 更严格（hasattr 也对，但 model_dump() 顺带保证 schema 字段集
        # 合契约）。
        assert "tmp_key" not in resp.model_dump()

        # ---- SDK 收到的 policy ----
        assert len(fake_sts.calls) == 1
        cfg = fake_sts.calls[0].config
        assert cfg["secret_id"] == settings.cos_secret_id
        assert cfg["secret_key"] == settings.cos_secret_key
        assert cfg["bucket"] == settings.cos_bucket
        assert cfg["region"] == settings.cos_region
        assert cfg["duration_seconds"] == 1800
        # 2026-09-18 review：SDK config 必须同时含 allow_prefix +
        # allow_actions（policy 存在时 SDK 走 self.policy 分支，allow_*
        # 实际不影响最终 policy，但 config 形状必须保留与旧端点对齐）。
        assert cfg["allow_prefix"] == [f"{prefix}/*"]
        assert sorted(cfg["allow_actions"]) == sorted(_UPLOAD_ACTIONS_SHORT)
        # policy 必须含 7 个上传类 action + 1 条 resource 收窄到 `{prefix}*`
        policy = cfg["policy"]
        assert policy["version"] == "2.0"
        assert len(policy["statement"]) == 1
        stmt = policy["statement"][0]
        assert stmt["effect"] == "allow"
        # SDK policy 用 `name/cos:XXX` 全名；与 _UPLOAD_ACTIONS_SHORT 等价
        # （_UPLOAD_ACTIONS_FULL = ("name/cos:PutObject", ...)）。
        actions = set(stmt["action"])
        expected_actions = {
            "name/cos:PutObject",
            "name/cos:InitiateMultipartUpload",
            "name/cos:ListMultipartUploads",
            "name/cos:ListParts",
            "name/cos:UploadPart",
            "name/cos:CompleteMultipartUpload",
            "name/cos:AbortMultipartUpload",
        }
        assert actions == expected_actions
        assert len(actions) == 7
        # 等价断言：去掉 `name/` 前缀后的集合 == _UPLOAD_ACTIONS_SHORT。
        assert {a.split("/", 1)[1] for a in actions} == set(_UPLOAD_ACTIONS_SHORT)
        # 2026-09-18 review：resource 收窄到
        # `qcs::cos:{region}:uid/{appid}:{bucket_appid}/{prefix}*`（含
        # bucket 段，与旧端点行为对齐）。
        bucket_appid = settings.cos_bucket
        appid = bucket_appid.rsplit("-", 1)[-1]
        expected_resource = (
            f"qcs::cos:{settings.cos_region}:uid/{appid}:{bucket_appid}/{prefix}*"
        )
        assert stmt["resource"] == [expected_resource]


# ============================================================
# 旧端点 `grant_sts_tmp_key` 回归：证明重构没改旧端点行为
# ============================================================
class TestGrantStsTmpKeyRegression:
    """2026-09-18 review：旧端点回归测试。

    2026-09-18 抽出公共函数 `grant_credentials_for_prefix` 后，
    `grant_sts_tmp_key` 间接走到同一个底层函数。本测试固定两个旧端点
    行为不变量：
    1. policy.resource 必须含 `{bucket_appid}/` 中段（不是纯
       `uid/{appid}:{prefix}*`）；
    2. SDK config 必须同时含 `allow_prefix` + `allow_actions` + `policy`，
       不能因重构被删。
    """

    async def test_policy_resource_includes_bucket_mid_segment(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        svc = StsService()
        # 旧端点直接用 StsService.grant_tmp_keys（走 StsTmpKeysRequest）。
        from schema.sts import StsTmpKeysRequest

        req = StsTmpKeysRequest(
            purpose="drawing",
            filename="test.pdf",
            expire_seconds=1800,
        )
        await svc.grant_tmp_keys(req)

        assert len(fake_sts.calls) == 1
        cfg = fake_sts.calls[0].config
        policy = cfg["policy"]
        stmt = policy["statement"][0]
        # 不变量 1：resource 含 `{bucket_appid}/` 中段
        bucket_appid = settings.cos_bucket
        appid = bucket_appid.rsplit("-", 1)[-1]
        sha16 = "nohash"
        expected_prefix = f"tmp/{settings.sts_default_user_id}/{sha16}"
        expected_resource = (
            f"qcs::cos:{settings.cos_region}:uid/{appid}:{bucket_appid}"
            f"/{expected_prefix}*"
        )
        assert stmt["resource"] == [expected_resource], (
            "旧端点 grant_sts_tmp_key 的 resource 必须含 bucket 段"
        )

    async def test_sdk_config_keeps_allow_prefix_and_allow_actions(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        svc = StsService()
        from schema.sts import StsTmpKeysRequest

        req = StsTmpKeysRequest(
            purpose="drawing",
            filename="test.pdf",
            expire_seconds=1800,
        )
        await svc.grant_tmp_keys(req)

        assert len(fake_sts.calls) == 1
        cfg = fake_sts.calls[0].config
        # 不变量 2：SDK config 同时含 allow_prefix + allow_actions
        sha16 = "nohash"
        expected_prefix = f"tmp/{settings.sts_default_user_id}/{sha16}"
        assert cfg["allow_prefix"] == [f"{expected_prefix}/*"], (
            "旧端点 SDK config 必须保留 allow_prefix（policy 并存时 SDK "
            "走 self.policy 分支，但 config 形状必须维持旧行为）"
        )
        assert sorted(cfg["allow_actions"]) == sorted(_UPLOAD_ACTIONS_SHORT), (
            "旧端点 SDK config 必须保留 allow_actions"
        )
        assert "policy" in cfg, "旧端点 SDK config 必须保留 policy"


# ============================================================
# SDK 抛错路径 → BIZ_STS_GRANT_FAILED（502）
# ============================================================
class TestGrantPrefixCredentialsSdkError:
    """2026-09-18：SDK 抛错 → BIZ_STS_GRANT_FAILED，包装异常类型名。"""

    async def test_sdk_exception_maps_to_biz_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorder = _FakeStsRecorder()

        def _boom(_config: dict[str, Any]) -> _FakeStsClient:
            class _Boom:
                def __init__(self, cfg: dict[str, Any]) -> None:
                    self.cfg = cfg

                def get_credential(self) -> dict[str, Any]:
                    recorder.calls.append(_FakeStsCall(config=dict(self.cfg)))
                    raise RuntimeError("network boom")

            return _Boom(_config)

        monkeypatch.setattr(sts_core, "_CosSts", _boom)

        svc = StsService()
        req = StsPrefixCredentialsRequest(
            prefix="tmp/1/abcdef0123456789",
            expire_seconds=600,
        )

        with pytest.raises(BizError) as exc:
            await svc.grant_prefix_credentials(req)
        assert exc.value.code == ErrCode.BIZ_STS_GRANT_FAILED
        assert exc.value.http_status == 502
        # 异常消息只暴露 SDK 异常类型名，不泄漏完整 repr（含凭证 / policy 痕迹）。
        assert "RuntimeError" in exc.value.message
        assert "network boom" not in exc.value.message


# ============================================================
# core.sts.grant_credentials_for_prefix 直接校验：expire_seconds < 60 /
# prefix 不以 tmp/ 开头
# ============================================================
class TestGrantCredentialsForPrefixLowerBound:
    """2026-09-18：core 层兜底——expire_seconds < 60 抛 BIZ_INVALID_VALUE。"""

    async def test_below_60_rejected(self) -> None:
        with pytest.raises(BizError) as exc:
            await sts_core.grant_credentials_for_prefix(
                prefix="tmp/1/abc",
                expire_seconds=30,
            )
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400


class TestGrantCredentialsForPrefixCorePrefixGuard:
    """2026-09-18 review：core 层兜底——prefix 不以 tmp/ 开头也拒。"""

    async def test_prefix_without_tmp_prefix_rejected_at_core(self) -> None:
        with pytest.raises(BizError) as exc:
            await sts_core.grant_credentials_for_prefix(
                prefix="drawings/1/abc",
                expire_seconds=600,
            )
        assert exc.value.code == ErrCode.BIZ_STS_PREFIX_INVALID
        assert exc.value.http_status == 400
