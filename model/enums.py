import enum


class PartStatus(str, enum.Enum):
    """零件订单状态机。

    DB 存储一律用 `varchar(20)`，**不**用 PostgreSQL 原生 ENUM。
    Python 层靠本 Enum 做合法值校验，service 层抛 `BIZ_INVALID_VALUE` /
    `BIZ_INVALID_TRANSITION`。

    流转示意：
        PENDING ─place_on_shelf─▶ IN_PROCESS ─pick_up─▶ IN_PROCESS（holder=工人）
                                                       ─return──▶ IN_PROCESS（holder=货架）
                                                       ─inspect──▶ INSPECTION ──▶ READY_TO_SHIP ──▶ DELIVERED ──▶ COMPLETED
        （INSPECTION / IN_PROCESS / READY_TO_SHIP / DELIVERED）──▶ REPAIRING ──▶ IN_PROCESS
        任意状态 ──▶ CANCELLED（取消）
    """

    PENDING = "PENDING"               # 待生产（还没放到货架上，办公室暂存）
    PROGRAMMING = "PROGRAMMING"       # 编程中（已发送至 CNC 编程，等待/正在编程）
    IN_PROCESS = "IN_PROCESS"         # 生产中（在生产货架上 OR 在工人手里）
    INSPECTION = "INSPECTION"         # 待品检（在品检货架上）
    READY_TO_SHIP = "READY_TO_SHIP"   # 待送货
    DELIVERED = "DELIVERED"           # 已送货
    REPAIRING = "REPAIRING"           # 返修中
    COMPLETED = "COMPLETED"           # 已完成
    CANCELLED = "CANCELLED"           # 已取消


class PartLocation(str, enum.Enum):
    """零件物理位置。

    解决 current_holder_id 多态歧义：通过 location 明确指向 shelf 表还是 worker 表。
    """
    OFFICE = "OFFICE"                       # 文员处（PENDING 状态）
    PRODUCTION_SHELF = "PRODUCTION_SHELF"   # 生产货架（IN_PROCESS 状态）
    WORKER = "WORKER"                       # 工人手中（IN_PROCESS 状态）
    INSPECTION_SHELF = "INSPECTION_SHELF"   # 品检货架（INSPECTION 状态）


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


class PartEventType(str, enum.Enum):
    """订单全生命周期事件类型。

    DB 存 `varchar(30)`，Python 层校验。t_part_event.event_type 取值集合。

    - CREATED          零件创建
    - RELEASED         历史值，保留以兼容历史行（PENDING → READY 旧流程）。不再触发新事件。
    - SENT_TO_PROGRAMMING  文员把零件发送至 CNC 编程（PENDING → PROGRAMMING）
    - CNC_RELEASED     编程员上传 G 代码后下发到生产货架（PROGRAMMING → IN_PROCESS）
    - PLACED_ON_SHELF  文员把零件放到生产货架（PENDING → IN_PROCESS）
    - PICKED_UP        工人领取（holder 由货架改为工人；状态不变）
    - RETURNED         工人归还（holder 由工人改回货架；状态不变）
    - INSPECTED        工人扫图纸送检（→ INSPECTION，并改 holder 到品检货架）
    - STATUS_CHANGED   通用状态变更（含 change-status 端点的非扫码转换）
    - REPAIR_STARTED   → REPAIRING
    - REPAIR_COMPLETED REPAIRING → IN_PROCESS
    - CANCELLED        → CANCELLED
    - COMPLETED        → COMPLETED
    """

    CREATED = "CREATED"
    RELEASED = "RELEASED"                 # 历史值保留
    SENT_TO_PROGRAMMING = "SENT_TO_PROGRAMMING"
    CNC_RELEASED = "CNC_RELEASED"
    PLACED_ON_SHELF = "PLACED_ON_SHELF"
    PICKED_UP = "PICKED_UP"
    RETURNED = "RETURNED"
    INSPECTED = "INSPECTED"
    INSPECTION_FAILED = "INSPECTION_FAILED"   # 品检不通过，打回生产货架
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


