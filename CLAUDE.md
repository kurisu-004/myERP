# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 最近重大重构（2026-09-17 更新）

2026-09-16/17 连续完成 **PR-2（feat/part-slim-down）+ PR-3（feat/batch-step-ify）**：

- **`t_part` 瘦身**（PR-2，对齐 Rust 迁移 027）：删除 `actual_delivery_date` / `location` /
  `current_holder_id` / `placed_at` / `delivery_note_id` / `has_been_repaired` 列；rollup
  物化列（`status` / `next_process_id` 等）保留。状态机 + 批次 rollup 已接管派生字段。
- **`t_part_batch` 工艺链 step 切换**（PR-3，对齐 Rust 迁移 028）：删除
  `next_process_id`（→ t_process.id）/ `placed_at` 列，新增 `current_process_step_id`（→
  t_process_chain_step.id）；同步建 t_process_chain_step 表。
- **`t_assembly` 同步瘦身**：删除 `actual_delivery_date` 列。
- **本仓 model 同步**：`model/part.py` / `model/part_batch.py` / `model/assembly.py` 已
  全部删除对应列并补 2026-09-16 注释。`MCP` 输出契约（`schema/mcp.py`）同步：
  `McpPartDetail` 删 `actual_delivery_date`、`McpBatchItem` 删 `next_process_id` /
  `placed_at` / `has_been_repaired` 并新增 `current_process_step_id` + `next_process_name`
  派生字段，`McpDueRow.location_summary` 改从「最落后」活跃批次派生。
- **dormant 路径处置**（25 个 v1 测试 + `test_part_batch_tree`）：PR-2/3 期间按文件级
  `pytest.skip()` 全部 skip；`service/part.py` 内 v1 dormant 状态机路径未删，加
  `_batch_compat` 兼容助手 + 列已删注释 + try/except pass 兜底，确保仍能 import 与
  unit test mock 不抛 AttributeError。
- **后续若 v1 业务复活**：需按 PR-2/3 commit log 反向恢复列（MCP / service / model
  同步），并配合 backend-rust v2 复活 PR 全栈改造（单独列恢复步骤见 backend-rust 仓
  `docs/architecture.md` 的「v1 复活指引」），不建议在本仓内独立复活 v1 路径。

## 项目概述

myERP —— 零件加工订单管理系统。覆盖法拉电子、路达等一级客户及其下分厂/部门的零件下单、生产跟踪、外协、交付闭环。

- 后端：FastAPI + SQLAlchemy 2.0 异步 + asyncpg + Alembic + Pydantic v2
- 前端：`frontend/`（Vue 3 + Vite + TypeScript + Element Plus）
- 数据库：PostgreSQL 18（`docker-compose.yml` 提供容器）
- 包管理：uv（依赖在 `pyproject.toml` / `uv.lock`）
- 文件存储：腾讯云 COS（`core/cos.py`，后端上传模式）
- ID 方案：`utils/id_gen.py` 生成的雪花 ID
- CI/CD：本地 `./scripts/push-images.sh` → 腾讯云 TCR（`ccr.ccs.tencentyun.com/hsh-erp`） → CVM（`scripts/deploy.sh` 走 `docker compose pull && up -d`）。无 GHCR / GitHub Actions。

## 架构总览

请求从 `api/` 进，按 `api → service → repository → model` 分层流转：

