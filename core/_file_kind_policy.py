"""t_part_file 文件 kind 与「写权限角色」「白名单扩展名」的映射。

放 `core/` 而不是 `service/` 是为了避免 `core.permission` ↔
`service.part_file` 循环导入（permission 工厂依赖此模块，
service 也依赖 permission）。
"""
from __future__ import annotations

from model.enums import PartFileKind, UserRole

# 单文件 kind（每 part / owner 最多 1 份）：DRAWING / 3D_MODEL /
# SETUP_SHEET / ASSEMBLY_MASTER / CAD_2D。
# G_CODE 不在内，允许多版本。
SINGLE_FILE_KINDS: frozenset[PartFileKind] = frozenset({
    PartFileKind.DRAWING,
    PartFileKind.THREE_D_MODEL,
    PartFileKind.SETUP_SHEET,
    PartFileKind.ASSEMBLY_MASTER,
    PartFileKind.CAD_2D,
})


# 每个 kind 允许的文件扩展名（小写，不含点号）。
# DRAWING 同时接受 PDF 与 8 种图片格式（图片与 PDF 同槽"图纸"概念，
# 单文件覆盖语义）；DRAWING 打印背面要打序列号，详见 service.printing。
ALLOWED_EXTS_BY_KIND: dict[PartFileKind, frozenset[str]] = {
    PartFileKind.DRAWING: frozenset({
        "pdf",                            # 原始 PDF 图纸
        "png", "jpg", "jpeg",             # 常见光栅图
        "gif", "bmp",                     # 老格式
        "tif", "tiff",                    # 高保真扫描 / 工业相机
        "webp",                           # 现代压缩
        "heic",                           # iOS 高效图（pillow 需 pillow-heif）
    }),
    PartFileKind.THREE_D_MODEL: frozenset({
        "step", "stp",                    # ISO 10303-21 (STEP)
        "iges", "igs",                    # ISO 10303-21 (IGES, 旧版)
        "stl",                            # 三角面片
        "obj",                            # Wavefront OBJ
        "3mf",                            # 3D Manufacturing Format
    }),
    PartFileKind.G_CODE:          frozenset({"nc", "tap", "cnc", "mpf", "ngc"}),
    PartFileKind.SETUP_SHEET:     frozenset({"pdf"}),
    PartFileKind.ASSEMBLY_MASTER: frozenset({"pdf"}),
    PartFileKind.CAD_2D:          frozenset({"dwg", "dxf"}),
}


# 每个 kind 的写权限角色集（命中任一即可）。
# - DRAWING / 3D_MODEL / ASSEMBLY_MASTER / CAD_2D：MANAGER + CLERK（文员日常）
# - G_CODE / SETUP_SHEET：MANAGER + CNC_PROGRAMMER（编程员负责）
WRITE_ROLES_BY_KIND: dict[PartFileKind, frozenset[UserRole]] = {
    PartFileKind.DRAWING:         frozenset({UserRole.MANAGER, UserRole.CLERK}),
    PartFileKind.THREE_D_MODEL:   frozenset({UserRole.MANAGER, UserRole.CLERK}),
    PartFileKind.G_CODE:          frozenset({UserRole.MANAGER, UserRole.CNC_PROGRAMMER}),
    PartFileKind.SETUP_SHEET:     frozenset({UserRole.MANAGER, UserRole.CNC_PROGRAMMER}),
    PartFileKind.ASSEMBLY_MASTER: frozenset({UserRole.MANAGER, UserRole.CLERK}),
    PartFileKind.CAD_2D:          frozenset({UserRole.MANAGER, UserRole.CLERK}),
}