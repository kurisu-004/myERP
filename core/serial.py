"""序列号机制核心。

格式：`<单字母客户代码><4 位数字>`，如 F1000 / L1234。

- 每客户独立循环：F1000, F1001, ..., F9999, F1000 (wrap)；
- 释放条件：状态变为 COMPLETED / CANCELLED 时，service 层把
  `serial_no` 置 NULL，号回到同客户的可用池里；
- 分配算法：找 [SERIAL_MIN, SERIAL_MAX] 内最小的未被该客户占用的号。
  若全部被占用，循环回到 SERIAL_MIN。
"""

from __future__ import annotations

SERIAL_MIN = 1000
SERIAL_MAX = 9999
SERIAL_FORMAT_LENGTH = 8  # "F9999" = 5 chars, 留余量

# 一级客户名 → 单字母代码的硬编码映射。
# 扩展新一级客户时在此加一行（建议用首字母，避 I/O 容易混淆）。
PARENT_TO_CODE: dict[str, str] = {
    "法拉电子": "F",
    "路达": "L",
}

CODE_TO_PARENT: dict[str, str] = {v: k for k, v in PARENT_TO_CODE.items()}


def code_for_parent(parent_name: str) -> str | None:
    return PARENT_TO_CODE.get(parent_name)


def parent_for_code(code: str) -> str | None:
    return CODE_TO_PARENT.get(code)