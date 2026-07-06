# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

myERP —— 零件加工订单管理系统。覆盖法拉电子、路达两家一级客户及其下分厂/部门的零件下单、生产跟踪、交付闭环。

- 后端：FastAPI + SQLAlchemy 2.0 异步 + asyncpg + Alembic + Pydantic v2
- 前端：`frontend/`（Vue 3 + Vite + TypeScript + Element Plus）
- 数据库：PostgreSQL 18（`docker-compose.yml` 提供容器）
- 包管理：uv（依赖在 `pyproject.toml` / `uv.lock`）
- 文件存储：腾讯云 COS（`core/cos.py`，后端上传模式）
- ID 方案：`utils/id_gen.py` 生成的雪花 ID；`t_customer` 用自增
- CI/CD：GitHub Actions → GHCR → SSH 部署（`.github/workflows/deploy.yml`）

## 架构总览

请求从 `api/` 进，按 `api → service → repository → model` 分层流转：

```
api/v1/*.py          # FastAPI 路由（薄层，只做参数提取和响应）
   ↓ Depends
api/deps.py          # get_uow / get_*_service 依赖注入
   ↓
service/*.py         # 业务逻辑、状态机校验、抛 BizError
   ↓
repository/*.py      # 数据访问（每个聚合一个 Repository）
   ↓
model/*.py           # SQLAlchemy ORM
```

`core/` 放横切关注点：
- `config.py` — `Settings`（pydantic-settings，从 `.env` 读）
- `database.py` — 异步 engine + `SessionLocal` + `get_db` + lifespan
- `exception.py` — `BizError`（业务异常，含 `ErrCode` 与 `http_status`）
- `error_code.py` — 业务错误码枚举
- `exception_handler.py` — 把 `BizError` 等转成统一响应
- `middleware.py` — `UnifiedResponseMiddleware`（统一响应包装）
- `response.py` — 统一响应结构
- `cos.py` — 腾讯云 COS 文件上传/下载（后端 SDK 模式）
- `dashboard.py` — 仪表盘聚合查询
- `serial.py` — 流水号生成

### 当前模块清单

| 聚合 | Model | Repository | Service | API 路由 | Schema |
|------|-------|-----------|---------|----------|--------|
| 零件 | `TPart` | `PartRepository` | `part.py` | `api/v1/part.py` | `schema/part.py` |
| 零件事件 | `TPartEvent` | `PartEventRepository` | — | — | `schema/part.py` |
| 客户 | `TCustomer` | `CustomerRepository` | `customer.py` | `api/v1/customer.py` | `schema/customer.py` |
| 装配体 | `TAssembly` | `AssemblyRepository` | `assembly.py` | `api/v1/assembly.py` | `schema/assembly.py` |
| 图纸文件 | `TDrawingFile` | `DrawingFileRepository` | `drawing.py` | `api/v1/drawing.py` | `schema/drawing.py` |
| CNC 程序 | `TCncProgram` | `CncProgramRepository` | `cnc_program.py` | `api/v1/cnc_program.py` | `schema/cnc_program.py` |
| 工人 | `TWorker` | `WorkerRepository` | `worker.py` | `api/v1/worker.py` | `schema/worker.py` |
| 流水号 | `TSerialCounter` | `SerialCounterRepository` | — | — | — |
| WebSocket | — | — | — | `api/v1/ws.py` | — |

## 关键约定（务必遵守）

### 1. 数据库中禁止使用物理外键

所有跨表引用都是普通列 + 普通索引，**不**在 `model/*.py` 写 `ForeignKey(...)`，也**不**在 alembic 迁移里写 `sa.ForeignKey(...)`。

引用完整性、级联删除防悬空、防自环等由 **service 层** 校验：
- 写入前用 repository `get_by_id` 校验目标存在
- 删除聚合根前检查子记录
- `t_customer.parent_id` 写入前在 service 校验不会形成环

新加列如果逻辑上指向别表，照此办理；需要"防单行自引用"这种纯本地约束，用 `CheckConstraint` 即可（如 `parent_id IS NULL OR parent_id <> id`）。

### 2. 审计字段（AuditMixin / EventTimestampMixin）

**`Base` 是空的**（只有 `__abstract__ = True`），不包含任何字段。审计字段通过 `model/audit.py` 中的 mixin 按需继承：

| Mixin | 用途 | 字段 |
|-------|------|------|
| `AuditMixin` | 业务主表 | `created_at`, `created_by`, `updated_at`, `updated_by`, `deleted_at` |
| `EventTimestampMixin` | 事件/日志表 | `created_at`（只有创建时间） |

使用方式：
```python
# 业务主表
class TPart(Base, AuditMixin):
    ...

# 事件/日志表（append-only，不更新不删除）
class TPartEvent(Base, EventTimestampMixin):
    ...
```

约定：
- 字段顺序固定为 `created_at → created_by → updated_at → updated_by → deleted_at`，与 DB 列序一致。
- **禁止**直接 `session.delete()`；统一走 repository 的 `soft_delete(model)` 方法写 `deleted_at = utcnow()`。
- 默认查询条件是 `deleted_at IS NULL`；repository 已自动加。需要查全部时显式传 `include_deleted=True`。
- `created_by` / `updated_by` 由调用方（service 层）显式赋值；当前无用户体系时按 NULL 处理。
- **不要**在子类重复声明这些字段——会与 mixin 冲突。

### 3. ID 生成与 JSON 序列化

**DB 层**：
- `t_part` 等业务表：雪花 ID，列定义为 `Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)`。
- `t_customer`：自增 `autoincrement=True`（`BigInteger`）。
- 雪花参数从 `.env` 读：`SNOWFLAKE_INSTANCE` / `SNOWFLAKE_SEQ` / `SNOWFLAKE_EPOCH`。

**JSON 序列化**（防止 JS 精度丢失）：
- `schema/_types.py` 定义 `IdStr`（可空）和 `IdStrNonNull`（非空）两个类型。
- 所有 Pydantic schema 的 ID 字段统一使用这两个类型，确保 JSON 响应中 ID 都是字符串。
- **仅影响序列化**（`when_used="json"`），Python 内部仍是 `int`，DB 列保持 `BigInteger` 不动。
- service 层 / repository 层接收时用 `int`，不受影响。

```python
# schema 中使用
assembly_id: IdStr = None       # 可空外键 → JSON: "123456..." 或 null
id: IdStrNonNull                # 非空主键 → JSON: "123456..."
```

### 4. 状态机校验在 service 层

