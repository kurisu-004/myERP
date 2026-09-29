"""2026-09-17 新增 / 2026-09-28 扩展 / 2026-09-29 重构：
`POST /api/v1/files/sts-tmp-keys` 单测。

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

2026-09-29 重构（tmp_key 模板简化 `tmp/{uid}/{sha256}.{ext}`）：
- 所有 tmp_key 断言改为新模板（无 sha16 截断 / 无 safe_filename 段）；
- `StsTmpKeysRequest.content_sha256` 改为必填 + 64 hex；
- 新增 `ext: str` 必填字段（1..7 字符小写字母数字）；
- `core.sts.grant_sts_tmp_key` 已删除——本测试不再 import；service 层
  现直接走 `grant_credentials_for_prefix(prefix=tmp_key)`；
- policy.resource 同步改为以完整 tmp_key（=`{sha256}.{ext}`）为前缀
  补 `*` 的通配。

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


# 2026-09-29 重构：统一 64-char sha fixture，方便跨测试复用。
SHA_64_A = "a" * 64
SHA_64_B = "b" * 64
SHA_64_C = "c" * 64
# 与 sha16 等价的「前 16 hex」断言用 helper。
SHA_16_A = SHA_64_A[:16]
SHA_16_B = SHA_64_B[:16]
SHA_16_C = SHA_64_C[:16]


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


def _build_single_file_req(
    *,
    filename: str = "test.pdf",
    content_sha256: str = SHA_64_A,
    ext: str = "pdf",
    purpose: str = "drawing",
    expire_seconds: int = 1800,
) -> StsTmpKeysRequest:
    """2026-09-29 重构：构造单文件 `StsTmpKeysRequest`（含必填
    content_sha256 + ext）。"""
    return StsTmpKeysRequest(
        purpose=purpose,  # type: ignore[arg-type]
        filename=filename,
        content_sha256=content_sha256,
        ext=ext,
        expire_seconds=expire_seconds,
    )


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

    2026-09-29 重构：tmp_key 模板改为 `tmp/{uid}/{sha256}.{ext}`，
    prefix 入参语义对齐为「完整 key 前缀」（详见
    `core.sts.grant_credentials_for_prefix` docstring）。本测试
    不变量保持：resource 与 allow_prefix 形态合规（仍是 `*` 通配）。
    """

    async def test_policy_resource_uses_full_tmp_key_with_wildcard(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        svc = StsService()
        req = _build_single_file_req(
            filename="test.pdf",
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        await svc.grant_tmp_keys(req)

        assert len(fake_sts.calls) == 1
        cfg = fake_sts.calls[0].config
        policy = cfg["policy"]
        stmt = policy["statement"][0]
        # 不变量 1：resource 含 `{bucket_appid}/` 中段 + 完整 tmp_key
        bucket_appid = settings.cos_bucket
        appid = bucket_appid.rsplit("-", 1)[-1]
        # 2026-09-29 重构：prefix 现为完整 tmp_key（`tmp/{uid}/{sha256}.{ext}`）。
        expected_prefix = f"tmp/{settings.sts_default_user_id}/{SHA_64_A}.pdf"
        expected_resource = (
            f"qcs::cos:{settings.cos_region}:uid/{appid}:{bucket_appid}"
            f"/{expected_prefix}*"
        )
        assert stmt["resource"] == [expected_resource], (
            "policy.resource 必须以完整 tmp_key 为前缀补 * 通配"
        )

    async def test_sdk_config_keeps_allow_prefix_and_allow_actions(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        svc = StsService()
        req = _build_single_file_req(
            filename="test.pdf",
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        await svc.grant_tmp_keys(req)

        assert len(fake_sts.calls) == 1
        cfg = fake_sts.calls[0].config
        # 不变量 2：SDK config 同时含 allow_prefix + allow_actions
        # 2026-09-29 重构：allow_prefix 现为完整 tmp_key + 单 `*`（不再
        # 补 `/*`，因 prefix 是完整 key）。
        expected_prefix = f"tmp/{settings.sts_default_user_id}/{SHA_64_A}.pdf"
        assert cfg["allow_prefix"] == [f"{expected_prefix}*"], (
            "SDK config 必须保留 allow_prefix（policy 并存时 SDK "
            "走 self.policy 分支，但 config 形状必须维持旧行为）"
        )
        assert sorted(cfg["allow_actions"]) == sorted(_UPLOAD_ACTIONS_SHORT), (
            "SDK config 必须保留 allow_actions"
        )
        assert "policy" in cfg, "SDK config 必须保留 policy"

    async def test_single_file_tmp_key_format(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-29 重构：单文件响应 tmp_key 形如 `tmp/{uid}/{sha256}.{ext}`。"""
        svc = StsService()
        req = _build_single_file_req(
            filename="图纸.pdf",  # 故意带中文：验证后端不做 filename 推断
            content_sha256=SHA_64_A,
            ext="pdf",
        )
        resp = await svc.grant_tmp_keys(req)

        expected_key = f"tmp/{settings.sts_default_user_id}/{SHA_64_A}.pdf"
        assert resp.tmp_key == expected_key, (
            "tmp_key 必须严格匹配 `tmp/{uid}/{sha256}.{ext}` 模板"
        )
        # 关键不变量：不含中文 / 不含 safe_filename 段
        assert "图纸" not in resp.tmp_key
        assert "test" not in resp.tmp_key


# ============================================================
# 单文件 schema 校验（2026-09-29 重构：content_sha256 必填 + 64 hex，ext 必填）
# ============================================================
class TestStsTmpKeysRequestSchemaValidation:
    """2026-09-29 重构：schema 边界校验。

    - `content_sha256` 必填 64 hex（旧版 16..64 可选不再适用）；
    - `ext` 必填 1..7 字符小写字母数字（`^[a-z0-9]+$`）；
    - `filename` 仍必填 1..255，但**不**参与 tmp_key 命名。

    2026-09-29：本类测试在模块级 `pytestmark = pytest.mark.asyncio`
    下需要 `async def` 签名，否则会产生「sync 函数被 asyncio 标记」
    警告。Pydantic schema 校验本身不需要事件循环；声明为 `async def`
    仅是为兼容模块级 marker，无副作用。
    """

    async def test_content_sha256_required(self) -> None:
        """缺 `content_sha256` → Pydantic ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsTmpKeysRequest(
                purpose="drawing",
                filename="a.pdf",
                ext="pdf",
                expire_seconds=1800,
            )

    async def test_content_sha256_too_short_rejected(self) -> None:
        """`content_sha256` 长度 < 64 → Pydantic ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsTmpKeysRequest(
                purpose="drawing",
                filename="a.pdf",
                content_sha256="a" * 63,
                ext="pdf",
                expire_seconds=1800,
            )

    async def test_content_sha256_too_long_rejected(self) -> None:
        """`content_sha256` 长度 > 64 → Pydantic ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsTmpKeysRequest(
                purpose="drawing",
                filename="a.pdf",
                content_sha256="a" * 65,
                ext="pdf",
                expire_seconds=1800,
            )

    async def test_ext_required(self) -> None:
        """缺 `ext` → Pydantic ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsTmpKeysRequest(
                purpose="drawing",
                filename="a.pdf",
                content_sha256=SHA_64_A,
                expire_seconds=1800,
            )

    async def test_ext_uppercase_rejected(self) -> None:
        """`ext` 含大写字母 → Pydantic ValidationError（pattern 强制小写）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsTmpKeysRequest(
                purpose="drawing",
                filename="a.pdf",
                content_sha256=SHA_64_A,
                ext="PDF",
                expire_seconds=1800,
            )

    async def test_ext_too_long_rejected(self) -> None:
        """`ext` 长度 > 7 → Pydantic ValidationError。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsTmpKeysRequest(
                purpose="drawing",
                filename="a.pdf",
                content_sha256=SHA_64_A,
                ext="tooolong",
                expire_seconds=1800,
            )

    async def test_ext_with_special_char_rejected(self) -> None:
        """`ext` 含 `.` / `_` 等 → Pydantic ValidationError（只允许 a-z0-9）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsTmpKeysRequest(
                purpose="drawing",
                filename="a.pdf",
                content_sha256=SHA_64_A,
                ext="p.df",
                expire_seconds=1800,
            )

    async def test_ext_with_digits_accepted(self) -> None:
        """`ext` 纯数字 → 接受（pattern 允许 a-z0-9）。"""
        req = StsTmpKeysRequest(
            purpose="drawing",
            filename="a.123",
            content_sha256=SHA_64_A,
            ext="123",
            expire_seconds=1800,
        )
        assert req.ext == "123"


# ============================================================
# 批量端点：成功路径
# ============================================================
class TestGrantTmpKeysBatchSuccess:
    """2026-09-28：N 个文件 → N 个 tmp_key + 共享 bucket/region/scheme。

    2026-09-29 重构：tmp_key 改为 `tmp/{uid}/{sha256}.{ext}`，断言相应
    更新（无 safe_filename 段、各文件 tmp_key 形态由 content_sha256 + ext
    决定）。
    """

    async def test_two_files_returns_two_items(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                _build_single_file_req(
                    filename="a.pdf",
                    content_sha256=SHA_64_A,
                    ext="pdf",
                ),
                _build_single_file_req(
                    filename="b.pdf",
                    content_sha256=SHA_64_B,
                    ext="pdf",
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
        scheme/upload_prefix 共享。

        2026-09-29 重构：tmp_key 不同由 content_sha256 + ext 决定；
        `filename` **不参与** key 命名（同一 sha + ext 即同 key，CAS 语义）。
        """
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                _build_single_file_req(
                    filename="a.pdf",
                    content_sha256=SHA_64_A,
                    ext="pdf",
                ),
                _build_single_file_req(
                    filename="b.pdf",
                    content_sha256=SHA_64_B,
                    ext="pdf",
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
        # tmp_key 不同（不同 content_sha256）
        assert item_a.tmp_key != item_b.tmp_key
        # 2026-09-29 重构：tmp_key 形如 `tmp/{uid}/{sha256}.{ext}`
        expected_a = f"tmp/{settings.sts_default_user_id}/{SHA_64_A}.pdf"
        expected_b = f"tmp/{settings.sts_default_user_id}/{SHA_64_B}.pdf"
        assert item_a.tmp_key == expected_a
        assert item_b.tmp_key == expected_b
        # 不含 filename 段（关键不变量）：tmp_key 是 `<sha64>.pdf`，filename
        # 段不再出现。注：SHA="a"*64 + ext="pdf" 会自然产生 "...aaa.pdf"
        # 子串；严格断言应比对「无 filename 段」，改为比对 SHA 后 48 hex
        # + ".pdf" 整段（filename 不会进 key）。
        assert item_a.tmp_key.endswith(f"{SHA_64_A[16:]}.pdf")
        assert item_b.tmp_key.endswith(f"{SHA_64_B[16:]}.pdf")

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
                _build_single_file_req(
                    filename="a.pdf",
                    content_sha256=SHA_64_A,
                    ext="pdf",
                ),
                _build_single_file_req(
                    filename="b.pdf",
                    content_sha256=SHA_64_B,
                    ext="pdf",
                ),
                _build_single_file_req(
                    filename="c.pdf",
                    content_sha256=SHA_64_C,
                    ext="pdf",
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

    async def test_per_file_policy_resource_uses_full_tmp_key(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-29 重构：批量路径下每文件 policy.resource 以各自完整
        tmp_key（`tmp/{uid}/{sha256}.{ext}`）为前缀补 `*` 通配。"""
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                _build_single_file_req(
                    filename="a.pdf",
                    content_sha256=SHA_64_A,
                    ext="pdf",
                ),
                _build_single_file_req(
                    filename="b.pdf",
                    content_sha256=SHA_64_B,
                    ext="pdf",
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

        # 2026-09-29 重构：prefix 现为完整 tmp_key；resource / allow_prefix
        # 都补单 `*`。
        expected_a = (
            f"qcs::cos:{settings.cos_region}:uid/{appid}:{bucket_appid}"
            f"/tmp/{settings.sts_default_user_id}/{SHA_64_A}.pdf*"
        )
        expected_b = (
            f"qcs::cos:{settings.cos_region}:uid/{appid}:{bucket_appid}"
            f"/tmp/{settings.sts_default_user_id}/{SHA_64_B}.pdf*"
        )
        assert cfg_a["policy"]["statement"][0]["resource"] == [expected_a]
        assert cfg_b["policy"]["statement"][0]["resource"] == [expected_b]

        # 响应中 tmp_key 也按各自 sha256 + ext 命名
        assert resp.items[0].tmp_key == (
            f"tmp/{settings.sts_default_user_id}/{SHA_64_A}.pdf"
        )
        assert resp.items[1].tmp_key == (
            f"tmp/{settings.sts_default_user_id}/{SHA_64_B}.pdf"
        )

    async def test_different_ext_yields_different_tmp_key(
        self,
        fake_sts: _FakeStsRecorder,
    ) -> None:
        """2026-09-29 重构：同 sha256 + 不同 ext → 不同 tmp_key
        （前端可借此让同一文件支持多 ext 上传，如 `xxx.pdf` vs `xxx.png`）。"""
        svc = StsService()
        req = StsBatchTmpKeysRequest(
            scope="parts_new",
            files=[
                _build_single_file_req(
                    filename="same",
                    content_sha256=SHA_64_A,
                    ext="pdf",
                ),
                _build_single_file_req(
                    filename="same",
                    content_sha256=SHA_64_A,
                    ext="png",
                ),
            ],
        )
        resp = await svc.grant_tmp_keys_batch(req)

        assert resp.items[0].tmp_key == (
            f"tmp/{settings.sts_default_user_id}/{SHA_64_A}.pdf"
        )
        assert resp.items[1].tmp_key == (
            f"tmp/{settings.sts_default_user_id}/{SHA_64_A}.png"
        )
        assert resp.items[0].tmp_key != resp.items[1].tmp_key

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
                _build_single_file_req(
                    filename="a.pdf",
                    content_sha256=SHA_64_A,
                    ext="pdf",
                ),
                _build_single_file_req(
                    filename="b.pdf",
                    content_sha256=SHA_64_B,
                    ext="pdf",
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
    """2026-09-28：files 长度边界（schema min_length=1 / max_length=200）。

    2026-09-29 重构：批量 schema 内每项文件复用 `StsTmpKeysRequest`，
    文件 schema 校验（content_sha256 64 hex + ext 必填）一并继承。
    """

    async def test_files_empty_rejected_by_schema(self) -> None:
        """files=[] → Pydantic ValidationError（min_length=1）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsBatchTmpKeysRequest(scope="parts_new", files=[])

    async def test_files_length_201_rejected_by_schema(self) -> None:
        """files 长度=201 → Pydantic ValidationError（max_length=200）。"""
        from pydantic import ValidationError

        files = [
            _build_single_file_req(
                filename=f"f{i}.pdf",
                content_sha256=("a" * 60 + f"{i:04d}"),
                ext="pdf",
            )
            for i in range(201)
        ]
        with pytest.raises(ValidationError):
            StsBatchTmpKeysRequest(scope="parts_new", files=files)

    async def test_files_length_200_accepted_by_schema(self) -> None:
        """files 长度=200 → 通过 schema 校验（max_length=200 上限）。"""
        files = [
            _build_single_file_req(
                filename=f"f{i}.pdf",
                content_sha256=("a" * 60 + f"{i:04d}"),
                ext="pdf",
            )
            for i in range(200)
        ]
        req = StsBatchTmpKeysRequest(scope="parts_new", files=files)
        assert len(req.files) == 200

    async def test_batch_item_missing_content_sha256_rejected(
        self,
    ) -> None:
        """2026-09-29 重构：批量 schema 任一文件缺 content_sha256 → 422。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsBatchTmpKeysRequest(
                scope="parts_new",
                files=[
                    StsTmpKeysRequest(
                        purpose="drawing",  # type: ignore[arg-type]
                        filename="a.pdf",
                        ext="pdf",
                        expire_seconds=1800,
                    ),
                ],
            )

    async def test_batch_item_missing_ext_rejected(
        self,
    ) -> None:
        """2026-09-29 重构：批量 schema 任一文件缺 ext → 422。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            StsBatchTmpKeysRequest(
                scope="parts_new",
                files=[
                    StsTmpKeysRequest(
                        purpose="drawing",  # type: ignore[arg-type]
                        filename="a.pdf",
                        content_sha256=SHA_64_A,
                        expire_seconds=1800,
                    ),
                ],
            )

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
                content_sha256=("a" * 60 + f"{i:04d}"),
                ext="pdf",
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
                _build_single_file_req(
                    filename="a.pdf",
                    content_sha256=SHA_64_A,
                    ext="pdf",
                ),
                _build_single_file_req(
                    filename="b.pdf",
                    content_sha256=SHA_64_B,
                    ext="pdf",
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
