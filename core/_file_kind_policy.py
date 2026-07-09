"""t_part_file 文件 kind 与「写权限角色」「白名单扩展名」的映射。

放 `core/` 而不是 `service/` 是为了避免 `core.permission` ↔
`service.part_file` 循环导入（permission 工厂依赖此模块，
service 也依赖 permission）。
"""
from __future__ import annotations

from model.enums import PartFileKind, UserRole

# 单文件 kind（每 part / owner 最多 1 份）：DRAWING / 3D_MODEL /
# SETUP_SHEET / ASSEMBLY_MASTER。
# G_CODE 不在内，允许多版本。
SINGLE_FILE_KINDS: frozenset[PartFileKind] = frozenset({
    PartFileKind.DRAWING,
    PartFileKind.THREE_D_MODEL,
    PartFileKind.SETUP_SHEET,
    PartFileKind.ASSEMBLY_MASTER,
})


# 每个 kind 允许的文件扩展名（小写，不含点号）。
ALLOWED_EXTS_BY_KIND: dict[PartFileKind, frozenset[str]] = {
    PartFileKind.DRAWING:         frozenset({"pdf"}),
    PartFileKind.THREE_D_MODEL:   frozenset({"step", "stp"}),
    PartFileKind.G_CODE:          frozenset({"nc", "tap", "cnc", "mpf", "ngc"}),
    PartFileKind.SETUP_SHEET:     frozenset({"pdf"}),
    PartFileKind.ASSEMBLY_MASTER: frozenset({"pdf"}),
}


# 每个 kind 的写权限角色集（命中任一即可）。
# - DRAWING / 3D_MODEL / ASSEMBLY_MASTER：MANAGER + CLERK（文员日常操作）
# - G_CODE / SETUP_SHEET：MANAGER + CNC_PROGRAMMER（编程员负责）
WRITE_ROLES_BY_KIND: dict[PartFileKind, frozenset[UserRole]] = {
    PartFileKind.DRAWING:         frozenset({UserRole.MANAGER, UserRole.CLERK}),
    PartFileKind.THREE_D_MODEL:   frozenset({UserRole.MANAGER, UserRole.CLERK}),
    PartFileKind.G_CODE:          frozenset({UserRole.MANAGER, UserRole.CNC_PROGRAMMER}),
    PartFileKind.SETUP_SHEET:     frozenset({UserRole.MANAGER, UserRole.CNC_PROGRAMMER}),
    PartFileKind.ASSEMBLY_MASTER: frozenset({UserRole.MANAGER, UserRole.CLERK}),
}