`PartStatus`（9 个 DB 状态）和 `AssemblyStatus`（4 个状态）定义在 `model/enums.py`。状态流转规则由 `python-statemachine` (`StateChart`) 管理，详见 [第 9 节](#9-状态机约定)。

旧的手写 `PART_TRANSITIONS` / `ASSEMBLY_TRANSITIONS` frozenset 和 `_assert_transition()` 已移除，迁移到 `statemachines/` 下的状态机类。

Assembly 的 `IN_PROCESS` / `COMPLETED` 切换由 service 在子件状态变更时**自动维护**，不走显式端点。

### 5. 错误处理

业务异常用 `raise BizError(code=ErrCode.BIZ_xxx, message=..., http_status=...)`。
**不要**在 service 里 raise `HTTPException`，统一通过 `BizError` 走全局处理器。

### 6. 列表查询约定

`t_part` 列表查询统一走 `PartRepository.list_with_filters(...)`，**不**要散写 ad-hoc 查询。详见 `docs/db-design-part-customer.md` 第 4.5 节。

### 7. API 风格约定

**只使用 `GET` 和 `POST` 两种 HTTP 方法**：

| 方法 | 用途 | 参数位置 | 例子 |
|---|---|---|---|
| `GET` | 查询（无副作用） | URL query string + path | `GET /api/v1/parts?status=PENDING` |
| `POST` | 创建 / 状态变更 / 登录登出 / 任何需要 body 的请求 | URL path 表达动作 + JSON body | `POST /api/v1/parts`、`POST /api/v1/parts/{id}/cancel` |

约定：
- **不**使用 `PUT` / `PATCH` / `DELETE`。
- 路径用动词承载语义：`change-status` / `cancel` / `release` / `pick-up` / `return` / `inspect` 等。
- `WebSocket` 不受此约束（不是 HTTP 方法）。

### 8. COS 文件上传（后端模式）

文件 IO 全部走后端，**不**走前端直传 / STS 临时密钥：

- `core/cos.py` 用 `cos-python-sdk-v5`，单进程共用一个 `CosS3Client`。
- 阻塞调用用 `asyncio.to_thread` 包到默认 executor。
- 密钥来自 `.env` 的 `COS_SECRET_ID` / `COS_SECRET_KEY`。
- 前端预览/下载走 `GET /api/v1/drawings/{file_id}/download-url`，后端用 `get_object_url` 签 GET 临时 URL（默认 900s）。
- 上传走 multipart（`POST /v1/parts/{id}/files`、`POST /v1/assemblies/{id}/files`）。
- 任何新增文件类型只改 `core/cos.py` 加方法，**不要**引入 presign PUT / STS 代码。

### 9. 状态机约定

Part 和 Assembly 的状态转换由 `python-statemachine` (`StateChart`) 管理。

**位置**: `statemachines/part.py`（PartStateMachine）、`statemachines/assembly.py`（AssemblyStateMachine）。

**集成方式**: ORM 模型通过 `sm` property 创建状态机实例，自动从 `model.status` + `model.location` 恢复当前状态。

**Part 状态（扁平 10 态）**:
```
PENDING → ON_SHELF ⇄ WITH_WORKER → INSPECTION → READY_TO_SHIP → DELIVERED → COMPLETED
   │            ↓
   ├──→ PROGRAMMING → ON_SHELF  （CNC 编程：发送至编程 → 编程员下发到生产货架）
   │
   ↓
REPAIRING → ON_SHELF
   ↑
INSPECTION / READY_TO_SHIP / DELIVERED → REPAIRING
任意非终态 → CANCELLED
```

- ON_SHELF / WITH_WORKER 映射到 DB `status="IN_PROCESS"`，通过 `location` 字段（`PRODUCTION_SHELF` / `WORKER`）区分。
- PROGRAMMING 映射到 DB `status="PROGRAMMING"` + `location="OFFICE"`（编程员持有，**不**占货架）。
- 终态: COMPLETED、CANCELLED。

**Assembly 状态（4 态）**: `PENDING → IN_PROCESS → COMPLETED`，可从 PENDING/IN_PROCESS → CANCELLED。

**回调与副作用**: PartEvent 创建、流水号释放（COMPLETED/CANCELLED）、看板广播均在状态机回调中执行。回调通过 `send()` 的 `**kwargs` 接收依赖（`event_repo`、`shelf`、`worker` 等）。

**Validator**: 需要 DB 访问的校验（货架存在性、区域、工人有效性）在 service 层调用 `sm.send()` 之前执行。状态机内部不包含 DB 访问。

**取消操作**: 
- Part cancel: `POST /parts/{id}/cancel`
- Assembly cancel: `POST /assemblies/{id}/cancel`（级联取消所有非终态子件）

## 前端架构

```
frontend/src/
├── api/           # API 调用层（axios），一个模块一个文件
├── components/    # 通用组件（FileListCard, PdfViewer, NotificationBanner）
├── composables/   # 组合式函数（扫码枪、扫码队列、worker 缓存）
├── layouts/       # MainLayout.vue（主布局：侧边栏 + 顶栏 + 内容区）
├── router/        # Vue Router 4 配置
├── types/         # TypeScript 类型定义
└── views/         # 页面视图
    ├── Dashboard.vue
    ├── WorkerList.vue
    ├── assemblies/   # AssemblyList, AssemblyCreate, AssemblyDetail
    ├── parts/        # PartsList, PartBatchNew, PartDetail
    └── scan/         # 扫码台：ScanBadgeGate → ScanActionPicker → ScanPartsWork
```

### 路由一览

| 路径 | 名称 | 说明 |
|------|------|------|
| `/dashboard` | Dashboard | 首页仪表盘 |
| `/cnc/pending` | PendingProgrammingList | 待编程一览（CNC 编程员） |
| `/parts` | PartsList | 零件一览 |
| `/parts/new` | PartsNew | 批量新建零件 |
| `/parts/:id` | PartsDetail | 零件详情 |
| `/assemblies` | AssemblyList | 装配件一览 |
| `/assemblies/new` | AssemblyCreate | 新建装配件 |
| `/assemblies/:id` | AssemblyDetail | 装配件详情 |
| `/workers` | WorkerList | 工人一览 |
| `/scan/badge` | ScanBadge | 扫码台·工牌识别 |
| `/scan/action` | ScanAction | 扫码台·操作选择 |
| `/scan/parts` | ScanParts | 扫码台·扫码报工 |

### 前端约定
- 所有 ID 在 TypeScript 中类型为 `string`（配合后端的 `IdStr` 序列化）。
- API 函数参数 `id` 统一为 `string` 类型（`customer_id` 等查询参数除外，调用的地方显式转）。
- 扫码台脱离 MainLayout，整页占屏。

## 常用命令

> 所有命令需要在项目根目录执行，使用 `uv run` 触发虚拟环境。

| 用途 | 命令 |
|---|---|
| 启动 PostgreSQL | `docker compose up -d` |
| 运行后端（开发模式） | `uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000` |
| 应用所有迁移 | `uv run alembic upgrade head` |
| 回滚一步 | `uv run alembic downgrade -1` |
| 新建空迁移 | `uv run alembic revision -m "add_xxx"` |
| 自动生成迁移（需先有 DB） | `uv run alembic revision --autogenerate -m "..."` |
| 跑全部测试 | `uv run pytest` |
| 跑单个测试文件 | `uv run pytest tests/path/test_xxx.py` |
| 跑单个测试用例 | `uv run pytest tests/path/test_xxx.py::test_yyy` |
| 前端开发服 | `cd frontend && npm run dev` |
| 前端构建 | `cd frontend && npm run build` |

## 迁移文件命名

`alembic/versions/` 下的迁移文件使用 **12 位零填充数字** revision id（如 `000000000001_init_schema.py`），不是 hex。

模块顶部写明 `revision` / `down_revision` / `Create Date`，迁移开头用 docstring 说明要点（如"不使用物理外键"）。

当前迁移：
- `000000000001_init_schema.py` — 初始 schema（所有表的 DDL）
- `000000000002_init_seed.py` — 初始种子数据
- `000000000003_cnc_programming.py` — 新增 `t_cnc_program` 表 + seed 待编程一览菜单 + CLERK / CNC_PROGRAMMER 账号

## 已知问题 / 现状注意

1. **model/DB 漂移**：`uv run alembic check` 会报告一些预存的 index/comment 差异（`t_part.serial_no` 的 partial unique index、`t_part_event` / `t_worker` 的 index 和 column comment），这些与任何近期改动无关，不要在处理其他 PR 时混入修复。

2. **`docs/db-design-part-customer.md` 的审计字段描述已过时**：该文档第 6 点说审计字段由 `Base` 统一声明，实际已改为 `AuditMixin`（见约定第 2 条）。以本文件和 `model/audit.py` 为准。

3. **`created_by` / `updated_by` 全为 NULL**：鉴权已就绪（`t_user` / `t_user_role` / JWT / `core.permission`，`UserRole` 含 `MANAGER` / `SHELF_ACCOUNT` / `CLERK` / `INSPECTOR` / `CNC_PROGRAMMER`），但写操作人字段尚未接到 service 层的 `created_by` / `updated_by`，预留后续按 `CurrentUser.id` 自动填。

4. **CNC 编程环节（2026-07-06 接入）**：见 [第 9 节](#9-状态机约定) 的 10 态描述。零件可经「PENDING → PROGRAMMING → ON_SHELF」走到生产；编程员在 `/cnc/pending` 拉单、下载图纸、上传 G 代码（落 `t_cnc_program` 表，COS key 前缀 `drawings/cnc/part/{part_id}/...`），最后调用 `POST /parts/{id}/release-from-programming` 下发。G 代码与图纸走两套独立文件表 + 独立白名单（`cos_allowed_types` 现含 `nc,tap,cnc,mpf,ngc`）。

5. **2026-07-06 一波小调整**（统一记录于此）：
   - `PartOut.next_process_name: str | None` 字段新增（`schema/part.py` + `service/part.py` 批查 `ProcessRepository.list_by_ids`），消除 `PartsList.vue` 进页时再发一次 `GET /processes?limit=200` 的冗余请求。`api/deps.py` 中装配体内部用的 `PartService` 也注入了 `ProcessRepository`，保证子件返回同样带工序名。
   - `Dashboard.vue` 整体重写：上方 2/3 区域是按货架拆分的卡片网格（auto-fit grid，每架最多 10 件，加急整行红底无标签），下方 1/3 是「正在加工」flex-wrap pill 流（serial + `el-avatar`+`UserFilled` 占位 + 工人姓名）。`service/dashboard.py::build_snapshot` 同时按 `is_urgent DESC, planned_delivery_date ASC` 排序 + 每架 slice 10，并补全空货架（用户新增的 active 生产区货架即便没零件也会出现）。
   - `core/dashboard.py` 迁到 `service/dashboard.py`（`api/v1/ws.py` 是唯一引用方）。`service/__init__.py` 加 re-export。`core/` 现在只剩纯横切关注点。
   - `frontend/src/components/PdfViewer.vue` 适配 pdfjs-dist 6.x：`p.render({...})` 加 `canvas` 字段；卸载用 `pdfDoc.cleanup()` 替代已移除的 `pdfDoc.destroy()`。
   - `frontend/src/views/parts/PartBatchNew.vue` 把 `StagedEntry` / `FormState` 的 `customerId` 改为 `string | null`（与 `Customer.id` 是雪花 ID 字符串一致），提交时再 `Number(s.customerId)` 转 int；`findCustomerLabel` 入参 / `onAddConfirm` 的 `=== undefined` 检查同步修正。

6. **`UnitOfWork` 待补**：当前各 service 直接使用 repository，尚未实现统一的 UnitOfWork 模式（但 repository 层已具备独立的 `soft_delete` / `create` / `update` 等原子操作）。

---

## 10. 完整目录树

```
myERP/
├── main.py                        # FastAPI app 入口 + lifespan + 中间件注册
├── pyproject.toml                 # 项目元数据 + 依赖声明（uv 管理）
├── docker-compose.yml             # PostgreSQL 18 容器
│
├── api/
│   ├── __init__.py                # 聚合所有 v1 router → api_router (prefix="/api")
│   ├── deps.py                    # DI 工厂：get_session / get_*_service / get_*_repo
│   └── v1/
│       ├── __init__.py            # 注册所有子 router（含 CNC/文件多 router 注册）
│       ├── part.py                # /parts CRUD + 批量 + 状态流转 + 扫码（21 端点）
│       ├── assembly.py            # /assemblies + 子件反查 + 文件上传（3 router, 8 端点）
│       ├── cnc_program.py         # /parts/{id}/cnc-programs + /cnc-programs（5 端点）
│       ├── drawing.py             # /parts/{id}/files + /drawings/{id}/...（5 端点）
│       ├── customer.py            # /customers 列表（1 端点）
│       ├── worker.py              # /workers CRUD + 工牌验证（8 端点）
│       ├── auth.py                # /auth login/me/logout（3 端点）
│       ├── user.py                # /users CRUD + 角色管理（8 端点）
│       ├── shelf.py               # /shelves CRUD（5 端点）
│       ├── process.py             # /processes CRUD（5 端点）
│       ├── work_type.py           # /work-types CRUD + 工序映射（7 端点）
│       └── ws.py                  # WebSocket /ws/dashboard + ConnectionManager + 推送循环
│
├── service/
│   ├── __init__.py                # 重导出所有 service + build_menu_tree + build_snapshot_with_workers
│   ├── part.py                    # PartService：零件 CRUD + 全状态流转 + 扫码逻辑
│   ├── assembly.py                # AssemblyService：装配体创建/查询/取消/级联软删
│   ├── drawing.py                 # DrawingService：文件上传/下载/列表/软删 + COS
│   ├── cnc_program.py             # CncProgramService：G 代码上传/下载/软删
│   ├── customer.py                # CustomerService：客户树列表（含 parent_name）
│   ├── worker.py                  # WorkerService：工人 CRUD + 工牌验证 + 启停用
│   ├── auth.py                    # AuthService：登录 + JWT 签发 + /me
│   ├── user.py                    # UserService：账号 CRUD + 角色分配/移除
│   ├── shelf.py                   # ShelfService：货架 CRUD（含 account_count）
│   ├── process.py                 # ProcessService：工序 CRUD + 引用防删
│   ├── work_type.py               # WorkTypeService：工种 CRUD + 引用防删
│   ├── work_type_process.py       # WorkTypeProcessService：工种-工序映射替换
│   ├── dashboard.py               # build_snapshot_with_workers：大屏聚合查询（from core/ 迁入 2026-07-06）
│   └── menu.py                    # build_menu_tree()：角色→菜单行→递归树
│
├── repository/
│   ├── __init__.py                # 重导出所有 repository
│   ├── part.py                    # PartRepository：多维过滤 + 流水号查/工种取件
│   ├── assembly.py                # AssemblyRepository：装配件过滤查询
│   ├── drawing_file.py            # DrawingFileRepository：按 part/assembly 列文件
│   ├── cnc_program.py             # CncProgramRepository：按 part 列 G 代码
│   ├── customer.py                # CustomerRepository：全量列表 + 按 ID 批量查
│   ├── worker.py                  # WorkerRepository：工牌码定位 + 过滤
│   ├── user.py                    # UserRepository + UserRoleRepository
│   ├── shelf.py                   # ShelfRepository：按 zone/active 过滤
│   ├── process.py                 # ProcessRepository：按 code/category 过滤
│   ├── work_type.py               # WorkTypeRepository：按 code 过滤
│   ├── work_type_process.py       # WorkTypeProcessRepository：映射替换
│   ├── serial_counter.py          # SerialCounterRepository：原子获取/释放流水号
│   ├── part_event.py              # PartEventRepository：追加事件 + 按 part 列
│   └── menu.py                    # MenuRepository：按角色取活跃菜单行
│
├── model/
│   ├── __init__.py                # 重导出所有 ORM + 枚举
│   ├── base.py                    # Base（空的 abstract DeclarativeBase）
│   ├── audit.py                   # AuditMixin（5 审计字段）+ EventTimestampMixin
│   ├── enums.py                   # PartStatus, AssemblyStatus, PartEventType, PartLocation,
│   │                              #   UserRole, ShelfZone, PartSortKey, SortDir, ProcessCategory
│   ├── part.py                    # TPart：零件/订单（21 columns + AuditMixin）
│   ├── assembly.py                # TAssembly：装配体（10 columns + AuditMixin）
│   ├── part_event.py              # TPartEvent：生命周期事件（6 columns + EventTimestampMixin）
│   ├── customer.py                # TCustomer：客户树（parent_id 邻接表）
│   ├── worker.py                  # TWorker：工人（badge_code, name, ...）
│   ├── user.py                    # TUser：账号（username, password_hash, ...）
│   ├── user_role.py               # TUserRole：角色绑定（user_id, role, scope_type/id）
│   ├── shelf.py                   # TShelf：货架（code, name, zone, location）
│   ├── process.py                 # TProcess：工序（code unique, category, is_inspection）
│   ├── work_type.py               # TWorkType：工种（code unique, name）
│   ├── work_type_process.py       # TWorkTypeProcess：工种-工序映射（junction）
│   ├── drawing_file.py            # TDrawingFile：图纸文件元数据（XOR part/assembly）
│   ├── cnc_program.py             # TCncProgram：CNC 程序元数据
│   ├── serial_counter.py          # TSerialCounter：流水号计数器（prefix PK）
│   └── menu.py                    # TMenu + TRoleMenu：菜单树 + 角色可见性
│
├── schema/
│   ├── __init__.py                # 空
│   ├── _types.py                  # IdStr / IdStrNonNull：雪花 ID → JSON string 序列化
│   ├── part.py                    # PartOut, PartCreateRequest, PartListQuery, PartScanRequest, ...
│   ├── assembly.py                # AssemblyOut, AssemblyCreateRequest, AssemblyDetail, ...
│   ├── drawing.py                 # DrawingFileOut
│   ├── cnc_program.py             # CncProgramOut
│   ├── customer.py                # CustomerOut
│   ├── worker.py                  # WorkerOut, WorkerCreateRequest, BadgeVerifyRequest, ...
│   ├── user.py                    # UserOut, LoginRequest, LoginResponse, CurrentUserOut, ...
│   ├── shelf.py                   # ShelfOut, ShelfCreateRequest, ...
│   ├── process.py                 # ProcessOut, ProcessCreateRequest, ...
│   ├── work_type.py               # WorkTypeOut, WorkTypeCreateRequest, ...
│   ├── work_type_process.py       # WorkTypeProcessLinkOut, SetWorkTypeProcessRequest
│   └── menu.py                    # MenuNodeOut（递归 children 树）
│
├── core/
│   ├── config.py                  # Settings：.env → Pydantic BaseSettings
│   ├── database.py                # async engine + SessionLocal + get_db + lifespan
│   ├── exception.py               # BizError(code: ErrCode, message, http_status)
│   ├── error_code.py              # ErrCode IntEnum（SUCCESS=0, BIZ_* = 20xxx/40xxx）
│   ├── exception_handler.py       # 全局异常 → 统一响应（BizError/Validation/SQLAlchemy）
│   ├── middleware.py               # UnifiedResponseMiddleware：JSON 响应统一信封
│   ├── response.py                # R[T] 泛型响应模型（code + message + data）
│   ├── cos.py                     # COS 上传/下载/预签/删除（async to_thread 包装）
│   ├── serial.py                  # 客户名→流水号前缀映射（PARENT_TO_CODE / CODE_TO_PARENT）
│   ├── security.py                # hash_password / verify_password / JWT 签发+解码
│   └── permission.py              # CurrentUser @dataclass + require_role/roles/auth/shelf
│
├── statemachines/
│   ├── __init__.py                # 空
│   ├── part.py                    # PartStateMachine：10 态扁平状态机（python-statemachine）
│   └── assembly.py                # AssemblyStateMachine：4 态状态机
│
├── utils/
│   ├── id_gen.py                  # new_id() → int：雪花 ID 单例生成器
│   ├── barcode_gen.py             # 条码图片生成
│   └── gen_barcode_sheet.py       # A4 条码排版打印
│
├── scripts/
│   └── seed_from_excel.py         # Excel → DB 种子数据导入
│
├── tests/
│   ├── conftest.py                # Session 级 postgres-test 容器 + async fixtures + FakeCos
│   ├── test_part_state_machine_events.py   # 状态机事件集成测试
│   ├── test_cnc_programming.py    # CNC 编程端到端测试
│   └── unit/
│       ├── conftest.py            # 跳过 postgres 容器（单元测试用 mock）
│       ├── test_part_service_query_crud.py
│       ├── test_part_service_workflow.py
│       ├── test_assembly_service.py
│       ├── test_drawing_service.py
│       ├── test_customer_service.py
│       ├── test_worker_service.py
│       ├── test_auth_service.py
│       ├── test_user_service.py
│       ├── test_shelf_service.py
│       ├── test_process_service.py
│       ├── test_work_type_service.py
│       └── test_work_type_process_service.py
│
├── alembic/
│   ├── env.py                     # 异步迁移上下文（target_metadata=Base.metadata）
│   └── versions/
│       ├── 000000000001_init_schema.py    # 初始全部 DDL
│       ├── 000000000002_init_seed.py      # 初始种子数据
│       └── 000000000003_cnc_programming.py # t_cnc_program + 种子菜单/账号
│
├── frontend/                      # Vue 3 + Vite + TypeScript + Element Plus（见 §前端架构）
│   └── src/
│       ├── api/                   # 12 个 API 模块（http.ts 为 axios 单例）
│       ├── components/            # Barcode, FileListCard, NotificationBanner, PdfViewer
│       ├── composables/           # useAuthSession, useBarcodeScanner, useScanSession, usePartsScanQueue
│       ├── layouts/               # MainLayout + MenuTreeItem
│       ├── router/                # 16 条路由（含扫码台独立路由）
│       ├── types/                 # 12 个类型定义文件
│       └── views/                 # 17 个页面组件（auth/cnc/parts/assemblies/scan/settings/shelves/users）
│
└── docs/
    ├── db-design-part-customer.md # 数据库设计文档（审计字段描述已过时，以 CLAUDE.md 为准）
    └── example/cos_example.py     # COS SDK 调用示例
```

---

## 11. API 端点速查

> 统一响应信封 `{ code: 0, message: "ok", data: ... }`。
> 权限列用缩写：M=MANAGER, C=CLERK, S=SHELF_ACCOUNT, CNC=CNC_PROGRAMMER, I=INSPECTOR, *=任意已登录。

### /parts（api/v1/part.py）

| 方法 | 路径 | Handler | 权限 | 说明 |
|------|------|---------|------|------|
| GET | /parts | list_parts | M,C | 分页查询，支持 customer_id/statuses/is_urgent/keyword/sort |
| POST | /parts | create_part | M,C | 新增 PENDING 零件，分配流水号 |
| POST | /parts/batch | create_parts_batch | M,C | 批量创建 |
| POST | /parts/{id}/update | update_part | M,C | 编辑基本信息（field-level partial） |
| GET | /parts/pending-programming | list_pending_programming_parts | M,C,CNC | status=PROGRAMMING 的零件一览 |
| GET | /parts/{id} | get_part | M,C,CNC | 零件详情 |
| POST | /parts/{id}/soft-delete | soft_delete_part | M | 软删 |
| POST | /parts/{id}/place-on-shelf | place_part_on_shelf | M,C | PENDING→IN_PROCESS，指定 shelf_id+next_process_id |
| POST | /parts/{id}/send-to-programming | send_part_to_programming | M,C | PENDING→PROGRAMMING |
| POST | /parts/{id}/release-from-programming | release_part_from_programming | M,CNC | PROGRAMMING→IN_PROCESS |
| POST | /parts/{id}/pass-inspection | pass_part_inspection | M,C | INSPECTION→READY_TO_SHIP |
| POST | /parts/{id}/deliver | deliver_part | M,C | READY_TO_SHIP→DELIVERED |
| POST | /parts/{id}/complete | complete_part | M,C | DELIVERED→COMPLETED，释放流水号 |
| POST | /parts/{id}/start-repair | start_part_repair | M,C | →REPAIRING |
| POST | /parts/{id}/complete-repair | complete_part_repair | M,C | REPAIRING→IN_PROCESS，需 query shelf_id |
| POST | /parts/{id}/cancel | cancel_part | M,C | →CANCELLED，释放流水号 |
| GET | /parts/{id}/events | list_part_events | M,C,CNC | 事件历史流 |
| POST | /parts/pick-up | pick_up_part | S@该shelf | 扫码领取（holder: shelf→worker） |
| POST | /parts/scan | scan_part | S@该shelf | 扫码归还/送检（RETURNED/INSPECTED） |
| GET | /parts/by-serial/{serial_no} | get_part_by_serial | * | 按序列号查零件 |
| GET | /parts/by-work-type/{wt_id} | list_pickable_parts_by_work_type | * | PICK_UP 可领件列表（需 query shelf_id） |

### /assemblies（api/v1/assembly.py）— 3 个 router

| 方法 | 路径 | Handler | 权限 | 说明 |
|------|------|---------|------|------|
| GET | /assemblies | list_assemblies | M,C | 分页，支持 customer_id/status/is_urgent/drawing_no_like/name_like |
| POST | /assemblies | create_assembly | M,C | multipart: JSON(data) + PDF(file)，创建装配体+子件+上传 |
| GET | /assemblies/{id} | get_assembly | M,C | 详情：自身+子件+文件 |
| POST | /assemblies/{id}/soft-delete | soft_delete_assembly | M,C | 级联软删装配体+子件+文件 |
| POST | /assemblies/{id}/cancel | cancel_assembly | M,C | 取消装配体，级联取消所有非终态子件 |
| GET | /parts/{id}/assembly | get_assembly_for_child | M,C,CNC | 从子零件反查所属装配件 |
| POST | /assemblies/{id}/files | upload_assembly_file | M,C,CNC | 上传附加文件（STEP/DWG 等） |
| GET | /assemblies/{id}/files | list_assembly_files | M,C,CNC | 列出装配件文件 |

### /customers（api/v1/customer.py）

| GET | /customers | list_customers | M,C,CNC | 全量列表（含 parent_name），不分页 |
|-----|-----------|---------------|---------|-----------------------------------|

### /workers（api/v1/worker.py）

| 方法 | 路径 | Handler | 权限 | 说明 |
|------|------|---------|------|------|
| POST | /workers/verify-badge | verify_badge | * | 扫码台工牌定位（body: badge_code） |
| GET | /workers | list_workers | M | 分页，支持 name_like/is_active |
| POST | /workers | create_worker | M | 新增工人 |
| GET | /workers/{id} | get_worker | M | 工人详情 |
| POST | /workers/{id}/update | update_worker | M | 部分更新 |
| POST | /workers/{id}/deactivate | deactivate_worker | M | 停用（软删+is_active=false） |
| POST | /workers/{id}/reactivate | reactivate_worker | M | 重新启用 |

### /auth（api/v1/auth.py）

| POST | /auth/login | login | 公开 | 用户名+密码→JWT+用户信息+菜单 |
| GET | /auth/me | me | * | 当前账号信息+菜单（用于刷新） |
| POST | /auth/logout | logout | * | no-op（客户端丢弃 token） |

### /users（api/v1/user.py）— MANAGER-only

| 方法 | 路径 | Handler | 说明 |
|------|------|---------|------|
| GET | /users | list_users | 分页，支持 username_like/is_active |
| POST | /users | create_user | 创建账号（默认无角色） |
| GET | /users/{id} | get_user | 账号详情 |
| POST | /users/{id}/update | update_user | 部分更新 |
| POST | /users/{id}/deactivate | deactivate_user | 停用（软删） |
| GET | /users/{id}/roles | list_user_roles | 列出角色 |
| POST | /users/{id}/roles | add_user_role | 添加角色（SHELF_ACCOUNT 需 scope） |
| POST | /users/{id}/roles/{role_id}/remove | remove_user_role | 移除角色 |

### /shelves（api/v1/shelf.py）— MANAGER-only

| GET | /shelves | list_shelves | 分页，支持 zone/is_active |
| POST | /shelves | create_shelf | 创建货架 |
| GET | /shelves/{id} | get_shelf | 货架详情 |
| POST | /shelves/{id}/update | update_shelf | 部分更新 |
| POST | /shelves/{id}/deactivate | deactivate_shelf | 软删（有零件时拒） |

### /processes（api/v1/process.py）— 读 M,C,CNC / 写 M

| GET | /processes | list_processes | 分页，支持 code_like/category |
| POST | /processes | create_process | 新增工序 |
| GET | /processes/{id} | get_process | 工序详情 |
| POST | /processes/{id}/update | update_process | 部分更新（code 不可改） |
| POST | /processes/{id}/soft-delete | soft_delete_process | 软删（被引用时拒） |

### /work-types（api/v1/work_type.py）— 读 M,C,CNC / 写 M

| GET | /work-types | list_work_types | 分页，支持 code_like |
| POST | /work-types | create_work_type | 新增工种 |
| GET | /work-types/{id} | get_work_type | 工种详情 |
| POST | /work-types/{id}/update | update_work_type | 部分更新（code 不可改） |
| POST | /work-types/{id}/soft-delete | soft_delete_work_type | 软删（被引用时拒） |
| GET | /work-types/{id}/processes | list_work_type_processes | 工种当前映射的工序列表 |
| POST | /work-types/{id}/processes | set_work_type_processes | 整体替换工种工序映射 |

### 文件 endpoint（api/v1/drawing.py）— 2 个 router

| 方法 | 路径 | Handler | 权限 | 说明 |
|------|------|---------|------|------|
| POST | /parts/{id}/files | upload_part_file | M,C | 为零件上传附件 |
| GET | /parts/{id}/files | list_part_files | M,C,CNC | 列出零件文件 |
| GET | /drawings/{id}/download-url | get_download_url | M,C,CNC | 签发临时下载 URL |
| GET | /drawings/{id}/content | get_file_content | M,C,CNC | 后端代理预览/下载 |
| POST | /drawings/{id}/delete | delete_file | M,C,CNC | 软删+COS 异步清理 |

### CNC 程序 endpoint（api/v1/cnc_program.py）— 2 个 router

| 方法 | 路径 | Handler | 权限 | 说明 |
|------|------|---------|------|------|
| POST | /parts/{id}/cnc-programs | upload_part_cnc_program | M,CNC | 上传 G 代码 |
| GET | /parts/{id}/cnc-programs | list_part_cnc_programs | M,C,CNC | 列出 G 代码文件 |
| GET | /cnc-programs/{id}/download-url | get_cnc_download_url | M,C,CNC | 签发临时下载 URL |
| GET | /cnc-programs/{id}/content | get_cnc_file_content | M,C,CNC | 后端代理获取内容 |
| POST | /cnc-programs/{id}/delete | delete_cnc_program | M,CNC | 软删+COS 异步清理 |

### WebSocket（api/v1/ws.py）

| 路径 | 说明 |
|------|------|
| /ws/dashboard?token=... | 大屏实时推送：连接即推快照 + 每 5 秒周期推 + 业务事件即时推 |

---

## 12. Service 层速查

> 签名简化格式：`方法名(params) -> ReturnType`，省略 async/self。带 `?` 表示可选参数。

### PartService（service/part.py）
构造：`PartService(parts, customers, workers, events, serial_counters, shelves, processes?, work_types?, work_type_process?, broadcaster?, event_broadcaster?)`

| 方法 | 签名 | 说明 |
|------|------|------|
| list_parts | (query: PartListQuery) -> PartListOut | 多维过滤+分页 |
| get_part | (part_id: int) -> PartOut | 单零件详情（含 holder/customer_path） |
| create_part | (data: PartCreateRequest) -> PartOut | 单条创建+PENDING+CREATED事件 |
| create_parts_batch | (payload: PartBatchCreateRequest) -> PartBatchCreateResult | 批量创建（部分失败不停） |
| update_part | (part_id: int, data: PartUpdateRequest) -> PartOut | 字段级 partial update |
| soft_delete_part | (part_id: int) -> None | 软删 |
| place_on_shelf | (part_id: int, data: PlaceOnShelfRequest) -> PartOut | PENDING→ON_SHELF |
| send_to_programming | (part_id: int) -> PartOut | PENDING→PROGRAMMING |
| release_from_programming | (part_id: int, data: PlaceOnShelfRequest) -> PartOut | PROGRAMMING→ON_SHELF |
| pick_up_by_scan | (data: PartPickUpRequest) -> PartOut | 扫码领取（ON_SHELF→WITH_WORKER） |
| scan_event | (data: PartScanRequest) -> PartOut | 扫码归还/送检（RETURNED→ON_SHELF / INSPECTED→INSPECTION） |
| pass_inspection | (part_id: int) -> PartOut | INSPECTION→READY_TO_SHIP |
| deliver | (part_id: int) -> PartOut | READY_TO_SHIP→DELIVERED |
| complete | (part_id: int) -> PartOut | DELIVERED→COMPLETED，释放流水号 |
| start_repair | (part_id: int) -> PartOut | →REPAIRING |
| complete_repair | (part_id: int, shelf_id: int) -> PartOut | REPAIRING→ON_SHELF |
| cancel | (part_id: int) -> PartOut | →CANCELLED，释放流水号 |
| list_events | (part_id: int) -> list[PartEventOut] | 事件历史（按时间倒序） |
| list_pickable_parts | (work_type_id: int, shelf_id: int) -> list[PartOut] | 扫码台可领件列表 |

### AssemblyService（service/assembly.py）
构造：`AssemblyService(assemblies, parts, files, customers, serial_counters, events, part_service, drawings, event_broadcaster)`

| 方法 | 签名 | 说明 |
|------|------|------|
| list_assemblies | (q: AssemblyListQuery) -> AssemblyListOut | 分页+过滤 |
| create_assembly | (data, *, pdf_bytes, pdf_filename) -> AssemblyCreateResult | 核心：校验→写assembly→上传PDF→写drawing_file→批量创建子零件 |
| get_assembly_detail | (assembly_id: int) -> AssemblyDetail | 自身+子件+文件 |
| get_assembly_for_child | (child_part_id: int) -> AssemblyDetail | 子件反查装配件 |
| cancel_assembly | (assembly_id: int) -> AssemblyDetail | 取消+级联取消所有非终态子件 |
| soft_delete_assembly | (assembly_id: int) -> None | 级联软删+文件异步清理 |

### DrawingService（service/drawing.py）
构造：`DrawingService(files, parts?, assemblies?)`

| upload_to_part | (part_id, *, data, original_filename, content_type, page_index?) -> DrawingFileOut |
| upload_to_assembly | (assembly_id, *, data, original_filename, content_type, page_index?) -> DrawingFileOut |
| list_for_part | (part_id: int) -> list[DrawingFileOut] |
| list_for_assembly | (assembly_id: int) -> list[DrawingFileOut] |
| get_download_url | (file_id: int) -> str |
| get_file_content | (file_id: int) -> tuple[bytes, str, str] |
| delete_file | (file_id: int) -> None | 软删+COS 异步清理 |
| delete_files_silently | (keys: list[str]) -> None | 批量异步清理（不抛错） |

### CncProgramService（service/cnc_program.py）
构造：`CncProgramService(programs, parts?)`

| upload_to_part | (part_id, *, data, original_filename, content_type) -> CncProgramOut |
| list_for_part | (part_id: int) -> list[CncProgramOut] |
| get_download_url | (file_id: int) -> str |
| get_file_content | (file_id: int) -> tuple[bytes, str, str] |
| delete_file | (file_id: int) -> None |

### 其他 Service

| Service | 方法 |
|---------|------|
| **AuthService** | login(data: LoginRequest) -> LoginResponse; me(*, user_id, roles, shelf_ids) -> CurrentUserOut |
| **UserService** | list_users, get_user, create_user, update_user, soft_delete_user, list_user_roles, add_role, remove_role |
| **ShelfService** | list_shelves, get_shelf, create_shelf, update_shelf, soft_delete_shelf |
| **WorkerService** | list_workers, get_worker, verify_badge, create_worker, update_worker, deactivate, reactivate |
| **CustomerService** | list_customers() -> list[CustomerOut] |
| **ProcessService** | list_processes, get_process, create_process, update_process, soft_delete_process |
| **WorkTypeService** | list_work_types, get_work_type, create_work_type, update_work_type, soft_delete_work_type |
| **WorkTypeProcessService** | list_for_work_type, set_for_work_type |
| **build_menu_tree** | (menus: MenuRepository, roles: list[str]) -> list[MenuNodeOut] |

---

## 13. Repository 层速查

> 所有 Repository 继承相同模式：`__init__(session)`, `create`, `get_by_id`, `update`, `soft_delete`。
> 下面只列**非标准查询方法**。

| Repository | 文件 | 特殊方法 |
|------------|------|---------|
| **PartRepository** | repository/part.py | `get_by_serial(serial_no)`; `list_with_filters(customer_id, statuses, is_urgent, keyword, sort_by, sort_dir, limit, offset)` — 核心多维过滤+ILIKE；`count_with_filters(...)`; `list_children(assembly_id)`; `list_for_work_type(shelf_id, mapped_process_ids)` — PICK_UP 热点 |
| **AssemblyRepository** | repository/assembly.py | `list_with_filters(customer_id, status, is_urgent, drawing_no_like, name_like, ...)`; `count_with_filters(...)` |
| **DrawingFileRepository** | repository/drawing_file.py | `list_by_part(part_id)`; `list_by_assembly(assembly_id)`; `list_for_part_ids(ids)`; `get_master_pdf(assembly_id)`; `soft_delete_many(files)` |
| **CncProgramRepository** | repository/cnc_program.py | `list_by_part(part_id)` |
| **CustomerRepository** | repository/customer.py | `list_all()`; `list_by_ids(ids)`; `list_roots()`; `list_children(parent_id)` |
| **WorkerRepository** | repository/worker.py | `get_by_badge_code(code)`; `list_with_filters(name_like, is_active, ...)`; `count_with_filters(...)` |
| **UserRepository** | repository/user.py | `get_by_username(username_lower)` — lower 匹配；`touch_login(user)` — 更新 last_login_at |
| **UserRoleRepository** | repository/user_role.py | `list_by_user(user_id)`; `list_active_shelf_ids_for_user(user_id)` |
| **ShelfRepository** | repository/shelf.py | `get_by_code(code)`; `list_active_by_zone(zone)`; `list_with_filters(zone, is_active, ...)` |
| **ProcessRepository** | repository/process.py | `get_by_code(code)`; `list_with_filters(code_like, category, ...)` |
| **WorkTypeRepository** | repository/work_type.py | `get_by_code(code)`; `list_with_filters(code_like, ...)` |
| **WorkTypeProcessRepository** | repository/work_type_process.py | `list_by_work_type(wt_id)`; `list_by_process(process_id)`; `list_process_ids_by_work_type(wt_id)`; `delete_by_work_type(wt_id)` |
| **SerialCounterRepository** | repository/serial_counter.py | `acquire_serial(prefix: str)` — SELECT...FOR UPDATE 原子递增+冲突检测，释放流水号用 `release_serial` |
| **PartEventRepository** | repository/part_event.py | `add(event)` — 同步写入（状态机回调）；`create(event)` — async 写入；`list_by_part(part_id)` |
| **MenuRepository** | repository/menu.py | `list_active_for_roles(roles: Sequence[str])` — 按角色取可见菜单行 |

---

## 14. Model/ORM 速查

### 核心业务表

| ORM | 表名 | 关键列（非审计/非 ID 列） |
|-----|------|--------------------------|
| **TPart** | t_part | serial_no, name, drawing_no, applicant_name, quantity, unit_price, total_price, request_date, planned_delivery_date, actual_delivery_date, status(PENDING/PROGRAMMING/IN_PROCESS/INSPECTION/READY_TO_SHIP/DELIVERED/REPAIRING/COMPLETED/CANCELLED), location(OFFICE/PRODUCTION_SHELF/WORKER/INSPECTION_SHELF), is_urgent, current_holder_id(多态→shelf/worker), placed_at, customer_id, assembly_id, next_process_id |
| **TAssembly** | t_assembly | drawing_no, name, applicant_name, customer_id, request_date, planned_delivery_date, actual_delivery_date, is_urgent, status(PENDING/IN_PROCESS/COMPLETED/CANCELLED) |
| **TPartEvent** | t_part_event | part_id, worker_id, event_type(CREATED/SENT_TO_PROGRAMMING/CNC_RELEASED/PLACED_ON_SHELF/PICKED_UP/RETURNED/INSPECTED/STATUS_CHANGED/REPAIR_STARTED/REPAIR_COMPLETED/CANCELLED/COMPLETED), from_status, to_status, drawing_code, badge_code, note |
| **TCustomer** | t_customer | name, parent_id(自引用邻接表→t_customer.id) |
| **TWorker** | t_worker | badge_code, name, id_card_no, phone, is_active, work_type_id |
| **TUser** | t_user | username, password_hash, full_name, phone, is_active, last_login_at |
| **TShelf** | t_shelf | code, name, zone(PRODUCTION/INSPECTION), location, is_active |
| **TProcess** | t_process | code(unique), name, category(INHOUSE/OUTSOURCE), is_inspection, sort_order, description |
| **TWorkType** | t_work_type | code(unique), name, description, sort_order |

### 关联/文件表

| ORM | 表名 | 关键列 |
|-----|------|--------|
| **TDrawingFile** | t_drawing_file | part_id(XOR assembly_id), assembly_id(XOR part_id), file_type, object_key(COS key), original_filename, file_size, content_type, page_index, upload_status(READY) |
| **TCncProgram** | t_cnc_program | part_id, file_type, object_key, original_filename, file_size, content_type, upload_status |
| **TWorkTypeProcess** | t_work_type_process | work_type_id, process_id, sort_order |
| **TUserRole** | t_user_role | user_id, role(MANAGER/SHELF_ACCOUNT/CLERK/INSPECTOR/CNC_PROGRAMMER), scope_type, scope_id |
| **TSerialCounter** | t_serial_counter | prefix: str(1) PK, counter: int |
| **TMenu** | t_menu | parent_id, code, title, path, icon, sort_order, is_active |
| **TRoleMenu** | t_role_menu | role, menu_id |

### Mixin 速查

| Mixin | 字段 | 适用 |
|-------|------|------|
| AuditMixin | created_at, created_by, updated_at, updated_by, deleted_at | 业务主表 |
| EventTimestampMixin | created_at（仅此一列） | 事件/日志表 |
| Base | （空 abstract） | 所有 ORM 基类 |

---

## 15. 状态机 + 核心流程

### 15.1 Part 状态机（PartStateMachine — statemachines/part.py）

10 个平坦状态（python-statemachine StateChart），映射到 9 个 DB status 值。
ON_SHELF 和 WITH_WORKER 共享 DB status="IN_PROCESS"，通过 location 列区分。

| 从 | 到 | 触发方法 | DB status | DB location | HTTP 端点 |
|----|----|---------|-----------|-------------|-----------|
| (new) | PENDING | — | PENDING | OFFICE | POST /parts |
| PENDING | ON_SHELF | place_on_shelf | IN_PROCESS | PRODUCTION_SHELF | POST /parts/{id}/place-on-shelf |
| PENDING | PROGRAMMING | send_to_programming | PROGRAMMING | OFFICE | POST /parts/{id}/send-to-programming |
| PROGRAMMING | ON_SHELF | release_from_programming | IN_PROCESS | PRODUCTION_SHELF | POST /parts/{id}/release-from-programming |
| ON_SHELF | WITH_WORKER | pick_up | IN_PROCESS | WORKER | POST /parts/pick-up |
| WITH_WORKER | ON_SHELF | return_to_shelf | IN_PROCESS | PRODUCTION_SHELF | POST /parts/scan（RETURNED） |
| WITH_WORKER | INSPECTION | inspect | INSPECTION | INSPECTION_SHELF | POST /parts/scan（INSPECTED） |
| INSPECTION | READY_TO_SHIP | pass_inspection | READY_TO_SHIP | — | POST /parts/{id}/pass-inspection |
| READY_TO_SHIP | DELIVERED | deliver | DELIVERED | — | POST /parts/{id}/deliver |
| DELIVERED | COMPLETED | complete | COMPLETED | — | POST /parts/{id}/complete |
| INSPECTION/READY_TO_SHIP/DELIVERED | REPAIRING | start_repair | REPAIRING | — | POST /parts/{id}/start-repair |
| REPAIRING | ON_SHELF | complete_repair | IN_PROCESS | PRODUCTION_SHELF | POST /parts/{id}/complete-repair |
| 任意非终态 | CANCELLED | cancel | CANCELLED | — | POST /parts/{id}/cancel |

每次转换都会写入 `t_part_event`（状态机回调 `on_*` 中完成），并通过 `event_broadcaster` 推送 WS 事件。

### 15.2 Assembly 状态机（AssemblyStateMachine — statemachines/assembly.py）

4 态，IN_PROCESS/COMPLETED 由 service 在子件状态变更时自动维护。

| 从 | 到 | 触发条件 | 端点 |
|----|----|---------|------|
| (new) | PENDING | 创建时初始 | POST /assemblies |
| PENDING | IN_PROCESS | 任一子件进入非 PENDING 状态（service 自动） | — |
| IN_PROCESS | COMPLETED | 全部子件均 COMPLETED（service 自动） | — |
| PENDING/IN_PROCESS | CANCELLED | 显式取消（级联所有非终态子件） | POST /assemblies/{id}/cancel |

### 15.3 核心业务流程

**零件生命周期**
1. 文员创建 PENDING 零件（POST /parts，分配流水号）
2a. 放到生产货架（PENDING→IN_PROCESS：place_on_shelf，指定 shelf_id+next_process_id）
2b. 发送至 CNC 编程（PENDING→PROGRAMMING：send_to_programming）→ 编程员上传 G 代码→下发到货架（release_from_programming）
3. 工人扫码领取（pick_up：status 不变，holder: shelf→worker）
4. 工人扫码归还（RETURNED：holder: worker→shelf，更新 next_process_id）或送检（INSPECTED：status→INSPECTION，holder→品检货架）
5. 品检合格（pass_inspection：INSPECTION→READY_TO_SHIP）
6. 发货（deliver：READY_TO_SHIP→DELIVERED）
7. 确认完成（complete：DELIVERED→COMPLETED，释放流水号）
★ 返修：INSPECTION/READY_TO_SHIP/DELIVERED→REPAIRING→ON_SHELF
★ 取消：任意非终态→CANCELLED（释放流水号）

**装配体生命周期**
1. 创建装配体（POST /assemblies，multipart：JSON+PDF）→ 自动创建子零件+分配流水号+上传 PDF→COS+写 drawing_file 行
2. 任一子件进入生产 → Assembly 自动 IN_PROCESS（PartService 状态机回调触发）
3. 全部子件 COMPLETED → Assembly 自动 COMPLETED
4. 取消 → Assembly CANCELLED + 级联取消所有非终态子件

**扫码报工流程**
1. 工人刷工牌（POST /workers/verify-badge）→ 获取工人信息
2. 选择操作：领取(PICK_UP) / 归还(RETURNED) / 送检(INSPECTED)
3a. 领取：选工种→列可领件(GET /parts/by-work-type/{wt_id})→扫零件条码→POST /parts/pick-up
3b. 归还：扫零件条码→选下一道工序→POST /parts/scan（RETURNED）
3c. 送检：扫零件条码→指定品检货架→POST /parts/scan（INSPECTED）

**CNC 编程流程**
1. 文员发送零件到编程（send_to_programming：PENDING→PROGRAMMING）
2. 编程员在 /cnc/pending 查看待编程列表
3. 编程员下载图纸（GET /parts/{id}/files）→ 编写 G 代码
4. 编程员上传 G 代码（POST /parts/{id}/cnc-programs）
5. 编程员下发到货架（release_from_programming：PROGRAMMING→IN_PROCESS）

**文件上传流程**
1. 前端 multipart → 后端读字节流
2. 校验：扩展名白名单+文件大小上限（core/config.py）
3. 生成 COS key：`{prefix}part/{part_id}/{file_id}.{ext}` 或 `{prefix}assembly/{assembly_id}/{file_id}.{ext}` 或 `{prefix}cnc/part/{part_id}/{file_id}.{ext}`
4. 上传 COS（core/cos.py：upload_object，asyncio.to_thread）
5. 写 DB 元数据（t_drawing_file / t_cnc_program，upload_status=READY）
6. 失败→异步清理 COS 孤儿对象（fire-and-forget）
