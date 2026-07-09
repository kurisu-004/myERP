"""序列号机制核心。

格式：`<单字母客户代码><4 位数字>`，如 F1000 / L1234 / H1050。

- 每客户独立循环，pool 大小 = 9000（1000..9999 走完回到 1000，wrap 由
  `counter % SERIAL_POOL_SIZE` 实现）；
- 释放条件：状态变为 COMPLETED / CANCELLED 时 service 层把
  `serial_no` 置 NULL，号回到同客户的可用池里；counter 不动；
- 分配算法：见 `repository.serial_counter.SerialCounterRepository.acquire_serial`。
  用 `SELECT ... FOR UPDATE` 串行化同 prefix 的并发分配。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import status as http_status

from core.error_code import ErrCode
from core.exception import BizError
from model.enums import PartStatus

if TYPE_CHECKING:
    # 避开运行时循环导入（service.part / service.assembly → core.serial → model）。
    from model.customer import TCustomer

SERIAL_MIN = 1000
SERIAL_MAX = 9999
SERIAL_POOL_SIZE = 9000  # 单前缀同时活跃工单上限
SERIAL_FORMAT_LENGTH = 8  # "F9999" = 5 chars，留余量

# 计数器按 prefix 串行化时单次最多绕一圈（= pool size），
# 再绕说明已用尽，service 抛 BIZ_PART_SERIAL_EXHAUSTED。
SERIAL_ALLOC_MAX_ATTEMPTS = 9000

# 一级客户名 → 单字母代码的硬编码映射（**DEPRECATED** 兜底层）。
# 2026-07-09 起，serial_prefix 已迁到 t_customer.serial_prefix 列；
# 此 dict 仅作为「未设置 serial_prefix 的历史数据」兼容回退，新代码应使用
# `resolve_root_prefix(root_customer)`。迁移 000000000013 已经把现有 3 个
# 一级客户（法拉电子/路达/宏发）回填到 serial_prefix 列，正常路径不会再
# 走到这里。
PARENT_TO_CODE: dict[str, str] = {
    "法拉电子": "F",
    "路达": "L",
    "宏发": "H",
}

CODE_TO_PARENT: dict[str, str] = {v: k for k, v in PARENT_TO_CODE.items()}

# 服务层和 repo 层都要 import 的活跃/释放判定集合：
# 状态进入这两个值时 serial_no 置 NULL，号被释放。
SERIAL_RELEASE_STATUSES: frozenset[PartStatus] = frozenset(
    {PartStatus.COMPLETED, PartStatus.CANCELLED}
)

# 所有合法 prefix（种子迁移、validate 用）。A-Z 全集——
# t_serial_counter 预置 26 行（迁移 000000000014），任意字母可选。
SERIAL_PREFIXES: tuple[str, ...] = tuple(chr(ord("A") + i) for i in range(26))


def code_for_parent(parent_name: str) -> str | None:
    """按一级客户名取前缀（**DEPRECATED**，仅供兼容回退）。

    新代码请用 `resolve_root_prefix(root_customer)`，它会优先读
    `root.serial_prefix` 列，未设置时再回退到这里。
    """
    return PARENT_TO_CODE.get(parent_name)


def parent_for_code(code: str) -> str | None:
    return CODE_TO_PARENT.get(code)


def resolve_root_prefix(root: "TCustomer") -> str:
    """从一级客户 ORM 对象取前缀（service 层 4 个 acquire_serial 调用点统一入口）。

    解析顺序：
    1. `root.serial_prefix`（一级客户的业务字段，新建时必填）；若已设置则用之。
    2. `PARENT_TO_CODE[root.name]`（**DEPRECATED** 兜底）：兼容历史脏数据。
    3. 都拿不到 → 抛 BizError(BIZ_CUSTOMER_NOT_FOUND, 400)。

    返回值已 uppercase 规范化。
    """
    if root.serial_prefix:
        return root.serial_prefix.upper()
    legacy = PARENT_TO_CODE.get(root.name)
    if legacy is not None:
        return legacy
    raise BizError(
        code=ErrCode.BIZ_CUSTOMER_NOT_FOUND,
        message=f"未配置一级客户「{root.name}」的序列号前缀",
        http_status=http_status.HTTP_400_BAD_REQUEST,
    )
