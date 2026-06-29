"""序列号机制核心。

格式：`<单字母客户代码><4 位数字>`，如 F1000 / L1234 / H1050。

- 每客户独立循环，pool 大小 = 5000（1000..5999 走完回到 1000，wrap 由
  `counter % SERIAL_POOL_SIZE` 实现）；
- 释放条件：状态变为 COMPLETED / CANCELLED 时 service 层把
  `serial_no` 置 NULL，号回到同客户的可用池里；counter 不动；
- 分配算法：见 `repository.serial_counter.SerialCounterRepository.acquire_serial`。
  用 `SELECT ... FOR UPDATE` 串行化同 prefix 的并发分配。
"""

from __future__ import annotations

from model.enums import PartStatus

SERIAL_MIN = 1000
SERIAL_MAX = 5999  # 由 9999 改为 5999（公式 1000 + counter % 5000 的上界）
SERIAL_POOL_SIZE = 5000  # 单前缀同时活跃工单上限
SERIAL_FORMAT_LENGTH = 8  # "F5999" = 5 chars，留余量

# 计数器按 prefix 串行化时单次最多绕一圈（= pool size），
# 再绕说明已用尽，service 抛 BIZ_PART_SERIAL_EXHAUSTED。
SERIAL_ALLOC_MAX_ATTEMPTS = 5000

# 一级客户名 → 单字母代码的硬编码映射。
# 扩展新一级客户时：在此加一行 + 在 t_serial_counter 种子迁移里加一行。
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

# 所有合法 prefix（种子迁移、validate 用）。
SERIAL_PREFIXES: tuple[str, ...] = ("L", "F", "H")


def code_for_parent(parent_name: str) -> str | None:
    return PARENT_TO_CODE.get(parent_name)


def parent_for_code(code: str) -> str | None:
    return CODE_TO_PARENT.get(code)
