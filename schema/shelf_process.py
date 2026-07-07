"""货架 ↔ 工序 映射 (ShelfProcess) Pydantic schema."""
from pydantic import BaseModel, Field

from schema._types import IdStrNonNull


class ShelfProcessLinkOut(BaseModel):
    """单条映射条目（用于嵌套显示）。"""

    process_id: IdStrNonNull
    process_code: str
    process_name: str
    sort_order: int


class ShelfWithProcessesOut(BaseModel):
    """货架 + 该货架映射的全部工序（含 sort_order）。"""

    id: IdStrNonNull
    code: str
    name: str
    zone: str
    processes: list[ShelfProcessLinkOut] = Field(default_factory=list)


class SetShelfProcessRequest(BaseModel):
    """整体替换某货架的工序映射。

    前端保存时把当前勾选的 process_id 列表一次性提交；
    service 端 delete-then-insert 替换原映射。
    """

    process_ids: list[int] = Field(
        default_factory=list,
        description="该货架可执行的工序 id 列表（提交顺序即 sort_order）",
    )
