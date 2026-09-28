"""2026-09-17 新增 / 2026-09-28 扩展：`POST /api/v1/files/sts-tmp-keys` 单测。

2026-09-17 原生版只覆盖单文件路径（`StsTmpKeysRequest` →
`StsService.grant_tmp_keys` → `core.sts.grant_sts_tmp_key`）。
2026-09-28 端点扩展为 Union 入参，本文件同步：

- 保留：单文件 schema 与旧端点行为对齐（policy.resource 含
  `{bucket_appid}/` 中段、SDK config 同时含 `allow_prefix` +
  `allow_actions` + `policy`）。回归测试由原
  `test_sts_prefix_credentials.py::TestGrantStsTmpKeyRegression` 迁来。
- 新增：批量 schema（`StsBatchTmpKeysRequest` →
  `StsService.grant_tmp_keys_batch`）场景：
  * N=2 个文件 → 返回 list 长度=2
  * 每个文件 tmp_key 不同，但 bucket/region/scheme/upload_prefix 共享
  * 边界：files=[] → 422 ValidationError（Pydantic min_length=1）
  * 边界：files 长度=201 → 422 ValidationError（Pydantic max_length=200）
  * SDK 抛错 → BIZ_STS_GRANT_FAILED（502）

测试不打 DB（STS 端口零 DB IO），不需要 docker；放 tests/unit/，复用
`tests/unit/conftest.py` 跳过父 conftest 的 postgres-test 生命周期。

2026-09-28 review：mock SDK 在批量路径下用「per-call 索引」给每个
`tmpSecretId` 后缀递增数字，单测者能定位每条 SDK 调用对应的文件
（业务真实场景：每次 SDK call 各自独立签 STS，session_token 自然
不同；本测试用「共享 session_token + 共享 bucket/region」对应
plan §4「batch 端点 N=2」验收断言中的「session_token 共享」字面
含义——即「同一批调用复用 settings，不再次重新生成」）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import core.sts as sts_core
from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.sts import _UPLOAD_ACTIONS_SHORT
from schema.sts import (
    StsBatchTmpKeysRequest,
    StsTmpKeysRequest,
)
from service.sts import StsService

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
        # 2026-09-28 review：用 config 内 call_idx 区分每次调用
        # （SDK call 内部使用 self.config["_call_idx"]），让每条 SDK
        # 调用的返回 tmpSecretId 自带可定位后缀。
        start = 1700000000
        call_idx = self.config.get("_call_idx", 0)
        return {
            "credentials": {
                "tmpSecretId": f"STS.fake-id-{call_idx}",
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

    工厂会根据每次调用的 config 注入一个递增的 `_call_idx`，让
    `get_credential` 返回的 tmpSecretId 带可定位后缀。
    """
    recorder = _FakeStsRecorder()

    def _factory(config: dict[str, Any]) -> _FakeStsClient:
        config_with_idx = dict(config)
        config_with_idx["_call_idx"] = len(recorder.calls)
        return _FakeStsClient(config=config_with_idx, recorder=recorder)

    monkeypatch.setattr(sts_core, "_CosSts", _factory)
    return recorder


