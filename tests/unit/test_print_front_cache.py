"""Unit tests for service/_print_front_cache.py L1 disk-cache lifecycle.

2026-07-31 引入：覆盖
- 可写目录 → init_l1_cache() 返回 True；_l1_put + _l1_get 往返一致；
- 不可写目录 → init_l1_cache() 返回 False；后续 _l1_get 返 None、_l1_put 静默
  no-op，且 warning 只打一次（连续 3 次写不重复刷屏）。
- _reset_l1_for_testing() 让每个用例拿到干净的探测状态。

容器路径（/app/.cache/print）只用作模块顶层默认值；这些测试用 monkeypatch
把 _CACHE_DIR 临时改到 tmp_path 或只读路径，不碰真实根文件系统。
"""
from __future__ import annotations

import logging
import os

import pytest

from service import _print_front_cache as fc


@pytest.fixture(autouse=True)
def _reset_l1_state():
    """每个用例前后清掉 L1 探测状态 + 还原 _CACHE_DIR。"""
    original_dir = fc._CACHE_DIR
    yield
    fc._reset_l1_for_testing()
    # 还原 _CACHE_DIR（monkeypatch 在 teardown 会自动还原，但显式兜底）
    fc._CACHE_DIR = original_dir


def test_init_l1_cache_returns_true_when_writable(tmp_path, monkeypatch, caplog):
    """可写目录：init_l1_cache() 探测成功，返回 True。"""
    monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
    fc._reset_l1_for_testing()

    with caplog.at_level(logging.INFO, logger=fc.__name__):
        ok = fc.init_l1_cache()

    assert ok is True
    assert fc._l1_enabled is True
    assert any("L1 print cache enabled" in r.message for r in caplog.records)


def test_l1_put_and_get_roundtrip(tmp_path, monkeypatch):
    """可写目录：put 后 get 能拿回原 bytes。"""
    monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
    fc._reset_l1_for_testing()
    fc.init_l1_cache()

    sha = "a" * 64
    payload = b"%PDF-1.4 fake front page bytes"
    fc._l1_put(sha, payload)

    assert fc._l1_get(sha) == payload


def test_init_l1_cache_returns_false_when_unwritable(tmp_path, monkeypatch, caplog):
    """不可写目录：init_l1_cache() 返回 False；warning 只打一次。

    在 tmp_path 下建一个只读父目录（chmod 0o555），让 mkdir(parents=True) 在
    它底下创建子路径时必然 PermissionError。
    """
    ro_parent = tmp_path / "ro_parent"
    ro_parent.mkdir()
    os.chmod(ro_parent, 0o555)
    monkeypatch.setattr(fc, "_CACHE_DIR", ro_parent / "child")
    fc._reset_l1_for_testing()

    with caplog.at_level(logging.WARNING, logger=fc.__name__):
        ok = fc.init_l1_cache()

    # 还原权限，避免 cleanup 时 tmp_path 不能删
    os.chmod(ro_parent, 0o755)

    assert ok is False
    assert fc._l1_enabled is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "L1 print cache disabled" in warnings[0].message


def test_l1_put_silently_noops_after_disabled(tmp_path, monkeypatch, caplog):
    """init_l1_cache() 失败后：连续 _l1_put 三次都不再 warning，且不抛异常。"""
    ro_parent = tmp_path / "ro_parent"
    ro_parent.mkdir()
    os.chmod(ro_parent, 0o555)
    monkeypatch.setattr(fc, "_CACHE_DIR", ro_parent / "child")
    fc._reset_l1_for_testing()

    try:
        assert fc.init_l1_cache() is False

        with caplog.at_level(logging.WARNING, logger=fc.__name__):
            for _ in range(3):
                fc._l1_put("a" * 64, b"x")
    finally:
        os.chmod(ro_parent, 0o755)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # 关键断言：warning 只出现 1 次（init_l1_cache 那条），后续三次 put 全静默
    assert len(warnings) == 1
    assert fc._l1_enabled is False


def test_l1_get_returns_none_after_disabled(tmp_path, monkeypatch):
    """init_l1_cache() 失败后：_l1_get 静默返回 None，不抛异常。"""
    ro_parent = tmp_path / "ro_parent"
    ro_parent.mkdir()
    os.chmod(ro_parent, 0o555)
    monkeypatch.setattr(fc, "_CACHE_DIR", ro_parent / "child")
    fc._reset_l1_for_testing()

    try:
        assert fc.init_l1_cache() is False
        assert fc._l1_get("a" * 64) is None
    finally:
        os.chmod(ro_parent, 0o755)


def test_init_l1_cache_is_idempotent(tmp_path, monkeypatch):
    """重复调用 init_l1_cache() 不重复探测，结果一致。"""
    monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
    fc._reset_l1_for_testing()

    assert fc.init_l1_cache() is True
    # 再次调用：_l1_enabled 已是 True，直接返回（不会再走 mkdir）
    assert fc.init_l1_cache() is True
    assert fc._l1_enabled is True


def test_reset_l1_for_testing_clears_state(tmp_path, monkeypatch):
    """_reset_l1_for_testing() 把 _l1_enabled 置回 None。"""
    monkeypatch.setattr(fc, "_CACHE_DIR", tmp_path)
    fc.init_l1_cache()
    assert fc._l1_enabled is True

    fc._reset_l1_for_testing()
    assert fc._l1_enabled is None