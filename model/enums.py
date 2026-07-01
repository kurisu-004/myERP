import enum


class PartStatus(str, enum.Enum):
    """零件订单状态机。

    DB 存储一律用 `varchar(20)`，**不**用 PostgreSQL 原生 ENUM。
    Python 层靠本 Enum 做合法值校验，service 层抛 `BIZ_INVALID_VALUE` /
    `BIZ_INVALID_TRANSITION`。

    流转示意：
        PENDING ─release─▶ READY ─pick_up─▶ IN_PROCESS ─return─▶ READY
        (待生产)              (就绪/待加工区)    (生产中)             ↗
                                                   │
                                                   └──finish──▶ INSPECTION ── ... ──▶ COMPLETED
                                                   └────────────────finish (从 READY 也可)──▶ INSPECTION
        任意状态 ──▶ REPAIRING ──▶ IN_PROCESS（返修后回生产）
        任意状态 ──▶ CANCELLED（取消）
    """

    PENDING = "PENDING"               # 待生产
    READY = "READY"                   # 就绪（待加工区，等待工人领取）
    IN_PROCESS = "IN_PROCESS"         # 生产中（工人持有）
    INSPECTION = "INSPECTION"         # 待品检
    READY_TO_SHIP = "READY_TO_SHIP"   # 待送货
    DELIVERED = "DELIVERED"           # 已送货
    REPAIRING = "REPAIRING"           # 返修中
    COMPLETED = "COMPLETED"           # 已完成
    CANCELLED = "CANCELLED"           # 已取消


class AssemblyStatus(str, enum.Enum):
    """装配件状态机。

    DB 存 `varchar(20)`，Python 层靠本 Enum 做合法值校验，service 层抛
    `BIZ_INVALID_VALUE` / `BIZ_INVALID_TRANSITION`。

    流转示意（service 层 / 事件触发维护）：
        PENDING ──任一子件进入生产/品检/待送货/已送货──▶ IN_PROCESS
                                                                  │
        所有子件 COMPLETED ────────────────────────────────────▶ COMPLETED
        手动取消（整装级联，所有子件一同 CANCELLED）───▶ CANCELLED

    注意：
    - IN_PROCESS / COMPLETED 切换由 service 在子件状态变更事件里**自动维护**，
      不会走 `change-status` 端点（避免与子件状态不一致）。
    - CANCELLED 是**显式动作**：调用 `POST /assemblies/{id}/cancel` 端点，
      service 在该事务内把所有未完成的子件也置为 CANCELLED（保留审计事件）。
    - 终态（COMPLETED / CANCELLED）不接受任何再变更。
    """

    PENDING = "PENDING"           # 创建时初始状态
    IN_PROCESS = "IN_PROCESS"     # 至少有一个子件进入生产环节
    COMPLETED = "COMPLETED"       # 所有子件均 COMPLETED
    CANCELLED = "CANCELLED"       # 手动取消（含级联子件）


# Assembly 合法状态转换矩阵（service 层校验）。
# - PENDING → IN_PROCESS：由子件状态变更事件触发
# - IN_PROCESS → COMPLETED：所有子件都 COMPLETED 时由事件触发
# - * → CANCELLED：手动调用 cancel 端点
# 终态（COMPLETED / CANCELLED）不出现在 from 侧。
ASSEMBLY_TRANSITIONS: frozenset[tuple[AssemblyStatus, AssemblyStatus]] = frozenset(
    {
        (AssemblyStatus.PENDING, AssemblyStatus.IN_PROCESS),
        (AssemblyStatus.PENDING, AssemblyStatus.CANCELLED),
        (AssemblyStatus.IN_PROCESS, AssemblyStatus.COMPLETED),
        (AssemblyStatus.IN_PROCESS, AssemblyStatus.CANCELLED),
    }
)


# Part 合法状态转换矩阵（service 层校验）。
# 注意：任意状态 → CANCELLED 不列在内，由 service 单独放行。
PART_TRANSITIONS: frozenset[tuple[PartStatus, PartStatus]] = frozenset(
    {
        # 文员 release（文员点击"开始生产"）
        (PartStatus.PENDING, PartStatus.READY),
        # 工人扫码领取 / 归还（可在 ready / in_process 间反复）
        (PartStatus.READY, PartStatus.IN_PROCESS),
        (PartStatus.IN_PROCESS, PartStatus.READY),
        # 工人扫图纸送检（ready 或 in_process 都可）
        (PartStatus.READY, PartStatus.INSPECTION),
        (PartStatus.IN_PROCESS, PartStatus.INSPECTION),
        # 既有正向流水线
        (PartStatus.INSPECTION, PartStatus.READY_TO_SHIP),
        (PartStatus.READY_TO_SHIP, PartStatus.DELIVERED),
        (PartStatus.DELIVERED, PartStatus.COMPLETED),
        # 返修闭环
        (PartStatus.IN_PROCESS, PartStatus.REPAIRING),
        (PartStatus.INSPECTION, PartStatus.REPAIRING),
        (PartStatus.READY_TO_SHIP, PartStatus.REPAIRING),
        (PartStatus.DELIVERED, PartStatus.REPAIRING),
        (PartStatus.REPAIRING, PartStatus.IN_PROCESS),
    }
)


class PartEventType(str, enum.Enum):
    """订单全生命周期事件类型。

    DB 存 `varchar(30)`，Python 层校验。t_part_event.event_type 取值集合。

    - CREATED          零件创建
    - RELEASED         文员点击"开始生产"（PENDING → READY）
    - PICKED_UP        工人扫码领取（READY → IN_PROCESS）
    - RETURNED         工人扫图纸归还（IN_PROCESS → READY）
    - INSPECTED        工人扫图纸送检（→ INSPECTION）
    - STATUS_CHANGED   通用状态变更（含 change-status 端点的非扫码转换）
    - REPAIR_STARTED   → REPAIRING
    - REPAIR_COMPLETED REPAIRING → IN_PROCESS
    - CANCELLED        → CANCELLED
    - COMPLETED        → COMPLETED
    """

    CREATED = "CREATED"
    RELEASED = "RELEASED"
    PICKED_UP = "PICKED_UP"
    RETURNED = "RETURNED"
    INSPECTED = "INSPECTED"
    STATUS_CHANGED = "STATUS_CHANGED"
    REPAIR_STARTED = "REPAIR_STARTED"
    REPAIR_COMPLETED = "REPAIR_COMPLETED"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"


# 扫码类事件（pick-up / return / inspect）的子集；
# 前端扫码页只展示这三种。
SCAN_EVENT_TYPES: frozenset[PartEventType] = frozenset(
    {PartEventType.PICKED_UP, PartEventType.RETURNED, PartEventType.INSPECTED}
)


class PartSortKey(str, enum.Enum):
    """零件列表支持的排序字段。"""

    PLANNED_DELIVERY_DATE = "PLANNED_DELIVERY_DATE"
    REQUEST_DATE = "REQUEST_DATE"
    CREATED_AT = "CREATED_AT"


class SortDir(str, enum.Enum):
    ASC = "ASC"
    DESC = "DESC"