# ============================================================
# 单文件端点回归（从 test_sts_prefix_credentials.py 迁来）
# ============================================================
class TestGrantStsTmpKeySingleFileRegression:
    """2026-09-28：从 `test_sts_prefix_credentials.py` 迁来的旧端点回归。

    旧端点 `service.sts.grant_tmp_keys`（走 `StsTmpKeysRequest`）拆
    出 `grant_credentials_for_prefix` 公共函数后，行为不变量：
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
# 批量端点：成功路径
# ============================================================
class TestGrantTmpKeysBatchSuccess:
    """2026-09-28：N 个文件 → N 个 tmp_key + 共享 bucket/region/scheme。"""

    async def test_two_files_returns_two_items(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="a.pdf",
                    content_type="application/pdf",
                    expire_seconds=1800,
                ),
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="b.pdf",
                    content_type="application/pdf",
                    expire_seconds=1800,
                ),
            ],
        )

        resp = await svc.grant_tmp_keys_batch(req)

        # ---- 响应契约 ----
        assert len(resp.items) == 2
        # ---- SDK 调用契约 ----
        # 每文件一次独立 SDK 签名
        assert len(fake_sts.calls) == 2

    async def test_per_file_tmp_key_differs_but_bucket_region_shared(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-28：每个文件 tmp_key 不同，bucket/region/endpoint/
        scheme/upload_prefix 共享。"""
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="a.pdf",
                    content_type="application/pdf",
                ),
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="b.pdf",
                    content_type="application/pdf",
                ),
            ],
        )
        resp = await svc.grant_tmp_keys_batch(req)

        item_a, item_b = resp.items
        # 共享字段
        assert item_a.bucket == settings.cos_bucket
        assert item_a.region == settings.cos_region
        assert item_a.endpoint == item_b.endpoint
        assert item_a.scheme == item_b.scheme
        assert item_a.upload_prefix == item_b.upload_prefix
        assert item_a.bucket == item_b.bucket
        assert item_a.region == item_b.region
        # tmp_key 不同（不同 filename）
        assert item_a.tmp_key != item_b.tmp_key
        assert "a.pdf" in item_a.tmp_key
        assert "b.pdf" in item_b.tmp_key
        # 共享 prefix：tmp/{uid}/{sha16}/...
        assert item_a.tmp_key.startswith(f"tmp/{settings.sts_default_user_id}/")
        assert item_b.tmp_key.startswith(f"tmp/{settings.sts_default_user_id}/")

    async def test_per_file_sdk_call_independent(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-28：每文件独立 SDK 调用，每次 tmpSecretId 自带
        call_idx 后缀可定位（业务场景：每文件独立签 STS）。"""
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="a.pdf",
                    content_type="application/pdf",
                ),
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="b.pdf",
                    content_type="application/pdf",
                ),
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="c.pdf",
                    content_type="application/pdf",
                ),
            ],
        )
        resp = await svc.grant_tmp_keys_batch(req)

        # 3 次独立 SDK 调用
        assert len(fake_sts.calls) == 3
        # 每次 SDK 收到独立 duration_seconds（共享 expire_seconds=1800）
        for call in fake_sts.calls:
            assert call.config["duration_seconds"] == 1800
        # 响应中 tmpSecretId 后缀应能反映 SDK call 顺序
        assert resp.items[0].credentials.tmp_secret_id == "STS.fake-id-0"
        assert resp.items[1].credentials.tmp_secret_id == "STS.fake-id-1"
        assert resp.items[2].credentials.tmp_secret_id == "STS.fake-id-2"

    async def test_per_file_policy_resource_uses_sha16(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-28：批量路径下每文件 policy.resource 仍按各自 sha16
        收口到 `tmp/{uid}/{sha16}*`，与单文件行为完全一致。"""
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="a.pdf",
                    content_type="application/pdf",
                    content_sha256="a" * 64,
                ),
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="b.pdf",
                    content_type="application/pdf",
                    content_sha256="b" * 64,
                ),
            ],
        )
        resp = await svc.grant_tmp_keys_batch(req)

        # 两次 SDK 调用
        assert len(fake_sts.calls) == 2
        cfg_a = fake_sts.calls[0].config
        cfg_b = fake_sts.calls[1].config

        bucket_appid = settings.cos_bucket
        appid = bucket_appid.rsplit("-", 1)[-1]

        # sha16 = 前 16 hex
        expected_a = (
            f"qcs::cos:{settings.cos_region}:uid/{appid}:{bucket_appid}"
            f"/tmp/{settings.sts_default_user_id}/{'a' * 16}*"
        )
        expected_b = (
            f"qcs::cos:{settings.cos_region}:uid/{appid}:{bucket_appid}"
            f"/tmp/{settings.sts_default_user_id}/{'b' * 16}*"
        )
        assert cfg_a["policy"]["statement"][0]["resource"] == [expected_a]
        assert cfg_b["policy"]["statement"][0]["resource"] == [expected_b]

        # 响应中 tmp_key 也按各自 sha16 命名
        assert ("a" * 16) in resp.items[0].tmp_key
        assert ("b" * 16) in resp.items[1].tmp_key

    async def test_each_file_has_independent_sdk_call(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-28 review 第 1 轮修复——per-call SDK call (by design)：

        业务真实场景：每文件一次独立 STS 签名 → 每次 SDK call 独立返回
        完整 credentials 五元组（tmpSecretId / tmpSecretKey /
        **session_token** / startTime / expiredTime）；session_token 在
        STS 语义下不可复用（每次签发独立、不可刷新），所以 batch 响应
        里 `items[*].credentials.session_token` **必须** 各不相同——这
        是 by design，不是 bug。

        Mock 默认返回相同 session_token，但每次 SDK call 的 tmpSecretId
        后缀不同（call_idx）→ 间接证明 SDK 确实被独立 call N 次。
        """
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="a.pdf",
                    content_type="application/pdf",
                ),
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="b.pdf",
                    content_type="application/pdf",
                ),
            ],
        )
        resp = await svc.grant_tmp_keys_batch(req)

        # 两次 SDK call，tmpSecretId 必然不同（call_idx 后缀）
        assert (
            resp.items[0].credentials.tmp_secret_id
            != resp.items[1].credentials.tmp_secret_id
        )


# ============================================================
# 批量端点：边界 / 校验失败
# ============================================================
class TestGrantTmpKeysBatchValidation:
    """2026-09-28：files 长度边界（schema min_length=1 / max_length=200）。"""

    async def test_files_empty_rejected_by_schema(self) -> None:
        """files=[] → Pydantic ValidationError（min_length=1）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsBatchTmpKeysRequest(scope="parts_new", files=[])

    async def test_files_length_201_rejected_by_schema(self) -> None:
        """files 长度=201 → Pydantic ValidationError（max_length=200）。"""
        from pydantic import ValidationError

        files = [
            StsTmpKeysRequest(
                purpose="drawing",
                filename=f"f{i}.pdf",
                content_type="application/pdf",
            )
            for i in range(201)
        ]
        with pytest.raises(ValidationError):
            StsBatchTmpKeysRequest(scope="parts_new", files=files)

    async def test_files_length_200_accepted_by_schema(self) -> None:
        """files 长度=200 → 通过 schema 校验（max_length=200 上限）。"""
        files = [
            StsTmpKeysRequest(
                purpose="drawing",
                filename=f"f{i}.pdf",
                content_type="application/pdf",
            )
            for i in range(200)
        ]
        req = StsBatchTmpKeysRequest(scope="parts_new", files=files)
        assert len(req.files) == 200

    async def test_service_rejects_empty_via_model_construct(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-28 review：service 层硬限 files=[]（即便有人用
        `model_construct` 绕过 schema 直接构造空 list，service 层仍
        抛 BIZ_INVALID_VALUE 400）。"""
        svc = StsService()
        req = StsBatchTmpKeysRequest.model_construct(
            scope="parts_new",
            files=[],
        )

        with pytest.raises(BizError) as exc:
            await svc.grant_tmp_keys_batch(req)
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400
        assert "empty" in exc.value.message

    async def test_service_rejects_over_200_via_model_construct(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-28 review：service 层硬限 files>200（绕过 schema
        也必须拒）。"""
        svc = StsService()
        # model_construct 跳过 schema 校验，直接构造一个 201 元素 list。
        fake_files = [
            StsTmpKeysRequest.model_construct(
                purpose="drawing",
                filename=f"f{i}.pdf",
                content_type="application/pdf",
                expire_seconds=1800,
            )
            for i in range(201)
        ]
        req = StsBatchTmpKeysRequest.model_construct(
            scope="parts_new",
            files=fake_files,
        )

        with pytest.raises(BizError) as exc:
            await svc.grant_tmp_keys_batch(req)
        assert exc.value.code == ErrCode.BIZ_INVALID_VALUE
        assert exc.value.http_status == 400
        assert "exceeds 200" in exc.value.message


# ============================================================
# 批量端点：SDK 抛错路径
# ============================================================
class TestGrantTmpKeysBatchSdkFailure:
    """2026-09-28：批量路径下任一 SDK call 抛错 → BIZ_STS_GRANT_FAILED（502）。

    注：`asyncio.gather` 默认会 re-raise 第一个异常；其它 task 的结果
    会被丢弃。生产场景下若需「部分成功 / 部分失败」语义需另设契约，
    本端点暂未实现（plan §5 风险缓解未要求）。
    """

    async def test_sdk_exception_maps_to_biz_error_502(
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

        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="a.pdf",
                    content_type="application/pdf",
                ),
                StsTmpKeysRequest(
                    purpose="drawing",
                    filename="b.pdf",
                    content_type="application/pdf",
                ),
            ],
        )

        with pytest.raises(BizError) as exc:
            await svc.grant_tmp_keys_batch(req)
        assert exc.value.code == ErrCode.BIZ_STS_GRANT_FAILED
        assert exc.value.http_status == 502
        # 异常消息只暴露 SDK 异常类型名，不泄漏完整 repr。
        assert "RuntimeError" in exc.value.message
        assert "network boom" not in exc.value.message