class ShelfZone(str, enum.Enum):
    """货架所属区域。

    DB 存 `varchar(16)`。PRODUCTION = 生产区；INSPECTION = 品检区。
    """

    PRODUCTION = "PRODUCTION"
    INSPECTION = "INSPECTION"


class UserRole(str, enum.Enum):
    """账号角色。

    DB 存 `varchar(20)`（t_user_role.role）。一个用户可有多个角色。

    SHELF_ACCOUNT 角色必须配 `scope_type='shelf' / scope_id=<shelf.id>`；
    其它角色（MANAGER / CLERK / INSPECTOR / CNC_PROGRAMMER）scope 通常为 NULL。
    """

    MANAGER = "MANAGER"               # 后台管理员；可访问所有管理端点
    SHELF_ACCOUNT = "SHELF_ACCOUNT"   # 货架一体机登录账号；必须 scope 到具体 shelf
    CLERK = "CLERK"                   # 文员：下单 / 投放 / 下发 / 发送至 CNC 编程
    INSPECTOR = "INSPECTOR"           # 预留：品检员验收
    CNC_PROGRAMMER = "CNC_PROGRAMMER"  # CNC 编程员：待编程一览 / 上传 G 代码 / 下发生产


class PartSortKey(str, enum.Enum):
    """零件列表支持的排序字段。"""

    PLANNED_DELIVERY_DATE = "PLANNED_DELIVERY_DATE"
    REQUEST_DATE = "REQUEST_DATE"
    CREATED_AT = "CREATED_AT"
    SERIAL_NO = "SERIAL_NO"
    DRAWING_NO = "DRAWING_NO"
    NAME = "NAME"


class SortDir(str, enum.Enum):
    ASC = "ASC"
    DESC = "DESC"


class ProcessCategory(str, enum.Enum):
    """工序类别。

    DB 存 `varchar(16)`。INHOUSE = 自产（车间内加工）；
    OUTSOURCE = 外协（外发给供应商 / 加工厂）。
    """
    INHOUSE = "INHOUSE"
    OUTSOURCE = "OUTSOURCE"


class PartFileKind(str, enum.Enum):
    """零件 / 装配体的统一文件类型。

    DB 存 `varchar(20)` (`t_part_file.kind`)，check 约束限定在 6 个取值。

    - DRAWING          零件 / 装配体子件的图纸 (PDF / PNG / JPG / JPEG / GIF /
                       BMP / TIF / TIFF / WEBP / HEIC) — 单文件约束
    - THREE_D_MODEL    零件 3D 模型 (STEP / STP / IGES / IGS / STL / OBJ / 3MF) — 单文件约束
    - G_CODE           零件的 CNC G 代码 (NC / TAP / CNC / MPF / NGC) — 多版本
    - SETUP_SHEET      零件的 CNC 设定单 (PDF) — 单文件约束
    - ASSEMBLY_MASTER  装配体的总装图 (PDF)，polymorphic part_id = assembly.id — 单文件约束
    - CAD_2D           零件 CAD 源文件 (DWG / DXF) — 单文件约束（2026-07-14 新增）

    单文件约束 (除 G_CODE 外)：每 part 每 kind 最多 1 份；索引
    `uk_t_part_file_single` 在 DB 层强制。

    DRAWING 与图片格式（PNG/JPG/...）共用同一槽位：上传 PDF 时如果已有 PNG
    图纸，旧行 soft_delete、COS 旧对象异步清，新 PDF 行创建（覆盖语义）。
    """

    DRAWING = "DRAWING"
    THREE_D_MODEL = "3D_MODEL"
    G_CODE = "G_CODE"
    SETUP_SHEET = "SETUP_SHEET"
    ASSEMBLY_MASTER = "ASSEMBLY_MASTER"
    CAD_2D = "CAD_2D"
