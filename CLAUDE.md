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

`PartStatus`（9 个状态）和 `AssemblyStatus`（4 个状态）定义在 `model/enums.py`。状态流转规则**不**在 DB 约束中实现，而是在 service 层校验。

Part 状态：`PENDING → READY → IN_PROCESS → INSPECTION → READY_TO_SHIP → DELIVERED → COMPLETED`，另有 `REPAIRING` 分支和 `CANCELLED` 终态。合法跳转矩阵见 `model/enums.py` 的 `PART_TRANSITIONS`。

Assembly 状态：`PENDING → IN_PROCESS → COMPLETED`，可由任意非终态 → `CANCELLED`。Assembly 的 `IN_PROCESS` / `COMPLETED` 切换由 service 在子件状态变更时**自动维护**，不走 `change-status` 端点。

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
| `POST` | 创建 / 状态变更 / 登录登出 / 任何需要 body 的请求 | URL path 表达动作 + JSON body | `POST /api/v1/parts`、`POST /api/v1/parts/{id}/change-status` |

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

## 已知问题 / 现状注意

1. **model/DB 漂移**：`uv run alembic check` 会报告一些预存的 index/comment 差异（`t_part.serial_no` 的 partial unique index、`t_part_event` / `t_worker` 的 index 和 column comment），这些与任何近期改动无关，不要在处理其他 PR 时混入修复。

2. **`docs/db-design-part-customer.md` 的审计字段描述已过时**：该文档第 6 点说审计字段由 `Base` 统一声明，实际已改为 `AuditMixin`（见约定第 2 条）。以本文件和 `model/audit.py` 为准。

3. **`created_by` / `updated_by` 全为 NULL**：当前无鉴权中间件，所有操作人字段留空，预留后续使用。

4. **`UnitOfWork` 待补**：当前各 service 直接使用 repository，尚未实现统一的 UnitOfWork 模式（但 repository 层已具备独立的 `soft_delete` / `create` / `update` 等原子操作）。
