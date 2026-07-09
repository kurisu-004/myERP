"""CNC 程序 schema（2026-07-10 起合并到 `schema.part_file.PartFileOut`）。

本文件保留为向后兼容的 alias；新代码请直接 `from schema.part_file import PartFileOut`。
"""
from __future__ import annotations

from schema.part_file import PartFileOut as CncProgramOut  # noqa: F401

__all__ = ["CncProgramOut"]