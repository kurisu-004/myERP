"""Unit tests for CJK font resolution in service/printing.py.

覆盖 `_load_cn_font` 的三种解析路径：
1. `settings.print_cn_font_path` 命中 → 返回 FreeType 字体
2. 候选路径命中 → 返回 FreeType 字体
3. 全部失败 + strict=False → 退回 PIL 默认位图 + warning
4. 全部失败 + strict=True → RuntimeError

并校验 `_build_info_card_page` 在显式 CJK 字体可用时能生成可读图像。

测试不要求宿主机预装中文字体：通过 monkeypatch 把候选路径与
`print_cn_font_path` 都打到不存在的路径，模拟「无字体」环境。
"""
from __future__ import annotations

import os

import pytest
from PIL import Image, ImageFont

import service.printing as printing
import core.config as config_mod


@pytest.fixture
def reset_cn_font_cache():
    """重置 `_load_cn_font` 的 lru_cache + 一次性 warning 标记。"""
    printing._load_cn_font.cache_clear()
    printing._CN_FONT_FALLBACK_WARNED = False
    yield
    printing._load_cn_font.cache_clear()
    printing._CN_FONT_FALLBACK_WARNED = False


def _real_ttf() -> str | None:
    """返回宿主机上能找到的第一个 TTF/TTC 路径，测试用 fixture 复用。"""
    for p in (
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ):
        if os.path.isfile(p):
            return p
    return None


@pytest.fixture
def fake_cn_font(tmp_path, monkeypatch):
    """写一个可被 PIL truetype() 接受的 TTF，并指向 settings.print_cn_font_path。"""
    real = _real_ttf()
    if real is None:
        pytest.skip("no host TTF available to back fake_cn_font fixture")
    fake = tmp_path / "fake-cjk.ttc"
    fake.write_bytes(open(real, "rb").read())
    settings = config_mod.settings.model_copy(update={
        "print_cn_font_path": str(fake),
        "print_cn_font_strict": False,
    })
    monkeypatch.setattr(printing, "settings", settings)
    return str(fake)


def test_load_cn_font_configured_path_takes_priority(
    reset_cn_font_cache, fake_cn_font,
):
    """配置路径命中时优先使用，不走候选扫描。"""
    font = printing._load_cn_font(32)
    assert isinstance(font, ImageFont.FreeTypeFont)
    assert font.path == fake_cn_font


def test_load_cn_font_candidate_path_used_when_unconfigured(
    reset_cn_font_cache, monkeypatch,
):
    """未配置 print_cn_font_path 时，走候选路径列表。"""
    settings = config_mod.settings.model_copy(update={
        "print_cn_font_path": "",
        "print_cn_font_strict": False,
    })
    monkeypatch.setattr(printing, "settings", settings)
    font = printing._load_cn_font(32)
    assert isinstance(font, ImageFont.FreeTypeFont)


def test_load_cn_font_fallback_warns_when_missing(
    reset_cn_font_cache, monkeypatch, caplog,
):
    """所有候选失败 + strict=False → 退回 PIL 默认 + warning。"""
    settings = config_mod.settings.model_copy(update={
        "print_cn_font_path": "",
        "print_cn_font_strict": False,
    })
    monkeypatch.setattr(printing, "settings", settings)
    monkeypatch.setattr(
        printing, "_CN_FONT_CANDIDATES",
        ["/nonexistent/__no_such_a.ttf", "/nonexistent/__no_such_b.ttf"],
    )
    with caplog.at_level("WARNING", logger="service.printing"):
        f1 = printing._load_cn_font(32)
        f2 = printing._load_cn_font(48)  # 第二次不应再 warn
    # PIL default 字体类型不一定是 FreeTypeFont；只要不是 None 即可
    assert f1 is not None
    warning_count = sum(
        1 for rec in caplog.records if "CJK font not found" in rec.message
    )
    assert warning_count == 1, f"warning 应只发一次，实发 {warning_count} 次"


def test_load_cn_font_strict_raises_when_missing(
    reset_cn_font_cache, monkeypatch,
):
    """strict=True + 全部失败 → RuntimeError，不静默退到 tofu。"""
    settings = config_mod.settings.model_copy(update={
        "print_cn_font_path": "/nonexistent/__strict.ttf",
        "print_cn_font_strict": True,
    })
    monkeypatch.setattr(printing, "settings", settings)
    monkeypatch.setattr(
        printing, "_CN_FONT_CANDIDATES",
        ["/nonexistent/__strict_a.ttf", "/nonexistent/__strict_b.ttf"],
    )
    with pytest.raises(RuntimeError, match="PRINT_CN_FONT_STRICT"):
        printing._load_cn_font(32)


def test_build_info_card_uses_configured_font(
    reset_cn_font_cache, fake_cn_font,
):
    """信息卡渲染走配置的 CJK 字体；图像包含明显非白像素。"""
    img = printing._build_info_card_page(
        orientation="landscape",
        drawing_no="DWG-001",
        name="测试零件",
        serial_no="F1004",
        customer_path="法拉/路达",
    )
    assert isinstance(img, Image.Image)
    gray = img.convert("L")
    ink = sum(1 for px in gray.getdata() if px < 200)
    # 配置了真 TTF 后，标题/字段都应有足量 ink；用 500 作为保守下限
    assert ink > 500, f"信息卡几乎全白，疑似字体未生效（ink={ink}）"
