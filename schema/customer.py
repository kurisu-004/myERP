"""客户相关的 Pydantic schema。

客户树是邻接表（parent_id 指向 t_customer.id），通过 `parent_name` 字段
把上级客户的名字一起返回，前端拿 `parent_name / name` 就能唯一定位节点。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schema._types import IdStr, IdStrNonNull


def _normalize_serial_prefix(v: str | None) -> str | None:
    """序列号前缀规范化：None 保持 None；其他先 uppercase 再校验 A-Z 单字符。

    - 一级客户的创建 / 更新入参会走这里；叶子客户的该字段会被 service 层忽略。
    - DB 端 check constraint `serial_prefix IS NULL OR serial_prefix ~ '^[A-Z]$'`
      与本规范化输出对齐。
    """
    if v is None:
        return None
    upper = v.upper()
    if len(upper) != 1 or not ("A" <= upper <= "Z"):
        raise ValueError(f"serial_prefix 必须是 A-Z 单字符：{v!r}")
    return upper


class CustomerOut(BaseModel):
    """单条客户展示用出参。

    `id` / `parent_id` 序列化为字符串（雪花 ID 19 位，超 JS
    `Number.MAX_SAFE_INTEGER`），避免 JS 精度截断。DB 仍存 BigInteger，
    但已统一为雪花 ID（与其他业务表一致）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: IdStrNonNull
    name: str
    parent_id: IdStr = None
    parent_name: str | None = None
    # 一级客户的序列号前缀（单字符 A-Z）；叶子客户为 null。
    # 这是「出参」字段，前端不需要 IdStr 包装（不是 ID）。
    serial_prefix: str | None = None


class CustomerCreateRequest(BaseModel):
    """新增客户。

    - `parent_id` 留空 → 一级客户（根）；此时 `serial_prefix` 必填。
    - `parent_id` 非空 → 二级客户；service 层忽略 `serial_prefix`（叶子继承根）。

    `parent_id` 是雪花 ID 字符串（与 CLAUDE.md §3 「雪花 ID 入参必须用
    str 类型」一致），service 层 int() 转回。
    """

    name: str = Field(min_length=1, max_length=100)
    parent_id: str | None = Field(
        default=None, description="父客户 id（雪花 ID 字符串）；NULL = 一级客户",
    )
    serial_prefix: str | None = Field(
        default=None,
        max_length=1,
        description="一级客户必填 A-Z；叶子客户忽略",
    )

    @field_validator("name")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("serial_prefix")
    @classmethod
    def normalize_prefix(cls, v: str | None) -> str | None:
        return _normalize_serial_prefix(v)


class CustomerUpdateRequest(BaseModel):
    """更新客户字段（全部可选，只更新传入的非 None 字段）。

    - 修改 `serial_prefix` 只影响后续创建的零件 / 装配体；已生成 serial_no 不动。
    - 一级客户显式传 None 想清空前缀 → service 层拒绝（BIZ_INVALID_VALUE 400）。
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: str | None = Field(
        default=None,
        description="父客户 id（雪花 ID 字符串）；NULL = 一级客户；显式传 None 表示去父",
    )
    serial_prefix: str | None = Field(
        default=None,
        max_length=1,
        description="一级客户的序列号前缀 A-Z；不可清空",
    )

    @field_validator("name")
    @classmethod
    def strip(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None

    @field_validator("serial_prefix")
    @classmethod
    def normalize_prefix(cls, v: str | None) -> str | None:
        return _normalize_serial_prefix(v)