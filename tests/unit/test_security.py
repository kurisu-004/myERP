"""Tests for core/security.py — JWT token factory + decode.

覆盖：
- create_access_token 带 type="access"
- create_refresh_token 带 type="refresh" + ver=N
- _decode_token 对 access 老 token（无 type 字段）的兼容
- decode_refresh_token 拒绝 access token（type 不匹配）
- decode_access_token 拒绝 refresh token（type 不匹配）
- 过期 / 篡改签名 / 错误 issuer 的失败路径
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import status as http_status

from core.config import settings
from core.error_code import ErrCode
from core.exception import BizError
from core.security import (
    _build_token_claims,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)


# ======================================================================
# _build_token_claims
# ======================================================================


class TestBuildTokenClaims:
    """`_build_token_claims` 是 access / refresh 共用工厂，验证：基础字段 + type。"""

    def test_access_type_in_claims(self) -> None:
        claims = _build_token_claims(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[100],
            extra={"shelf_wildcard": True},
            expires_minutes=60,
            token_type="access",
        )
        assert claims["sub"] == "42"
        assert claims["username"] == "admin"
        assert claims["roles"] == ["MANAGER"]
        assert claims["shelf_ids"] == [100]
        assert claims["type"] == "access"
        assert claims["iss"] == settings.jwt_issuer
        assert claims["shelf_wildcard"] is True
        assert isinstance(claims["iat"], int)
        assert isinstance(claims["exp"], int)
        assert claims["exp"] > claims["iat"]

    def test_refresh_type_in_claims(self) -> None:
        claims = _build_token_claims(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[],
            extra=None,
            expires_minutes=10080,
            token_type="refresh",
            extra_claims={"ver": 7},
        )
        assert claims["type"] == "refresh"
        assert claims["ver"] == 7
        # refresh 不需要 shelf_wildcard
        assert "shelf_wildcard" not in claims


# ======================================================================
# create_access_token
# ======================================================================


class TestCreateAccessToken:
    """access token 签发 + 端到端解码。"""

    def test_decode_round_trip(self) -> None:
        token = create_access_token(
            user_id=42,
            username="admin",
            roles=["MANAGER", "CLERK"],
            shelf_ids=[100, 200],
            extra={"shelf_wildcard": False},
        )
        payload = decode_access_token(token)
        assert payload["sub"] == "42"
        assert payload["username"] == "admin"
        assert payload["roles"] == ["MANAGER", "CLERK"]
        assert payload["shelf_ids"] == [100, 200]
        assert payload["type"] == "access"
        assert payload["shelf_wildcard"] is False

    def test_legacy_token_without_type_still_decodes(self) -> None:
        """2026-07-10 之前签发的 access token 没 type 字段 → 兼容通过。

        构造一个无 type 字段的 token（手动 jwt.encode），确认
        decode_access_token 不报错。
        """
        now = datetime.now(tz=timezone.utc)
        legacy_payload = {
            "sub": "42",
            "username": "admin",
            "roles": ["MANAGER"],
            "shelf_ids": [],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=60)).timestamp()),
            "iss": settings.jwt_issuer,
        }
        # 不含 type 字段
        legacy_token = jwt.encode(
            legacy_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
        payload = decode_access_token(legacy_token)
        assert payload["sub"] == "42"
        assert "type" not in payload  # 老 token 没有这个字段


# ======================================================================
# create_refresh_token
# ======================================================================


class TestCreateRefreshToken:
    """refresh token 签发 + 端到端解码。"""

    def test_decode_round_trip(self) -> None:
        token = create_refresh_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[100],
            refresh_token_version=7,
        )
        payload = decode_refresh_token(token)
        assert payload["sub"] == "42"
        assert payload["type"] == "refresh"
        assert payload["ver"] == 7
        # refresh token 用 settings.jwt_refresh_token_expire_minutes
        expected_exp_seconds = settings.jwt_refresh_token_expire_minutes * 60
        actual_exp_delta = payload["exp"] - payload["iat"]
        # ±5s 容差（时钟漂移）
        assert abs(actual_exp_delta - expected_exp_seconds) <= 5

    def test_refresh_rejects_access_token(self) -> None:
        """access token 喂给 decode_refresh_token → 401 BIZ_AUTH_REFRESH_INVALID。"""
        access_token = create_access_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[],
        )
        with pytest.raises(BizError) as exc_info:
            decode_refresh_token(access_token)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID
        assert exc_info.value.http_status == 401

    def test_refresh_rejects_legacy_token_without_type(self) -> None:
        """无 type 字段的老 access token 也不能蒙混进 decode_refresh_token。"""
        now = datetime.now(tz=timezone.utc)
        legacy_payload = {
            "sub": "42",
            "username": "admin",
            "roles": ["MANAGER"],
            "shelf_ids": [],
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=60)).timestamp()),
            "iss": settings.jwt_issuer,
        }
        legacy_token = jwt.encode(
            legacy_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
        with pytest.raises(BizError) as exc_info:
            decode_refresh_token(legacy_token)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID


# ======================================================================
# decode_access_token 拒绝 refresh token
# ======================================================================


class TestDecodeAccessTokenRejectsRefresh:
    """refresh token 喂给 decode_access_token → 401 BIZ_AUTH_INVALID。"""

    def test_refresh_token_rejected(self) -> None:
        refresh_token = create_refresh_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[],
            refresh_token_version=0,
        )
        with pytest.raises(BizError) as exc_info:
            decode_access_token(refresh_token)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
        assert exc_info.value.http_status == 401


# ======================================================================
# 错误路径：过期 / 篡改 / 错误 issuer
# ======================================================================


class TestDecodeErrorPaths:
    """过期 / 篡改签名 / 错误 issuer 各自对应错误码。"""

    def test_expired_access_token(self) -> None:
        """access token 过期 → BIZ_AUTH_TOKEN_EXPIRED (40102)。"""
        now = datetime.now(tz=timezone.utc)
        expired_payload = {
            "sub": "42",
            "username": "admin",
            "roles": ["MANAGER"],
            "shelf_ids": [],
            "type": "access",
            "iat": int((now - timedelta(hours=2)).timestamp()),
            "exp": int((now - timedelta(hours=1)).timestamp()),
            "iss": settings.jwt_issuer,
        }
        expired_token = jwt.encode(
            expired_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
        with pytest.raises(BizError) as exc_info:
            decode_access_token(expired_token)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_TOKEN_EXPIRED

    def test_expired_refresh_token(self) -> None:
        """refresh token 过期 → BIZ_AUTH_REFRESH_INVALID (40103)。"""
        now = datetime.now(tz=timezone.utc)
        expired_payload = {
            "sub": "42",
            "type": "refresh",
            "ver": 0,
            "iat": int((now - timedelta(days=8)).timestamp()),
            "exp": int((now - timedelta(days=7)).timestamp()),
            "iss": settings.jwt_issuer,
        }
        expired_token = jwt.encode(
            expired_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
        with pytest.raises(BizError) as exc_info:
            decode_refresh_token(expired_token)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_REFRESH_INVALID

    def test_tampered_signature(self) -> None:
        """签名被改 → 401 BIZ_AUTH_INVALID / BIZ_AUTH_REFRESH_INVALID。"""
        token = create_access_token(
            user_id=42,
            username="admin",
            roles=["MANAGER"],
            shelf_ids=[],
        )
        # 在 token 末尾追加几个字符 → 签名失效
        tampered = token[:-3] + "AAA"
        with pytest.raises(BizError) as exc_info:
            decode_access_token(tampered)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID

    def test_wrong_issuer(self) -> None:
        """错误 issuer → 401 BIZ_AUTH_INVALID。"""
        now = datetime.now(tz=timezone.utc)
        bad_iss_payload = {
            "sub": "42",
            "username": "admin",
            "roles": ["MANAGER"],
            "shelf_ids": [],
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=60)).timestamp()),
            "iss": "evil-issuer",
        }
        bad_iss_token = jwt.encode(
            bad_iss_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
        with pytest.raises(BizError) as exc_info:
            decode_access_token(bad_iss_token)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID

    def test_missing_required_claims(self) -> None:
        """缺 iat → jwt 直接抛 InvalidTokenError，被 catch 后报 BIZ_AUTH_INVALID。"""
        now = datetime.now(tz=timezone.utc)
        bad_payload = {
            "sub": "42",
            "username": "admin",
            "roles": ["MANAGER"],
            "shelf_ids": [],
            "type": "access",
            "exp": int((now + timedelta(minutes=60)).timestamp()),
            "iss": settings.jwt_issuer,
            # 没有 iat
        }
        bad_token = jwt.encode(
            bad_payload, settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
        with pytest.raises(BizError) as exc_info:
            decode_access_token(bad_token)
        assert exc_info.value.code == ErrCode.BIZ_AUTH_INVALID