```
api/v1/*.py          # FastAPI 路由（薄层，只做参数提取和响应）
   ↓ Depends
api/deps.py          # get_*_service / get_*_repository 依赖注入
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
- `exception.py` / `error_code.py` / `exception_handler.py` — `BizError` + `ErrCode` 枚举 + 全局异常处理（含 `StaleDataError→409`）
- `middleware.py` / `response.py` — `UnifiedResponseMiddleware` + 统一响应信封
- `cos.py` — 腾讯云 COS 上传/下载/预签/删除（`asyncio.to_thread` 包装）
- `file_hash.py` — SHA-256 计算 + `safe_filename` + `make_object_key`（CAS 命名）
- `_file_kind_policy.py` — `ALLOWED_EXTS_BY_KIND` 文件扩展名白名单
- `serial.py` — 流水号前缀解析（`resolve_root_prefix`；`code_for_parent` 已 deprecated）
- `security.py` — 密码 hash + JWT（access/refresh 双 token）
- `permission.py` — `CurrentUser` + `require_role/roles/auth/shelf`
- `time.py` — `now_naive()` / `now_shanghai_iso()`（Asia/Shanghai，不依赖环境 TZ）

### 当前模块清单

| 聚合 | Model | Repository | Service | API 路由 |
|------|-------|-----------|---------|----------|
| 零件 | `TPart` | `PartRepository` | `part.py` | `api/v1/part.py` |
| 零件事件 | `TPartEvent` | `PartEventRepository` | (状态机回调写) | — |
| 跳序取件事件 | `TPickupSkipEvent` | `PickupSkipEventRepository` | (在 `pick_up_by_scan` 写) | — |
| 客户 | `TCustomer` | `CustomerRepository` | `customer.py` | `api/v1/customer.py` |
| 申请人 | `TApplicant` | `ApplicantRepository` | `applicant.py` | `api/v1/applicant.py` |
| 装配体 | `TAssembly` | `AssemblyRepository` | `assembly.py` | `api/v1/assembly.py` |
| 文件（多态） | `TPartFile` | `PartFileRepository` | `part_file.py` / `drawing.py` / `cnc_program.py` | `api/v1/drawing.py` / `cnc_program.py` |
| CNC 程序 | (走 `TPartFile` kind=G_CODE) | — | `cnc_program.py` | `api/v1/cnc_program.py` |
| 工人 | `TWorker` | `WorkerRepository` | `worker.py` | `api/v1/worker.py` |
| 货架 | `TShelf` | `ShelfRepository` | `shelf.py` | `api/v1/shelf.py` |
| 货架-工序 | `TShelfProcess` | `ShelfProcessRepository` | `shelf_process.py` | (并入 shelf 路由) |
| 工序 | `TProcess` | `ProcessRepository` | `process.py` | `api/v1/process.py` |
| 工种 | `TWorkType` | `WorkTypeRepository` | `work_type.py` | `api/v1/work_type.py` |
| 工种-工序 | `TWorkTypeProcess` | `WorkTypeProcessRepository` | `work_type_process.py` | (并入 work_type 路由) |
| 外协公司 | `TOutsourceCompany` | `OutsourceCompanyRepository` | `outsource_company.py` | `api/v1/outsource_company.py` |
| 外协报价 | `TOutsourceQuote` | `OutsourceQuoteRepository` | `outsource_quote.py` | `api/v1/outsource_quote.py` |
| 送货单 | `TDeliveryNote` / `TDeliveryNoteCounter` / `TDeliveryNoteEvent` | `DeliveryNote{Repository,EventRepository,CounterRepository}` | `delivery_note.py` / `delivery_note_print.py`（XLSX 模板填表） | `api/v1/delivery_note.py` |
| 用户/角色 | `TUser` / `TUserRole` | `UserRepository` / `UserRoleRepository` | `auth.py` / `user.py` | `api/v1/auth.py` / `user.py` |
| 菜单 | `TMenu` / `TRoleMenu` | `MenuRepository` | `menu.py` | (auth 返回) |
| 流水号 | `TSerialCounter` | `SerialCounterRepository` | — | — |
| WebSocket | — | — | `dashboard.py`（快照聚合） | `api/v1/ws.py` |

> **legacy 死表**：`t_drawing_file` / `t_cnc_program` 仍在 `schema_init` 中建表但**无 writer**，已被多态 `t_part_file`（`kind` 字段）取代。对应 `TDrawingFile` / `TCncProgram` ORM 不再使用。

---

## 关键约定（务必遵守）

### 1. 数据库中禁止使用物理外键

所有跨表引用都是普通列 + 普通索引，**不**在 `model/*.py` 写 `ForeignKey(...)`，也**不**在 alembic 迁移里写 `sa.ForeignKey(...)`。

引用完整性、级联删除防悬空、防自环等由 **service 层** 校验：
- 写入前用 repository `get_by_id` 校验目标存在
- 删除聚合根前检查子记录
- `t_customer.parent_id` 写入前在 service 校验不会形成环

需要"防单行自引用"这种纯本地约束用 `CheckConstraint`（如 `parent_id IS NULL OR parent_id <> id`）。

### 2. 审计字段（AuditMixin / EventTimestampMixin）

**`Base` 是空的**（只有 `__abstract__ = True`）。审计字段通过 `model/audit.py` 中的 mixin 按需继承：

| Mixin | 用途 | 字段 |
|-------|------|------|
| `AuditMixin` | 业务主表 | `version`, `created_at`, `created_by`, `updated_at`, `updated_by`, `deleted_at` |
| `EventTimestampMixin` | 事件/日志表 | `created_at`（只有创建时间；append-only 不加 `version`） |

```python
class TPart(Base, AuditMixin): ...          # 业务主表
class TPartEvent(Base, EventTimestampMixin): ...  # 事件表（append-only）
```

约定：
- `version` 是乐观锁列（见 §乐观锁）。列序固定 `created_at → created_by → updated_at → updated_by → deleted_at`。
- **禁止**直接 `session.delete()`；统一走 repository 的 `soft_delete(model)` 写 `deleted_at`。
- 默认查询条件 `deleted_at IS NULL`（repository 自动加）；查全部显式 `include_deleted=True`。
- `created_by` / `updated_by` 由 service 层显式赋值（当前无用户接线时为 NULL）。
- **不要**在子类重复声明这些字段——会与 mixin 冲突。

### 3. ID 生成与 JSON 序列化

**DB 层**：所有业务表统一雪花 ID，列定义 `Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)`。雪花参数从 `.env` 读（`SNOWFLAKE_INSTANCE` / `SNOWFLAKE_SEQ` / `SNOWFLAKE_EPOCH`）。

**JSON 序列化出参**（防 JS 精度丢失）：`schema/_types.py` 定义 `IdStr`（可空）和 `IdStrNonNull`（非空），所有响应 ID 字段用这两个类型序列化为字符串。**仅影响 JSON 序列化**（`when_used="json"`），Python 内部仍是 `int`。

```python
assembly_id: IdStr = None       # 可空外键 → JSON "123..." 或 null
id: IdStrNonNull                # 非空主键 → JSON "123..."
```

**⚠️ 雪花 ID 入参必须用 `str`（请求 body / path / query）**

`IdStr` 只解决出参。入参若用 `int`，前端把字符串 ID 用 `Number()` 转会因 `Number.MAX_SAFE_INTEGER`（≈9.007×10¹⁵）丢精度，后端拿到错误值查不到行（典型报错 `applicant X not found`）。

约束：
- **request body / path / query** 中所有雪花 ID 字段类型必须是 `str`（前端 TS 也是 `string`）。
- service 层统一用 `parse_snowflake_id(value, field_name=...)`（`service/_id_parse.py`）做 str→int 转换；失败抛 `BIZ_INVALID_VALUE` 400。
- 前端**不要** `Number(id)`，直接传字符串。

```python
# ✅ 正确
class PartCreateRequest(BaseModel):
    applicant_id: str | None = Field(default=None)
# ❌ int 会丢精度
    applicant_id: int | None = None
```

### 4. 状态机校验在 service 层

`PartStatus`（10 个 DB 状态）和 `AssemblyStatus`（7 态，2026-08-03 扩展）定义在 `model/enums.py`。状态流转由 `python-statemachine` (`StateChart`) 管理，详见 §状态机约定。DB 访问型校验（货架存在、区域、工人有效）在 service 层于 `sm.send()` 之前执行；状态机内部不含 DB 访问。

### 5. 错误处理

业务异常用 `raise BizError(code=ErrCode.BIZ_xxx, message=..., http_status=...)`。**不要**在 service 里 raise `HTTPException`，统一走全局处理器。

### 6. 列表查询约定

`t_part` 列表查询统一走 `PartRepository.list_with_filters(...)`，**不**散写 ad-hoc 查询。

### 7. API 风格约定

**只用 `GET` 和 `POST`**：

| 方法 | 用途 | 例子 |
|---|---|---|
| `GET` | 查询（无副作用），query + path | `GET /api/v1/parts?status=PENDING` |
| `POST` | 创建 / 状态变更 / 登录 / 任何需 body 的请求 | `POST /api/v1/parts/{id}/cancel` |

- **不**使用 `PUT` / `PATCH` / `DELETE`。路径用动词承载语义（`cancel` / `release` / `pick-up` / `soft-delete` 等）。
- `WebSocket` 不受此约束。

### 8. COS 文件上传（前端直传 STS + 后端 confirm 两段式，2026-09-17 重构）

文件上传采用**前端直传 COS tmp 区 + 后端不再代理 body**的两段式：

1. **前端**先调 `POST /api/v1/files/sts-tmp-keys` 拿 STS 临时凭证（CAM
   policy 限定到 `tmp/{user_id}/{sha16}/*` 单目录，TTL 默认 1800s），用
   `cos-js-sdk-v5`（前端 v5 客户端）直接 PUT 到 `tmp/{uid}/{sha16}/{filename}`。
2. **后端**业务接口（如 `POST /api/v1/parts/{id}/drawings` 之类 v2 直传
   confirm）只接收 object_key + SHA-256，**不再走 multipart 流上传**——
   文件已经在 COS 上，后端只做 `t_part_file` 表 CAS 写入。

**后端服务**：
- `core/cos.py` 仍用 `cos-python-sdk-v5`，但**只用于下载 / 清理 / 后端
  内部写**（如消息附件），不再承担前端上传的中转。
- `core/sts.py`（2026-09-17 新增）用 `qcloud-python-sts` 官方 SDK
  （pip 名 `qcloud-python-sts`，import 路径 `sts.sts.Sts`）现签凭证；
  每次请求通过 `asyncio.to_thread` 调同步 SDK，**不缓存**。
- `service/sts.py` 薄层 service 只做参数整理 + 拼 `tmp_key` + 注入
  endpoint / scheme / 上传前缀；不持 session、不写 DB。
- `api/v1/sts.py` 路由**裸开鉴权**（参考 `/api/mcp/*` 模式，详见 §14），
  靠部署层 nginx / 安全组隔离。

**STS 端口响应（`POST /api/v1/files/sts-tmp-keys`）**：
```json
{
  "tmp_key": "tmp/1/abcdef0123456789/test.pdf",
  "bucket": "myerp-prod-1300000000",
  "region": "ap-guangzhou",
  "endpoint": "https://cos.ap-guangzhou.myqcloud.com",
  "scheme": "https",
  "credentials": {
    "tmp_secret_id": "...",
    "tmp_secret_key": "...",
    "session_token": "...",
    "start_time": 1700000000,
    "expired_time": 1700001800
  },
  "expires_in": 1800,
  "upload_prefix": "drawings/"
}
```

**CAM policy**：
- 资源限定 `tmp/{user_id}/{sha16}/*` 单目录，禁止通配到整个桶。
- 仅允许上传类 7 个动作：`PutObject` / `InitiateMultipartUpload` /
  `ListMultipartUploads` / `ListParts` / `UploadPart` /
  `CompleteMultipartUpload` / `AbortMultipartUpload`。
- 不含 `GetObject`（下载仍走 `core/cos.presigned_get_url` 后端代理）/
  `DeleteObject`（清理走 `core/cos.delete_object`）。

**CAS 命名 + SHA-256 去重**（保持兼容）：
- tmp 路径：`tmp/{user_id}/{sha16}/{safe_filename}`（`sha16` = SHA-256
  前 16 hex；`safe_filename` = ASCII 折叠）。后续业务端点拿到 object_key
  后写 `t_part_file.content_sha256`（CHAR(64)）+ 部分唯一索引
  `(part_id, kind, content_sha256) WHERE deleted_at IS NULL`。
- **跨 part 不共享**：同字节跨 part → 捕获 `IntegrityError` →
  `BIZ_PART_FILE_DUPLICATE 409`。
- 文件格式白名单（`_file_kind_policy.py`）：`DRAWING`=PDF + 9 种图片
  （PNG/JPG/JPEG/GIF/BMP/TIF/TIFF/WEBP/HEIC，图片与 PDF 同槽单文件覆盖）；
  `THREE_D_MODEL`=STEP/STP/IGES/IGS/STL/OBJ/3MF；`CAD_2D`=DWG/DXF；
  `G_CODE`=nc/tap/cnc/mpf/ngc。
- **打印双面 PDF**（`service/printing.py`）：图纸 / 图片正面 + 背面序列号
  大字 + Code128 条码；朝向随图纸同步；无图纸走信息卡占位。

### 9. 状态机约定

Part / Assembly 状态转换由 `python-statemachine` (`StateChart`) 管理。位置 `statemachines/part.py`（`PartStateMachine`）、`statemachines/assembly.py`（`AssemblyStateMachine`）。ORM 通过 `sm` property 从 `model.status` + `model.location` 恢复当前状态。

**Part 状态（11 flat / 10 DB）**：
```
PENDING → ON_SHELF ⇄ WITH_WORKER → INSPECTION → READY_TO_SHIP → DELIVERED → COMPLETED
   │            ↑↓
   ├──→ PROGRAMMING → ON_SHELF          （CNC 编程）
   │
   ├──→ OUTSOURCE → ON_SHELF / INSPECTION（外协；send/receive/inspect_from_outsource）
   │      ↑（可从 PENDING / ON_SHELF / WITH_WORKER 进入）
   ↓
REPAIRING → ON_SHELF
   ↑
INSPECTION / READY_TO_SHIP / DELIVERED → REPAIRING
任意非终态 → CANCELLED
ON_SHELF / PROGRAMMING → PENDING （召回 CLERK/MANAGER）
ON_SHELF → PROGRAMMING （召回 MANAGER/CNC）
```

- `ON_SHELF` / `WITH_WORKER` 都映射 DB `status="IN_PROCESS"`，用 `location`（`PRODUCTION_SHELF` / `WORKER`）区分。
- `PROGRAMMING` → DB `status="PROGRAMMING"` + `location="OFFICE"`（编程员持有，不占货架）。
- `OUTSOURCE` → DB `status="OUTSOURCE"` + `location="OUTSOURCE_COMPANY"` + `current_holder_id = outsource_company.id`。
- 终态：`COMPLETED`、`CANCELLED`。

**Assembly 状态（7 态，2026-08-03 扩展）**：`PENDING → IN_PROCESS → INSPECTION → READY_TO_SHIP → DELIVERED → COMPLETED`，可从任何非终态 → CANCELLED。
- 父件状态 = min(非取消子件进度)（见 `service/_assembly_rollup.py::ASSEMBLY_ROLLUP_TARGET` + 复用 `service/_batch_ops.py::ROLLUP_PROGRESS`）；含 BACKWARD regression（子件 fail_inspection / start_repair 时父件同步回退）。
- 终态：`COMPLETED`（所有非取消子件 COMPLETED）、`CANCELLED`（显式取消）。
- 维护入口：`PartService._check_parent_assembly`（子件流转后）+ `DeliveryNoteService.pickup`（批量配送后，2026-08-03 新增 — 之前绕过）。
- rollup path 走 `AssemblyStateMachine.recompute(target)`（任意方向），显式 cancel 仍走 named transition `cancel`。

**回调与副作用**：PartEvent 创建、流水号释放（COMPLETED/CANCELLED）、看板广播均在状态机回调中执行，通过 `send()` 的 `**kwargs` 接收依赖。回调都带 `created_by: int | None = None` kwarg 写入 `TPartEvent.created_by`。

**取消**：Part `POST /parts/{id}/cancel`；Assembly `POST /assemblies/{id}/cancel`（级联取消所有非终态子件）。

### 10. 一级客户序列号前缀（A-Z）

`t_customer.serial_prefix String(1)`：一级客户必填 A-Z 单字符，叶子客户 NULL 继承父。DB 约束 `serial_prefix ~ '^[A-Z]$'` + 部分唯一索引 `uq_t_customer_root_prefix`（仅未软删根客户）。`t_serial_counter` 预置 A-Z 全 26 行。所有 `acquire_serial(prefix)` 调用点用 `core.serial.resolve_root_prefix(root_customer)`（DB 列优先），**不要**再写 `PARENT_TO_CODE` 硬编码映射。

### 11. 货架 ↔ 工序 映射强制

任何 `(shelf, next_process)` 写入都必须校验 `t_shelf_process` 映射（冗余：后端硬拦 + 前端 reactive 收窄）。
- 后端：`PartService._assert_shelf_maps_process(shelf, process)` → 无映射 / 不含目标 → `BIZ_SHELF_PROCESS_NOT_MAPPED 422`；`shelf_process_repo is None` → 500。触发点：`place_on_shelf` / `release_from_programming` / `receive_from_outsource` / `scan_event RETURNED` / `complete_repair`（校验 carried `next_process_id`，`fail_inspection` 清空时跳过）。
- 前端：`composables/useShelfProcessFilter.ts` 双向 reactive 过滤，一次性消费 `GET /shelves/processes` 批量端点（避免 N+1）。

### 12. 乐观锁（OCC）

所有 `AuditMixin` 表有 `version Integer NOT NULL DEFAULT 0` 列。`model/audit.py::AuditMixin.__init_subclass__` 用 `declared_attr.directive` 注入 `__mapper_args__ = {"version_id_col": cls.version}`——所有继承 AuditMixin 的 ORM 自动获 OCC，**无需改业务代码**。
- SQLAlchemy 每次 dirty UPDATE 自动加 `WHERE id=? AND version=?` 并 `SET version=version+1`；0 行更新 → `StaleDataError` → 全局 handler → HTTP 409 + `BIZ_VERSION_CONFLICT`「该记录已被其他用户修改，请刷新后重试」。
- `version_id_generator` 默认 True：Python 端同步 `version += 1`，flush 后**不 expire / refresh** → 访问 `version` 自身不重现 MissingGreenlet。**仅覆盖 `version_id_col`，不防 `updated_at` / `deleted_at` 等其他 server-side 列的 expire**。
- `model/audit.py` 用 `@event.listens_for(DeclarativeBase, "init")` 兜底：构造时未传 `version` 自动填 0（`mapped_column(default=)` 只影响 INSERT SQL，不填实例）。
- 响应 schema 加 `version: int`（前端 TS 同步；V1 暂不消费）。service `_to_out` 等组装方法显式传 `version=row.version`。
- **不变量**：`t_user.refresh_token_version`（refresh 轮转）与行 `version`（OCC）是不同语义，两个字段并存。`TPartEvent`（append-only）不加 version。

---

## 13. 部分数量批次化（2026-07-29 引入）

核心思想：**所有数量永远活在批次里**（`t_part_batch.quantity`），`t_part.quantity` 是工单总容量，向后兼容；批次即可拆可合，工单 `status / location / holder / next_process_id` 由「最落后」的活跃批次 rollup 派生。

### 模型与不变量
- **t_part_batch**（新表，`AuditMixin`）：`id, part_id, batch_no（per-part 递增）, quantity, status, location, current_holder_id, next_process_id, placed_at, delivery_note_id, parent_batch_id（拆分谱系）`；索引：`(part_id)`, `(status, current_holder_id)`, `(location, status, next_process_id)`, `unique(part_id, batch_no)`；**删除物理禁用**，取消走 CANCELLED 终态。
- **t_part_event** 新增：`batch_id`（事件归属批次；工单级事件 NULL）、`quantity`（本次数量）。
- **不变量**：`Σ(未删除批次.quantity) = t_part.quantity`，service 层强制（`_split_batch` 取锁重校验、`_rollup_part_status` 自动维护）。

### 状态机鸭子复用
`PartStateMachine` 通过 `model.status / location` 字段恢复起始状态，进入态 `on_enter_*` 直接写 `self.model` 属性。`TPartBatch` 与 `TPart` 对该 SM 暴露同名字段，`sm` property 直接复用，零改 SM。所有事件回调经 `_add_event` 统一从 `model` 反射 `part_id / batch_id / quantity`（批次 `part_id = self.model.part_id`、`batch_id = self.model.id`）。批次无 `serial_no` 列，service 在解析/拆出批次时写入 transient 属性 `model._part_serial = part.serial_no`，SM 的 `_serial_of(model)` 优先读它，否则回退 `model.serial_no`（兼工单级事件）。

### 父子批次生命周期
- **创建工单**：自动生成 `batch_no=1` 的根批次（quantity = 工单总量，镜像工单当前 status / location / holder）。
- **拆分**（`_split_batch` in `service/_batch_ops.py`）：`get_for_update`（`FOR UPDATE + populate_existing`）锁源批次行；锁内重校验数量边界；`MAX(batch_no)+1` 取新号；新批次**继承**源批次的 status / location / current_holder_id / next_process_id / placed_at，**不继承** `delivery_note_id`（跟单量留在源批）；同步刷源 batch 的 `version`；写 SPLIT 事件（挂在新批次上，`quantity=拆出量`）。
- **合并**：v1 未实现（v2 候选）；同架同工序的两个批次需经 service 手动合并。

### 工单 rollup（核心简化点）
- `_after_batch_transition` 是所有流转端点的统一收尾：**先 `refresh_for_state_machine(part, attrs=(status/location/...))` 刷新派生字段**（防 MissingGreenlet，跟 keepwith CLAUDE.md §12 同源）→ `_rollup_part_status`：
  - 有活跃批次 → `part.status / location / holder / next_process_id` = `min(active, key=(ROLLUP_PROGRESS[status], batch_no))`（`ROLLUP_PROGRESS = {PENDING:0, PROGRAMMING:1, IN_PROCESS:2, REPAIRING:2, OUTSOURCE:3, INSPECTION:4, READY_TO_SHIP:5, DELIVERED:6}`）。
  - 全部终态 → 全部 `CANCELLED` ⇒ `CANCELLED`，否则 `COMPLETED`；`part.serial_no = None`（回池），写 `batch_id=NULL` 的工单级终态事件。
  - 全部活跃批次 `DELIVERED` 且 `actual_delivery_date IS NULL` → 写当天（送货单 pickup / auto-complete 共用）。

### 通用拆分原语
现有所有流转端点接受可选 `batch_id`（显式目标）+ `quantity`（部分量）参数。Service 调用 `_maybe_split(part, batch, quantity)`：
- `quantity is None or == batch.quantity` → 不拆，直接拿批次流转；
- 否则 `_split_batch(...)` 后取新批次。

`pick_up_by_scan` / `scan_event` 用专用 `_resolve_scan_batch`：先按 `holder_id` 收窄到唯一者，0 命中抛 `BIZ_INVALID_TRANSITION`，>1 命中要求指定 `batch_id`，**保持旧错误码语义**（`BIZ_AUTH_SHELF_MISMATCH` 等）。

### 错误码新增
`BIZ_PART_BATCH_NOT_FOUND` / `BIZ_PART_BATCH_INVALID_QUANTITY` / `BIZ_PART_QUANTITY_LOCKED`（已拆分禁止改工单总量）；`t_part.delivery_note_id` 列为兼容保留，**新写入仅写 `t_part_batch.delivery_note_id`**；`_to_out` 派生送货单字段时优先取 part 列，回退到活跃批次的 `delivery_note_id`。

### 集成测试
`tests/test_part_batch.py` 覆盖：拆分守恒 / 边界 / 终态保护、部分领取 / 归还 / 品检通过 / 打回 / 发货全链路、rollup 终态 + serial 释放、cancel 级联 / 单批次、update_part 总量保护、送货单部分量入单（自动拆）+ 批次送货、多批次必须指定 batch_id。

---

## SQLAlchemy 异步陷阱（务必牢记）

### MissingGreenlet

项目用 SQLAlchemy 2.0 异步 + asyncpg，所有 DB 操作必须在 `async` 函数里 `await`。任何**同步函数**（Pydantic 校验器 / `from_attributes=True` 反序列化 / `@property` / `__repr__`）都不能触发 `await` 或访问需 DB IO 的字段，否则抛：
```
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; ...
```

常见诱因与规避：
- **`from_attributes=True` 反序列化 ORM**：会访问未加载的列触发 lazy load。**正确做法**：始终用关键字传参（`AssemblyOut(id=asm.id, ...)`），不要 `from_attributes`。
- **访问 `lazy="raise"` 关系**（如 `TPart.customer`）：不要 `part.customer.name`，显式 `await self.customers.get_by_id(part.customer_id)`。
- **在 `field_validator` / `field_serializer` 中访问 ORM 字段**：校验器跑在同步上下文，只做值变换，不查 DB。
- **`await self.session.flush()` 之后才能读 server_default 列**。
- **`onupdate=func.now()` 列被 UPDATE 触碰后访问**：`AuditMixin.updated_at` 是 `onupdate=func.now()`；「先 INSERT 再 UPDATE 同一行」的两阶段写会把 `updated_at` 标 expired，async session 里 lazy refresh 触发同步 IO → MissingGreenlet（可能延后到属性访问才爆）。`expire_on_commit=False` 不防这种 expire。

**约束**：任何带 `AuditMixin` 的 ORM **不走「先 INSERT 再 UPDATE 同一行」的两阶段写**。某列后续才知道，就先在 Python 侧解析完再构造对象一次性 INSERT（如 `serial_no`：先 `acquire_serial` 再构造）。

> OCC（`version_id_generator=True`）只保护 `version_id_col` 本身（`version`）不 expire；`updated_at`（`onupdate=func.now()`）等 server-side 列被 UPDATE 触碰后仍会标 expired，sync-read 仍会触发 implicit SELECT → MissingGreenlet。规避：在 sync-read 前显式 `await session.refresh(obj, attribute_names=("updated_at", ...))`，参见 `service/_session_refresh.py::refresh_for_state_machine`（已有 `PartService.pass_inspection` / `service/outsource_quote.py` 等调用点）。**反例**：2026-07-22 `AssemblyService.cancel_assembly` 因 sync 读 `asm.updated_at` 抛 MissingGreenlet，后由 commit 修复。

---

## 前端架构

```
frontend/src/
├── api/           # API 调用层（http.ts 为 axios 单例 + 拦截器）
├── components/    # 通用组件（FileListCard, PdfViewer, Barcode, NotificationBanner）
├── composables/   # useAuthSession / useBarcodeScanner / useScanSession /
│                  #   usePartsScanQueue / useCustomerTree / useApplicantSearch / useShelfProcessFilter
├── layouts/       # MainLayout.vue（侧边栏 + 顶栏 + 内容区）+ MenuTreeItem
├── router/        # Vue Router 4
├── types/         # TypeScript 类型
└── views/
    ├── Dashboard.vue / WorkerList.vue
    ├── auth/          # Login
    ├── applicants/    # ApplicantList
    ├── customers/     # CustomerList
    ├── assemblies/    # AssemblyList / AssemblyCreate / AssemblyDetail
    ├── parts/         # PartsList / PartBatchNew / PartBidImport / PartDetail
    ├── cnc/           # PendingProgrammingList
    ├── inspection/    # InspectionPending
    ├── outsource/     # OutsourceList / OutsourceQuoteList / OutsourceSendReceive
    ├── delivery/      # DeliveryNoteNew
    ├── shelves/       # ShelfList
    ├── settings/      # ProcessList / WorkTypeList / WorkTypeProcess
    ├── users/         # UserList
    └── scan/          # ScanBadgeGate → ScanActionPicker → ScanPickParts / ScanReturnParts / ScanInspectParts / ScanDeliver
```

### 前端约定
- 所有 ID 在 TS 中类型为 `string`（配合后端 `IdStr` 序列化）；API 参数 `id` 统一 `string`。
- 扫码台脱离 MainLayout，整页占屏。
- 加急行整行红底 `#fde2e2`（与 Dashboard 同款，`row-class-name="row-urgent"`），区别于表单橙底 `#fdf6ec`。
- 申请人补全用 `el-autocomplete`（`useApplicantSearch::querySearch` 纯内存过滤缓存的申请人，`:debounce="0"`，不发网络请求）。

### Element Plus 工作流
- `package.json` 声明 `^2.7.0`；实际版本以 `cd frontend && npm ls element-plus` 为准（当前 2.14.x），文档基准 2.14.1。
- **任何对 `el-*` 组件、`@element-plus/icons-vue` 图标、`ElMessage`/`ElMessageBox`/`ElNotification`/`ElLoading` 命令式 API、或 `main.ts` 中 `app.use(ElementPlus, ...)` 与 locale 的修改，必须先调用 `element-plus` skill 并 WebFetch 对应组件官方文档，回复中附 `> Source: https://element-plus.org/...` 一行；不得凭记忆写 props / events / slots。**
- skill 的 `references/` 是 curated 高频子集；未命中时按 skill 内回退路径去官网 WebFetch / WebSearch。
- 项目用 full import（`main.ts` 中 `app.use(ElementPlus, { locale: zhCn })` + 图标全局注册循环），不引入 `unplugin-auto-import`。

---

## 常用命令

> 根目录执行，`uv run` 触发虚拟环境。

| 用途 | 命令 |
|---|---|
| 启动 PostgreSQL | `docker compose up -d` |
| 运行后端（开发） | `uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000` |
| 应用所有迁移 | `uv run alembic upgrade head` |
| 回滚一步 | `uv run alembic downgrade -1` |
| 新建空迁移 | `uv run alembic revision -m "add_xxx"` |
| 跑全部测试 | `uv run pytest` |
| 跑单个测试 | `uv run pytest tests/path/test_xxx.py::test_yyy` |
| 前端开发服 | `cd frontend && npm run dev` |
| 前端构建 | `cd frontend && npm run build` |

---

## Alembic 迁移

`alembic/versions/` 下用 **12 位零填充数字** revision id（如 `000000000001_schema_init.py`），不是 hex。模块顶部写明 `revision` / `down_revision` / `Create Date`，docstring 说明要点。

**当前迁移（5 个 schema + 4 个 prod_data 共 9 文件，schema 链 001 → 003 → 005 → 009 → 010，单 head = `000000000031`）**：

| 文件 | revision | down | 内容 |
|------|----------|------|------|
| `schema/000000000001_schema_init.py` | `000000000001` | base | 唯一 schema 基线：一次建全部表 + 索引 + 约束。文件表统一 `t_part_file`（另建 2 张 legacy 死表 `t_drawing_file` / `t_cnc_program`）；`t_customer.id` 用 `autoincrement=False`（全表雪花 ID）；所有 AuditMixin 表带 `version` 列。 |
| `prod_data/000000000002_data_init.py` | `000000000002` | `000000000001` | 唯一数据种子（全部 ON CONFLICT 幂等，无假数据）：工种 / 工序 / 工种↔工序映射 / 真实工人 / 账号（密码 changeme）/ 菜单 + role_menu / `t_serial_counter` A-Z 全 26 行 / 货架↔工序默认映射（每 active PRODUCTION 架映射 5 个 INHOUSE 工序）。 |
| `schema/000000000003_outsource_quote.py` | `000000000003` | `000000000002` | 外协：`t_outsource_company` / `t_outsource_quote` / `t_outsource_quote_event` / `t_outsource_company_process` + Part 外协状态/位置支持。 |
| `schema/000000000005_add_part_order_note.py` | `000000000005` | `000000000003` | `t_part` 加 `order_no` / `system_delivery_date` / `note` 三列。 |
| `prod_data/000000000006_inspector_menus.py` | `000000000006` | `000000000005` | 巡检员 (INSPECTOR) 菜单种子。 |
| `prod_data/000000000007_inspector_dashboard.py` | `000000000007` | `000000000006` | INSPECTOR 看板卡片种子。 |
| `prod_data/000000000008_remove_assemblies_new_menu.py` | `000000000008` | `000000000007` | 删除装配体（assemblies_new）老菜单条目。 |
| `schema/000000000009_delivery_note.py` | `000000000009` | `000000000008` | 送货单：`t_delivery_note` / `t_delivery_note_event` / `t_delivery_note_counter` + `t_part.delivery_note_id`；状态机 DRAFT ↔ SUBMITTED → PICKED_UP → ARCHIVED；菜单 `delivery_notes_manage`。 |
| `schema/000000000010_add_delivery_note_delivery_date.py` | `000000000010` | `000000000009` | `t_delivery_note.delivery_date`（Date NULL；默认 = 创建当天）；DRAFT/SUBMITTED 可改；PICKED_UP/ARCHIVED 后保留打印能力。 |
| `schema/000000000029_worktype_limit_and_pickup_skip.py` | `000000000029` | `000000000028` | `t_work_type.max_held_batches` 列 + `t_pickup_skip_event` 表（12 列 append-only）|
| `prod_data/000000000030_cnc_parts_list_menu.py` | `000000000030` | `000000000029` | 编程员零件一览菜单（`t_role_menu` 幂等授予 CNC_PROGRAMMER）|
| `prod_data/000000000031_delivery_note_menu_inspector.py` | `000000000031` | `000000000030` | INSPECTOR 送货单菜单授权（仅 t_role_menu row 插入；Create Date 2026-08-05，PR-K 早已合并，与本 PR 无关）|

- `alembic.ini`：`version_locations = schema:prod_data`（`recursive_version_locations = true`）。**无 `dev_data/` 目录**。
- `alembic heads` 只返 1 行（`000000000031`）；`alembic upgrade head` 单命令即可（Dockerfile 的 `CMD alembic upgrade head && uvicorn ...`）。
- **2026-09-17 STS 端口 PR**：alembic head 已停在 `000000000031`（`prod_data/000000000031_delivery_note_menu_inspector.py`，Create Date 2026-08-05，仅含 INSPECTOR 角色菜单授权 row 插入；与本 PR 无关——之前 PR-K 已合并）。本 PR **未新增任何 alembic 迁移**。
- 冷启结果：seed 表有数据，业务表（part/customer/assembly/applicant/outsource）为空。
- **新 schema 迁移放 `schema/` 子目录**，revision id 用下一个 12 位数字，`down_revision` 指向当前 head。改 `schema_init` 时验收门：全新库 `upgrade head` 后 `pg_dump --schema-only` 与旧链对比无意外差异。
- **已有库对齐**：确认 schema 等价后 `alembic stamp <head>` 即可；dev 本地假数据另写独立 seed 脚本（不走迁移）。

---

## JWT 双 token 自动刷新

引入 **access + refresh 双 token + 轮转** 解决「操作到一半 token 过期跳登录」。

| Token | TTL | 用途 | 关键 payload |
|---|---|---|---|
| access | dev 720min / prod 2880min | 业务请求携带 | `sub, username, roles, shelf_ids, type="access", iat, exp, iss` |
| refresh | 默认 7 天（`JWT_REFRESH_TOKEN_EXPIRE_MINUTES`） | 仅换新 access；轮转 | `sub, type="refresh", ver=<t_user.refresh_token_version>, ...` |

错误码 `BIZ_AUTH_REFRESH_INVALID = 40103`：refresh 失效 / 类型不匹配 / 版本落后 / 用户停用。

**轮转不变量**（`AuthService.refresh()` 严格顺序）：decode → get_by_id（不存在/软删/停用→40103）→ 比对 `refresh_token_version == payload.ver`（落后→40103）→ 重读 roles/shelf_ids/menus → **`increment_refresh_token_version` 必须先于 `_build_token_pair`**（先让旧 refresh 失效再签新 refresh，否则新 token 带旧 ver 会被自己顶掉）→ 返回。

**前端**（`frontend/src/api/http.ts`）：
- **Reactive**：响应拦截器收 `code === 40102`（access 过期）→ `getOrCreateRefresh()` → 用 `_isRetryAfterRefresh` 重试原请求。
- **Proactive**：每次成功响应检查 `exp - now < 5min` → fire-and-forget 刷新（30s 节流）。
- **Stampede 队列**：模块级 `refreshPromise` 单例，N 个并发 401 只发 1 个 `/auth/refresh`。
- **专用 `refreshClient`**（无拦截器）避免递归；失败 → dispatch `auth:logout` → `router.replace('/login')`。
- 兼容老 access token（无 `type` 字段视为 access）；refresh 端严格校验 `type="refresh"`。

---

## API 端点速查

> 统一信封 `{ code: 0, message: "ok", data: ... }`。权限缩写：M=MANAGER, C=CLERK, S=SHELF_ACCOUNT, CNC=CNC_PROGRAMMER, I=INSPECTOR, *=任意已登录。

### /parts（api/v1/part.py）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | /parts | M,C | 分页：customer_id/statuses/is_urgent/keyword/sort |
| POST | /parts · /parts/batch | M,C | 创建（PENDING，分配流水号）/ 批量 |
| POST | /parts/{id}/update | M,C | 字段级 partial |
| GET | /parts/pending-programming | M,C,CNC | status=PROGRAMMING 一览 |
| GET | /parts/{id} · /parts/{id}/events | M,C,CNC | 详情 / 事件历史 |
| POST | /parts/{id}/soft-delete | M | 软删 |
| POST | /parts/{id}/place-on-shelf | M,C | PENDING→ON_SHELF（shelf_id+next_process_id）|
| POST | /parts/{id}/send-to-programming | M,C | PENDING→PROGRAMMING |
| POST | /parts/{id}/release-from-programming | M,CNC | PROGRAMMING→ON_SHELF |
| POST | /parts/{id}/send-to-outsource | M,C | →OUTSOURCE（外协公司+工序）|
| POST | /parts/{id}/receive-from-outsource | M,C | OUTSOURCE→ON_SHELF |
| POST | /parts/{id}/receive-from-outsource-to-inspection | M,C | OUTSOURCE→INSPECTION |
| POST | /parts/{id}/pass-inspection · /fail-inspection | M,C | INSPECTION→READY_TO_SHIP / →REPAIRING(打回) |
| POST | /parts/{id}/deliver · /scan-deliver | M,C | READY_TO_SHIP→DELIVERED |
| POST | /parts/{id}/complete | M,C | DELIVERED→COMPLETED，释放流水号 |
| POST | /parts/{id}/start-repair · /complete-repair | M,C | 返修流转 |
| POST | /parts/{id}/cancel | M,C | →CANCELLED，释放流水号 |
| POST | /parts/{id}/recall-to-pending | M,C | ON_SHELF/PROGRAMMING→PENDING（仅未领批次）|
| POST | /parts/{id}/recall-to-programming | M,CNC | ON_SHELF→PROGRAMMING |
| POST | /parts/pick-up · /parts/scan | S@该shelf | 扫码领取 / 归还·送检 |
| GET | /parts/by-serial/{serial_no} | * | 按序列号查 |
| GET | /parts/by-work-type/{wt_id} | * | 可领件列表（query shelf_id；另有 all-shelves 变体）|
| GET | /parts/by-worker/{worker_id} | * | 工人当前持有件（RETURN 流程用）|

### /assemblies（api/v1/assembly.py — 3 router）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | /assemblies · /assemblies/{id} | M,C | 分页 / 详情 |
| POST | /assemblies | M,C | multipart JSON+PDF：建装配体+子件+上传 |
| POST | /assemblies/{id}/soft-delete | M | 级联软删（端点级 override）|
| POST | /assemblies/{id}/cancel | M,C | 级联取消非终态子件 |
| GET | /parts/{id}/assembly | M,C,CNC | 子件反查装配件 |
| POST/GET | /assemblies/{id}/files | M,C,CNC | 上传附加文件 / 列文件 |

### 客户 / 申请人

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | /customers | M,C,CNC | 全量树（含 parent_name / serial_prefix），不分页 |
| POST | /customers · /{id}/update · /{id}/soft-delete | M,C | 客户 CRUD（一级必填 serial_prefix；有子/被引用拒删）|
| GET | /applicants · /applicants/search | M,C | 列表 / 前序补全 |
| POST | /applicants · /bulk-get-or-create · /{id}/update · /{id}/soft-delete | M,C | 申请人 CRUD + 批量 get-or-create |

### 外协

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | /outsource-companies · /by-process/{pid} · /{id} | M,C,CNC | 外协公司查询 |
| POST | /outsource-companies · /{id}/update · /{id}/soft-delete · /{id}/processes | M,C | 外协公司 CRUD + 工序映射替换 |
| GET | /outsource-quotes · /approved-for-send · /{id} | M,C | 报价查询 / 可发外协零件 |
| POST | /outsource-quotes · /{id}/update · /{id}/submit · /{id}/soft-delete | M,C | 报价编辑 / 提交 |
| POST | /outsource-quotes/{id}/approve · /{id}/reject | M | 审批（MANAGER-only）|

### 送货单

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | /delivery-notes/generate | M,C | 按客户前缀（F→法拉/L→路达模板）聚合 READY_TO_SHIP 零件导出 Excel（含条码）|

### /statistics（api/v1/statistics.py — MANAGER-only）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | /statistics/overview | M | 生产概览 |
| GET | /statistics/workers | M | 工人贡献度一览 |
| GET | /statistics/workers/{worker_id} | M | 工人详情 |
| GET | /statistics/pickup-skips | M | 跳序取件次数汇总（按工人）|
| GET | /statistics/pickup-skips/{worker_id} | M | 工人跳序明细（分页）|

### /workers（api/v1/worker.py）

| POST | /workers/verify-badge | * | 扫码台工牌定位 |
| GET/POST | /workers · /{id} · /{id}/update · /{id}/deactivate · /{id}/reactivate | M | 工人 CRUD + 启停用 |

### /auth · /users

| POST | /auth/login · /auth/refresh | 公开 | 登录（返双 token + 菜单）/ 刷新 |
| GET/POST | /auth/me · /auth/logout | * | 当前信息 / 登出（no-op）|
| GET/POST | /users · /{id} · /{id}/update · /{id}/deactivate · /{id}/roles(+/{role_id}/remove) | M | 账号 + 角色管理 |

### /shelves（api/v1/shelf.py — picker + read + write）

| GET | /shelves · /shelves/{id} | M,C,CNC | 列表 / 详情（读放开）|
| GET | /shelves/processes | * | **批量货架↔工序映射**（`{items:[{shelf_id, process_ids}]}`，前端 useShelfProcessFilter 用）|
| GET | /shelves/for-return · /for-inspection | * | 扫码台字面子路径（须注册在 `/{shelf_id:int}` 之前）|
| GET | /shelves/{id}/processes | M | 单架映射 |
| POST | /shelves · /{id}/update · /{id}/deactivate · /{id}/processes | M | 货架 CRUD + 映射替换（写 MANAGER-only）|

> ⚠️ `picker_router` 必须在 `read_router` 之前注册，否则 `/{shelf_id:int}` catch-all 会截胡 `/processes` / `/for-return` / `/for-inspection` 字面子路径 → 422。

### /processes · /work-types（读 M,C,CNC,S / 写 M）

| GET/POST | /processes · /{id} · /{id}/update · /{id}/soft-delete | | 工序 CRUD（code 不可改，被引用拒删）|
| GET/POST | /work-types · /{id} · /{id}/update · /{id}/soft-delete | | 工种 CRUD |
| GET/POST | /work-types/{id}/processes | | 工种工序映射（整体替换）|

### 文件（api/v1/drawing.py + cnc_program.py）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | /parts/{id}/drawings · /3d-models · /cad-files | M,C | 上传图纸（PDF+9图片）/ 3D / CAD 源文件 |
| POST | /parts/{id}/cnc-programs · /setup-sheets | M,CNC | 上传 G 代码 / 工艺卡 |
| GET | /parts/{id}/files | M,C,CNC | 列文件（kind 可选过滤）|
| GET | /files/{id}/download-url · /content | M,C,CNC | 签临时 URL / 后端代理内容 |
| POST | /files/{id}/delete | 按 kind 派 | 软删 + COS 异步清理 |
| GET/POST | /cnc-programs/{id}/download-url · /content · /delete | M,C,CNC (删 M,CNC) | G 代码文件级（别名 → /files/{id}）|

### WebSocket

| /ws/dashboard?token=... | 大屏实时推送：连接推快照 + 每 5s 周期 + 业务事件即时推 |

---

## Service 层速查

> 每个 Repository 继承 `create / get_by_id / update / soft_delete` 标准模式。

- **PartService**（`service/part.py`）：list/get/create/batch/update/soft_delete + 全状态流转（place_on_shelf / send_to_programming / release_from_programming / send_to_outsource / receive_from_outsource / pass_inspection / deliver / complete / start_repair / complete_repair / cancel / pick_up_by_scan / scan_event）+ list_events + 可领件/持有件查询。
- **AssemblyService**（`service/assembly.py`）：list / create_assembly（校验→acquire_serial→构造→上传 PDF→写 part_file→批量子件，一次性 INSERT 避免两阶段写）/ get_detail / get_for_child / cancel / soft_delete（级联+文件异步清理）。子件 serial 派生 `{serial}-{i:02d}`（上限 99）。
- **DrawingService / PartFileService / CncProgramService**：文件上传（SHA 去重）/ 列表 / download-url / content / delete。
- **OutsourceCompanyService**：外协公司 CRUD + 工序映射替换。
- **OutsourceQuoteService**：报价 CRUD + 审批生命周期（DRAFT/SUBMITTED/APPROVED/REJECTED/USED）+ 事件 + 可发外协零件。
- **DeliveryNoteService**（`service/delivery_note.py`）：DRAFT ↔ SUBMITTED → PICKED_UP → ARCHIVED 状态机 + 候选零件勾选（INSPECTION + READY_TO_SHIP）+ partial update（送货日期 / 备注）+ 前缀分发 XLSX 打印（走 `delivery_note_print.py::DeliveryNotePrintService`，复用 PR-F 的 `TEMPLATE_CONFIGS` / `CellBinding`，模板文件 `template/delivery_note_fala.xlsx` / `template/delivery_note_luda.xlsx` 与 `core.config::delivery_note_template_by_prefix` 单 head 接入）。错误码 `BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED` / `BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS`（仍在 `core/error_code.py`）。pickup 完成后保留 `t_part.delivery_note_id` 指向归档单（便于 PICKED_UP/ARCHIVED 仍可打印）；add_parts 仅挡 active 单冲突。
- **ShelfProcessService**：列 / 原子替换货架↔工序有序映射。
- **ApplicantService**：申请人 CRUD + search + get_or_create + bulk_get_or_create（挂一级客户；被引用拒删）。
- **auto_complete**（`service/auto_complete.py`）：周期把足够老的 DELIVERED 零件推 COMPLETED（阈值用 `now_naive()`，与 DB `now()` 同源）。
- **AuthService / UserService / ShelfService / WorkerService / CustomerService / ProcessService / WorkTypeService / WorkTypeProcessService**：各自聚合 CRUD。
- **build_menu_tree**（`service/menu.py`）：角色 → 菜单行 → 递归树。
- **build_snapshot_with_workers**（`service/dashboard.py`）：大屏聚合查询（按货架拆分卡片 + 正在加工 pill）。

---

## Repository 层速查（非标准查询方法）

| Repository | 特殊方法 |
|------------|---------|
| PartRepository | `get_by_serial`; `list_with_filters` / `count_with_filters`（核心多维过滤+ILIKE）; `list_children`; `list_for_work_type`; `list_held_by_worker` |
| AssemblyRepository | `list_with_filters` / `count_with_filters` |
| PartFileRepository | `list_by_part`; `find_active_by_part_kind_sha`; `soft_delete_many` |
| CustomerRepository | `list_all` / `list_by_ids` / `list_roots` / `list_children` |
| ApplicantRepository | 按 customer + name 前序查询; 批量 get-or-create 支持 |
| WorkerRepository | `get_by_badge_code`; `list_with_filters` |
| UserRepository | `get_by_username`（lower）; `touch_login`; `increment_refresh_token_version` |
| UserRoleRepository | `list_by_user`; `list_active_shelf_ids_for_user` |
| ShelfRepository | `get_by_code`; `list_active_by_zone`; `list_with_filters` |
| ShelfProcessRepository | `list_process_ids_by_shelf`; `list_all_mappings`; 原子替换 |
| ProcessRepository / WorkTypeRepository | `get_by_code`; `list_with_filters` |
| WorkTypeProcessRepository | `list_by_work_type` / `list_process_ids_by_work_type` / `get_default_process_id_for_work_type` / `delete_by_work_type` |
| OutsourceCompanyRepository / OutsourceCompanyProcessRepository | 公司过滤 + 工序映射 |
| OutsourceQuoteRepository / OutsourceQuoteEventRepository | 报价过滤 + 事件追加 |
| SerialCounterRepository | `acquire_serial(prefix)`（SELECT...FOR UPDATE 原子递增）; `release_serial` |
| PartEventRepository | `add`（状态机回调同步写）; `create`; `list_by_part` |
| MenuRepository | `list_active_for_roles` |

---

## Model/ORM 速查

### 核心业务表

| ORM | 表 | 关键列（非审计/非 ID） |
|-----|----|----|
| TPart | t_part | serial_no, name, drawing_no, applicant_name, quantity, unit_price, total_price, request_date, planned_delivery_date, actual_delivery_date, **order_no, system_delivery_date, note**, status(10 态), location(OFFICE/PRODUCTION_SHELF/WORKER/INSPECTION_SHELF/**OUTSOURCE_COMPANY**), is_urgent, current_holder_id(多态→shelf/worker/**outsource_company**), placed_at, customer_id, assembly_id, next_process_id |
| TAssembly | t_assembly | serial_no, drawing_no, name, applicant_name, customer_id, request_date, planned_delivery_date, actual_delivery_date, is_urgent, status(PENDING/IN_PROCESS/INSPECTION/READY_TO_SHIP/DELIVERED/COMPLETED/CANCELLED) |
| TPartEvent | t_part_event | part_id, worker_id, created_by, event_type（含 `RECALLED`）, from_status, to_status, drawing_code, badge_code, note |
| TCustomer | t_customer | name, parent_id(自引用邻接表), serial_prefix(A-Z) |
| TApplicant | t_applicant | name, customer_id（partial unique `(name, customer_id) WHERE deleted_at IS NULL`）|
| TWorker / TUser | t_worker / t_user | badge_code/name/is_active/work_type_id · username/password_hash/is_active/last_login_at/refresh_token_version |
| TShelf / TProcess / TWorkType | | code/name/zone(PRODUCTION/INSPECTION) · code(unique)/category(INHOUSE/OUTSOURCE) · code(unique)/name · `max_held_batches`（工种可领取上限，NULL=不限）|
| TOutsourceCompany | t_outsource_company | name, contact, phone, address, is_active |
| TOutsourceQuote | t_outsource_quote | part_id, company_id, process_id, price, status(DRAFT/SUBMITTED/APPROVED/REJECTED/USED), reviewed_at/by |

### 关联/文件表

| ORM | 表 | 关键列 |
|-----|----|----|
| TPartFile | t_part_file | polymorphic `part_id`(=t_part.id 或 t_assembly.id); kind(DRAWING/THREE_D_MODEL/G_CODE/SETUP_SHEET/ASSEMBLY_MASTER/CAD_2D); file_type, object_key, original_filename, file_size, content_type, content_sha256(CHAR(64) NULL), upload_status |
| TShelfProcess | t_shelf_process | shelf_id, process_id, sort_order |
| TWorkTypeProcess | t_work_type_process | work_type_id, process_id, sort_order |
| TOutsourceCompanyProcess | t_outsource_company_process | company_id, process_id |
| TOutsourceQuoteEvent | t_outsource_quote_event | quote_id, event_type, note |
| TUserRole | t_user_role | user_id, role(MANAGER/SHELF_ACCOUNT/CLERK/INSPECTOR/CNC_PROGRAMMER), scope_type, scope_id |
| TSerialCounter | t_serial_counter | prefix: str(1) PK, counter |
| TMenu / TRoleMenu | t_menu / t_role_menu | parent_id/code/title/path/icon/sort_order/is_active · role/menu_id |

### enums.py 全枚举

- `PartStatus`: PENDING, PROGRAMMING, IN_PROCESS, INSPECTION, READY_TO_SHIP, DELIVERED, REPAIRING, **OUTSOURCE**, COMPLETED, CANCELLED（10）
- `PartLocation`: OFFICE, PRODUCTION_SHELF, WORKER, INSPECTION_SHELF, **OUTSOURCE_COMPANY**
- `AssemblyStatus`: PENDING, IN_PROCESS, **INSPECTION**, **READY_TO_SHIP**, **DELIVERED**, COMPLETED, CANCELLED（7，2026-08-03 扩展）
- `PartEventType`: CREATED, RELEASED, SENT_TO_PROGRAMMING, CNC_RELEASED, PLACED_ON_SHELF, PICKED_UP, RETURNED, INSPECTED, INSPECTION_FAILED, STATUS_CHANGED, REPAIR_STARTED, REPAIR_COMPLETED, SENT_TO_OUTSOURCE, RECEIVED_FROM_OUTSOURCE, QUOTE_CREATED, QUOTE_APPROVED, CANCELLED, RECALLED, COMPLETED
- `UserRole`: MANAGER, SHELF_ACCOUNT, CLERK, INSPECTOR, CNC_PROGRAMMER
- `ShelfZone`: PRODUCTION, INSPECTION
- `PartSortKey`: PLANNED_DELIVERY_DATE, REQUEST_DATE, CREATED_AT, SERIAL_NO, DRAWING_NO, NAME · `SortDir`: ASC, DESC
- `ProcessCategory`: INHOUSE, OUTSOURCE
- `PartFileKind`: DRAWING, THREE_D_MODEL(值 `3D_MODEL`), G_CODE, SETUP_SHEET, ASSEMBLY_MASTER, CAD_2D
- `OutsourceQuoteStatus`: DRAFT, SUBMITTED, APPROVED, REJECTED, USED · `OutsourceQuoteEventType`: CREATED, EDITED, SUBMITTED, APPROVED, REJECTED, USED · `OutsourceQuoteSortKey`: CREATED_AT, PRICE, REVIEWED_AT
- `SCAN_EVENT_TYPES`（set，非枚举）: PICKED_UP, RETURNED, INSPECTED

### Mixin

| AuditMixin | version, created_at, created_by, updated_at, updated_by, deleted_at | 业务主表 |
| EventTimestampMixin | created_at | 事件/日志表（append-only）|
| Base | （空 abstract）| 所有 ORM 基类 |

---

## 现状与已知问题

1. **model/DB 漂移**：`uv run alembic check` 会报告预存的 index/comment 差异（`t_part.serial_no` partial unique index、`t_part_event` / `t_worker` 的 index/comment），与近期改动无关，不要在处理其他 PR 时混入修复。
2. **`created_by` / `updated_by` 全为 NULL**：鉴权已就绪（`t_user` / `t_user_role` / JWT / `core.permission`），但写操作人字段尚未接到 service 层，预留后续按 `CurrentUser.id` 自动填。
3. **`service/applicant.py::get_or_create` 并发 race（follow-up，未修）**：`IntegrityError` 后没用 `SAVEPOINT` / `begin_nested()`，并发会 `PendingRollbackError`。
4. **`docs/db-design-part-customer.md` 部分描述已过时**（审计字段说由 `Base` 声明，实际是 `AuditMixin`）；以本文件和 `model/audit.py` 为准。
5. **`UnitOfWork` 待补**：各 service 直接用 repository，尚未实现统一 UnitOfWork（repository 层已具备原子 `soft_delete` / `create` / `update`）。
6. **历史时间错位不 backfill**：早期 `deleted_at` / `last_login_at` / `placed_at` 可能有 8h 偏差（`now_naive()` 接入前），单条 SQL 修正即可，不做全量迁移。

---

## 14. v1 业务路由下线 + JWT 完全 Bypass（2026-09-17 STS 端口 PR）

2026-09-17 起，本仓 v1 业务路由整体下线，业务由 backend-rust v2 承接；本仓同时切换为 JWT 完全 Bypass 模式（不依赖 token / 不查 DB）。

### 范围

**保留端点（共 7 个）**：
- `POST /api/v1/auth/login` — 双 token 签发（AuthService 仍用 `core.security.decode_*_token`，本端点本身不调用 `get_current_user`）
- `POST /api/v1/auth/refresh` — refresh 轮转
- `GET  /api/v1/auth/me` — 当前账号（bypass 后固定 default user）
- `POST /api/v1/auth/change-password` — `UserService.change_own_password`
- `POST /api/v1/files/sts-tmp-keys` — **2026-09-17 新增**：前端直传 COS 临时凭证（裸开鉴权，参考 `/api/mcp/*`）
- `POST /api/v1/files/sts-prefix-credentials` — **2026-09-18 新增**：内部端口——供 rust 后端按任意 `tmp/...` 前缀签凭证；policy 走 `core.sts.grant_credentials_for_prefix`，prefix 必须 `tmp/` 开头 + 至少含一个子目录段 + 无 `* ? .. \x00 \\` 字符
- `GET  /api/v1/files/sts-health` — **2026-09-18 新增**：STS 签发自检（healthcheck 探针）——裸开鉴权；每次 uuid4 hex probe prefix `tmp/__sts_healthcheck__/<hex>/probe`（TTL 60s）真实调一次 SDK 签发（不 mock），验证 SDK + 主账号密钥 + CAM policy + 到 sts.tencentcloudapi.com 网络整条链路；返回 `{status: "ok", probe_prefix, expired_at}`；BizError 透传（自带 http_status）让 compose healthcheck 拿到非 2xx 即 fail

**下线路由（18 个，已移至 `_archive/api_v1/` 待评审 git rm）**：
applicant / assembly / cnc_program / customer / delivery_note / drawing /
outsource_company / outsource_quote / outsource_shipment / part / process / shelf /
statistics / user / work_type / worker / ws。

**保留的 model / repository / schema / service 子集**：
- `repository`：`menu / customer / part / part_batch / part_event / part_file /` `assembly / process / worker / shelf / shelf_process / work_type /` `outsource_company / user / serial_counter`（仍被 MCP / auto_complete / 认证引用）
- `service`：`auth / user / menu / mcp_query / part_file / sts / dashboard /` `auto_complete` + `_*.py` 工具 + `printing / delivery_note_print`
- `schema`：`user / menu / mcp / sts / part_file / _types`
- `model/*`（所有 ORM）保留—— alembic / rust v2 仍引用

### JWT Bypass 实现（`core/permission.py`）

- `get_current_user_from_access_token(token)` 函数体替换为直接返回 `_DEFAULT_USER = CurrentUser(id=1, username="default", full_name="Default User", is_active=True, roles=("MANAGER",), shelf_ids=(), shelf_wildcard=True)`，`token` 参数保留但忽略。
- `get_current_user(request)` 同样直接返回 `_DEFAULT_USER`。
- `require_role` / `require_roles` / `require_part_file_role` 内部直接 `return user`。
- `require_shelf_account_from_body` 取 shelf_id 后直接 `return user, shelf_id`（跳过 `can_operate_shelf` 校验——default MANAGER 已对任意 shelf 放行）。
- `require_auth()` 保持 `return get_current_user`。
- `core/security.py::decode_access_token / decode_refresh_token` **保留原逻辑**，AuthService.refresh / auth.login 仍直接调用签发双 token。

### STS 端口裸开鉴权

`/api/v1/files/sts-tmp-keys` 不依赖 `get_current_user` 也不挂 `Depends(require_auth)`；安全性靠 nginx `/api/v1/files/` 不暴露 / 安全组隔离保证。部署层务必确认此路径不被直连到公网（仅 frontend nginx 同源访问）。

### 验证门

`uv run pytest` 当前 269 passed / 662 skipped。v1 业务测试（25 个 + dormant）已统一文件级 `pytestmark = pytest.mark.skip(reason=...)` 跳过，理由为「2026-09-17 v1 业务路由下线 + JWT bypass：业务由 backend-rust v2 承接」。

`tests/conftest.py` 加了 module-name stub + `__getattr__` 代理，让 removed modules collection 不抛 ImportError；运行即被 pytestmark.skip() 拦截。

**重启 v1 业务**需从 git history 还原下列文件 + 恢复 `core/permission.py` 原实现，并补 alembic 030 → 031 迁移的 DB 同步（见上文 §Alembic 迁移）。

### 风险与后续

- 本仓 auto_complete_loop 仍默认开启（`auto_complete_enabled=True`），与 backend-rust v2 后台 `auto_complete.rs` 双跑——若两仓同时连同一 DB 会重复推 COMPLETED。生产 / staging 应在 `.env` 设 `PYTHON_AUTO_COMPLETE_ENABLED=false`（已在 §Alembic 迁移里说明）。
- `created_by` / `updated_by` 全为 NULL 现状不变（item 2）；保留后续按 `settings.sts_default_user_id` 自动填。
- v1 复活指引：见 `_archive/api_v1/` 目录的文件历史 commit log。
