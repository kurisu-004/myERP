"""工种 ↔ 工序 映射 (WorkTypeProcess) Pydantic schema。"""
from pydantic import BaseModel, Field

from schema._types import IdStrNonNull
from schema.work_type import WorkTypeOut


class WorkTypeProcessLinkOut(BaseModel):
    """单条映射条目（用于嵌套显示）。"""

    process_id: IdStrNonNull
    process_code: str
    process_name: str
    sort_order: int


class WorkTypeWithProcessesOut(WorkTypeOut):
    """工种 + 该工种映射的全部工序（含 sort_order）。"""

    processes: list[WorkTypeProcessLinkOut] = Field(default_factory=list)


class SetWorkTypeProcessRequest(BaseModel):
    """整体替换某工种的工序映射。

    前端保存时把当前勾选的 process_id 列表一次性提交；
    service 端 delete-then-insert 替换原映射。
    """

    process_ids: list[int] = Field(
        default_factory=list,
        description="该工种可执行的工序 id 列表（提交顺序即 sort_order）",
    )