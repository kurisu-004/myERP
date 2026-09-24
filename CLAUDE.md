# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 最近重大重构（2026-09-24 更新）

### 2026-09-24 PR-3：全 dormant 业务代码下线 + CLAUDE.md 同步（chore/delete-dormant-and-cleanup）

本仓 v1 业务路由下线 + IAM 域迁出 + MCP 域下线（2026-09-17 / 09-19 / 09-24
PR-1/2/3）后，再无任何 dormant 业务调用方；2026-09-24 PR-3 把 dormant 业务
代码全量下线。变更范围：

- **删除源码 49 个**：20 个 dormant `model/*.py`（applicant / cnc_program /
  delivery_note_counter / delivery_note_event / drawing_file /
  outsource_company_process / outsource_company / outsource_quote_event /
  outsource_quote / outsource_shipment / part_event / pickup_skip_event /
  process_chain_step / process / serial_counter / shelf_process / shelf /
  work_type_process / work_type / worker）+ 15 个 dormant `repository/*.py`
  （applicant / outsource_company_process / outsource_company /
  outsource_quote_event / outsource_quote / outsource_shipment / part_event /
  pickup_skip_event / process / serial_counter / shelf_process / shelf /
  work_type_process / work_type / worker；其中 7 个已在 PR-1/2 删除）+ 4 个
  dormant `service/_*.py`（_assembly_rollup / _batch_ops /
  _delivery_note_events / _session_refresh）+ `core/security.py`（已无活跃
  消费者）+ `core/permission.py`（bypass 壳已无意义）。
- **删除测试 49 个**：23 个顶层 `tests/test_*.py`（assembly / cnc_programming
  / dashboard_snapshot / delivery_note / delivery_note_print_api / mcp_api /
  outsource_quote_lifecycle / outsource_send_receive_integration /
  outsource_shipment / part_batch / part_batch_order_info /
  part_filter_null_predicates / part_list_assembly_filter /
  part_location_tree / part_recall / part_state_machine_events / pickup_skip
  / repair_receive / statistics / version_concurrency / work_type /
  work_type_limit / worker）+ 25 个 `tests/unit/test_*.py` +
  `tests/unit/_fake_batches.py`。
- **清理 package init**：`model/__init__.py` 仅导出 6 个 ORM（Base / AuditMixin /
  EventTimestampMixin / TPart / TPartBatch / TPartFile / TAssembly / TCustomer /
  TDeliveryNote）+ 全部 enum 常量；`repository/__init__.py` 仅导出 6 个
  Repository（PartRepository / PartBatchRepository / PartFileRepository /
  AssemblyRepository / CustomerRepository / DeliveryNoteRepository）；`service/
  __init__.py` 仅导出 3 个 service（StsService / PrintingServiceFacade /
  DeliveryNotePrintService）+ dormant helper 注释更新。
- **清理 ErrCode**：`core/error_code.py` 删除全部未被活跃 handler / middleware
  / service 消费的枚举值：IAM（`BIZ_USER_*` 除 `BIZ_USER_NOT_FOUND` 默认值 /
  `BIZ_AUTH_*`）/ 外协（`BIZ_OUTSOURCE_*`）/ 装配体（`BIZ_ASSEMBLY_*`）/ 批次
  （`BIZ_PART_BATCH_*`）/ 跳序（`BIZ_PICKUP_SKIP_*`）/ 工人 / 货架 / 工序 /
  工种 / applicant / drawing 旧码 / 送货单过渡码（`BIZ_DELIVERY_NOTE_*` 除
  `BIZ_DELIVERY_NOTE_NOT_FOUND`）。
- **简化 `tests/conftest.py`**：删 `_V1_DORMANT_MODULES` / `_DormantStub` /
  `_DormantPackage` / `_V1_REMOVED_FROM_PACKAGE` / `_install_dormant_stubs()`
  / `_pkg_getattr` / `_make_pkg_getattr` 等 dormant stub 全部（约 280 行）；
  `_BUSINESS_TABLES` 精简到 7 张活跃表（t_part / t_part_batch / t_part_file /
  t_assembly / t_serial_counter / t_customer / t_delivery_note）；
  `_apply_pr3_test_db_patch` 删除 `t_process_chain_step` DDL 段（该表
  model 已删）；保留 `FakeCosClient` / `_FakeGetObjectResponse` /
  `_FakeRawStream` / `db_session` / `clean_db` / `seed_root_batch`（活跃
  打印测试 fixture 消费）。
- **CLAUDE.md 同步**：§14「保留端点」从 3 个扩展到 8 个（3 STS + 4 打印 + 1
  health）；删「下线路由（18 + 4 = 22 个）」段（已无意义）；「当前模块清单」
  表格精简到 3 个聚合；「Service / Repository / Model / API 端点速查」全部
  移除 dormant 业务引用；「验证门」更新到 ~160 passed / 0 skipped（PR-3 后
  dormant 全删，活跃 6 个测试文件全过）。

**保留活跃 service / repository / schema 子集**：
- `repository`：`part / part_batch / part_file / assembly / customer /
  delivery_note`（被 service.printing / service.delivery_note_print 实际调用）
- `service`：`printing / delivery_note_print / sts` + `_id_parse /
  _print_back_page / _print_front_cache`（活跃 helper）
- `schema`：`sts / _types`
- `model`：所有**业务** ORM 保留——alembic / rust v2 仍引用；**IAM** 相关
  ORM（`TUser` / `TUserRole` / `TMenu` / `TRoleMenu`）已删除（PR-2）；
  dormant 业务 ORM（applicant / process / worker / shelf / work_type /
  outsource_company / outsource_quote / part_event / pickup_skip_event /
  serial_counter / drawing_file / cnc_program / delivery_note_event /
  delivery_note_counter / process_chain_step / outsource_shipment /
  outsource_quote_event / outsource_company_process / shelf_process /
  work_type_process）已删除（PR-3）

### 2026-09-19 IAM 域迁出（chore/auth）

auth / user / menu 域全量迁出至 backend-rust v2，本仓仅保留 STS 凭证签发 + MCP
AI 只读入口。变更范围：

- **删除源码 10 个**：`api/v1/auth.py` + `service/{auth,user,menu}.py` +
  `repository/{user,menu}.py` + `model/{user,user_role,menu}.py`。
- **删除测试 5 个**：`tests/test_auth_refresh.py` +
  `tests/test_password_change.py` + `tests/unit/test_{auth_service,user_service,security}.py`。
- **清理 DI**：`api/deps.py` 删 `get_auth_service` / `get_user_service` /
  `get_user_repo` / `get_user_role_repo` / `get_menu_repo` + dashboard WS 死引用；
  `api/v1/__init__.py` 仅注册 `sts.router`。
- **清理 package init**：`service/__init__.py` / `repository/__init__.py` /
  `model/__init__.py` 不再导出 `AuthService` / `UserService` /
  `build_menu_tree` / `UserRepository` / `UserRoleRepository` /
  `MenuRepository` / `TUser` / `TUserRole` / `TMenu` / `TRoleMenu`。
- **错误码**：`ErrCode.BIZ_USER_DUPLICATE_USERNAME (20602)` /
  `ErrCode.BIZ_USER_NO_ROLE (20606)` 随抛点一并删除；其它 `BIZ_USER_*` /
  `BIZ_AUTH_*` 暂留（被 `tests/test_delivery_note_print_api.py` 消费；
  `tests/unit/test_part_service_workflow.py` 仅消费
  `ErrCode.BIZ_AUTH_SHELF_MISMATCH`，不 import core/security）。
- **`core/security.py` 保留**：仅被 `tests/test_delivery_note_print_api.py`
  消费（构造 token 验证鉴权链路）；`tests/unit/test_part_service_workflow.py`
  不 import 本模块，仅作 `BIZ_AUTH_SHELF_MISMATCH` 错误码锚点保留。
  彻底下线需先迁移 `tests/test_delivery_note_print_api.py` 到「mock 不依赖
  真实 JWT 签发 / 解码」的纯 fixture 路径。
- **`core/permission.py` 保留**：bypass 壳依赖 `get_current_user`，
  STS / MCP / 所有 v1 业务都消费；2026-09-17 bypass 实现不动。
- **alembic 链零改动**：基表（`t_user` / `t_user_role` / `t_menu` /
  `t_role_menu`）+ seed 数据保留供 rust v2 直接读写；本仓不持有 ORM 抽象，
  rust v2 通过自己的迁移管理 IAM 表结构。
- **STS 端点零改动**：`POST /api/v1/files/sts-tmp-keys` /
  `POST /api/v1/files/sts-prefix-credentials` /
  `GET /api/v1/files/sts-health` 与 IAM 无关，全部保留。
- **dormant stub 扩列**：`tests/conftest.py` 的 `_V1_DORMANT_MODULES` /
  `_V1_REMOVED_FROM_PACKAGE` 加入 `service.{auth,user,menu}` +
  `repository.{user,menu}` + `model.{user,user_role,menu}` + `api.v1.auth`，
  让历史 v1 测试 collection 不抛 ImportError（运行被 `pytestmark.skip` 拦截）。

验证：`uv run pytest` 从 269 passed / 662 skipped → **202 passed / 662 skipped**（-67
测试随源码删除）；`alembic upgrade head` 仍成功（head `000000000031` 不变）。

### 2026-09-16/17 PR-2 + PR-3（历史）

PR-2（feat/part-slim-down）+ PR-3（feat/batch-step-ify）已完成并归档：

- **`t_part` 瘦身**（PR-2，对齐 Rust 迁移 027）：删除 `actual_delivery_date` /
  `location` / `current_holder_id` / `placed_at` / `delivery_note_id` /
  `has_been_repaired` 列；rollup 物化列（`status` / `next_process_id` 等）保留。
  状态机 + 批次 rollup 已接管派生字段。
- **`t_part_batch` 工艺链 step 切换**（PR-3，对齐 Rust 迁移 028）：删除
  `next_process_id`（→ t_process.id）/ `placed_at` 列，新增
  `current_process_step_id`（→ t_process_chain_step.id）；同步建
  t_process_chain_step 表。
- **`t_assembly` 同步瘦身**：删除 `actual_delivery_date` 列。
- **本仓 model 同步**：`model/part.py` / `model/part_batch.py` /
  `model/assembly.py` 已全部删除对应列并补 2026-09-16 注释。`MCP` 输出契约
  （`schema/mcp.py`）同步：`McpPartDetail` 删 `actual_delivery_date`、
  `McpBatchItem` 删 `next_process_id` / `placed_at` / `has_been_repaired`
  并新增 `current_process_step_id` + `next_process_name` 派生字段，
  `McpDueRow.location_summary` 改从「最落后」活跃批次派生。
- **dormant 路径处置**（25 个 v1 测试 + `test_part_batch_tree`）：PR-2/3 期间按
  文件级 `pytest.skip()` 全部 skip；`service/part.py` 内 v1 dormant 状态机
  路径未删，加 `_batch_compat` 兼容助手 + 列已删注释 + try/except pass 兜底，
  确保仍能 import 与 unit test mock 不抛 AttributeError。

## 项目概述

myERP —— 零件加工订单管理系统。覆盖法拉电子、路达等一级客户及其下分厂/部门的
零件下单、生产跟踪、外协、交付闭环。

- 后端：FastAPI + SQLAlchemy 2.0 异步 + asyncpg + Alembic + Pydantic v2
- 前端：`frontend/`（Vue 3 + Vite + TypeScript + Element Plus）
- 数据库：PostgreSQL 18（`docker-compose.yml` 提供容器）
- 包管理：uv（依赖在 `pyproject.toml` / `uv.lock`）
- 文件存储：腾讯云 COS（`core/cos.py`，后端上传模式）
- ID 方案：`utils/id_gen.py` 生成的雪花 ID
- CI/CD：本地 `./scripts/push-images.sh` → 腾讯云 TCR
  （`ccr.ccs.tencentyun.com/hsh-erp`） → CVM（`scripts/deploy.sh` 走
  `docker compose pull && up -d`）。无 GHCR / GitHub Actions。

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
- `exception.py` / `error_code.py` / `exception_handler.py` — `BizError` +
  `ErrCode` 枚举 + 全局异常处理（含 `StaleDataError→409`）
- `middleware.py` / `response.py` — `UnifiedResponseMiddleware` + 统一响应信封
- `cos.py` — 腾讯云 COS 上传/下载/预签/删除（`asyncio.to_thread` 包装）
- `file_hash.py` — SHA-256 计算 + `safe_filename` + `make_object_key`（CAS 命名）
- `_file_kind_policy.py` — `ALLOWED_EXTS_BY_KIND` 文件扩展名白名单
- `serial.py` — 流水号前缀解析（`resolve_root_prefix`；`code_for_parent` 已 deprecated）
- `security.py` — 密码 hash + JWT 编解码（access/refresh 双 token）
  **历史保留**——2026-09-19 IAM 域迁出后，本仓仍消费 hash_password /
  create_access_token / decode_*_token 的**唯一**真实下游是：
  - `tests/test_delivery_note_print_api.py`（构造 token 验证 GET /print 鉴权链路）
  `tests/unit/test_part_service_workflow.py` **不 import core.security**，
  只断言 `ErrCode.BIZ_AUTH_SHELF_MISMATCH`（仅消费 ErrCode 枚举）；
  `core/permission.py` docstring（历史设计说明，无 import）。
  彻底下线需先迁移 `tests/test_delivery_note_print_api.py` 到 mock-only 路径。
- `permission.py` — `CurrentUser` + `require_role/roles/auth/shelf`（bypass 壳）
- `time.py` — `now_naive()` / `now_shanghai_iso()`（Asia/Shanghai，不依赖环境 TZ）

### 当前模块清单（2026-09-24 PR-3 最终态）

| 聚合 | Model | Repository | Service | API 路由 |
|------|-------|-----------|---------|----------|
| 零件 | `TPart` / `TPartBatch` | `PartRepository` / `PartBatchRepository` | `service/printing.py`（仅 PDF 打印消费） | `api/v1/printing.py`（**保留**：`GET /parts/{id}/print` + `POST /parts/print-batch`） |
| 装配体 | `TAssembly` | `AssemblyRepository` | `service/printing.py` / `service/delivery_note_print.py`（仅读取 customer / 父件） | 同上 |
| 文件（多态） | `TPartFile` | `PartFileRepository` | `service/printing.py`（图纸 / 图片正背面） | 同上 |
| 客户 | `TCustomer` | `CustomerRepository` | `service/printing.py` / `service/delivery_note_print.py`（读 `serial_prefix`） | 同上 |
| 送货单 | `TDeliveryNote` | `DeliveryNoteRepository` | `service/delivery_note_print.py`（XLSX 模板填表） | `api/v1/delivery_note_print.py`（**保留**：`POST /delivery-notes/{id}/print` + `POST /delivery-notes/{id}/print-labels`） |
| STS 凭证 | — | — | `service/sts.py`（薄层） + `core/sts.py`（SDK 包装） | `api/v1/sts.py`（**保留** 3 个端点） |
| 健康检查 | — | — | — | `main.py::/api/v1/health`（**保留**：compose 探针） |

> **2026-09-19 IAM 域迁出**：基表 `t_user` / `t_user_role` / `t_menu` /
> `t_role_menu` 仍由 alembic 管理（保留 seed 供 rust v2 继承），但本仓不再持有
> 任何 ORM / service / 路由。业务读写由 backend-rust v2 的 `/api/v2/iam/*` 承接。
>
> **2026-09-24 PR-3 dormant 下线**：applicant / process / worker / shelf /
> work_type / outsource_company / outsource_quote / part_event /
> pickup_skip_event / serial_counter / drawing_file / cnc_program /
> delivery_note_event / delivery_note_counter / process_chain_step /
> outsource_shipment / outsource_quote_event / outsource_company_process /
> shelf_process / work_type_process 共 20 个 ORM 整体删除；其对应的
> `repository/*.py` 共 15 个删除；`service/_assembly_rollup` /
> `_batch_ops` / `_delivery_note_events` / `_session_refresh` 4 个 dormant
> helper 删除。
>
> **legacy 死表**：`t_drawing_file` / `t_cnc_program` 仍在 `schema_init` 中建表
> 但**无 writer**，已被多态 `t_part_file`（`kind` 字段）取代。对应
> `TDrawingFile` / `TCncProgram` ORM 已删除（2026-09-24 PR-3）。

---

## 关键约定（务必遵守）

### 1. 数据库中禁止使用物理外键

所有跨表引用都是普通列 + 普通索引，**不**在 `model/*.py` 写 `ForeignKey(...)`，
也**不**在 alembic 迁移里写 `sa.ForeignKey(...)`。

引用完整性、级联删除防悬空、防自环等由 **service 层** 校验：
- 写入前用 repository `get_by_id` 校验目标存在
- 删除聚合根前检查子记录
- `t_customer.parent_id` 写入前在 service 校验不会形成环

需要"防单行自引用"这种纯本地约束用 `CheckConstraint`（如 `parent_id IS NULL OR parent_id <> id`）。

### 2. 审计字段（AuditMixin / EventTimestampMixin）

**`Base` 是空的**（只有 `__abstract__ = True`）。审计字段通过 `model/audit.py`
中的 mixin 按需继承：

| Mixin | 用途 | 字段 |
|-------|------|------|
| `AuditMixin` | 业务主表 | `version`, `created_at`, `created_by`, `updated_at`, `updated_by`, `deleted_at` |
| `EventTimestampMixin` | 事件/日志表 | `created_at`（只有创建时间；append-only 不加 `version`） |

```python
class TPart(Base, AuditMixin): ...          # 业务主表
# 历史: class TPartEvent(Base, EventTimestampMixin): ...  # 事件表（append-only）
# 2026-09-24 PR-3 后，TPartEvent / TPartBatchEvent 等事件表 ORM 已删除；
# EventTimestampMixin 保留供未来 v2 / 新增 event 表复用。
```

约定：
- `version` 是乐观锁列（见 §乐观锁）。列序固定
  `created_at → created_by → updated_at → updated_by → deleted_at`。
- **禁止**直接 `session.delete()`；统一走 repository 的 `soft_delete(model)` 写 `deleted_at`。
- 默认查询条件 `deleted_at IS NULL`（repository 自动加）；查全部显式 `include_deleted=True`。
- `created_by` / `updated_by` 由 service 层显式赋值（当前无用户接线时为 NULL）。
- **不要**在子类重复声明这些字段——会与 mixin 冲突。

### 3. ID 生成与 JSON 序列化

**DB 层**：所有业务表统一雪花 ID，列定义
`Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)`。
雪花参数从 `.env` 读（`SNOWFLAKE_INSTANCE` / `SNOWFLAKE_SEQ` / `SNOWFLAKE_EPOCH`）。

**JSON 序列化出参**（防 JS 精度丢失）：`schema/_types.py` 定义 `IdStr`（可空）
和 `IdStrNonNull`（非空），所有响应 ID 字段用这两个类型序列化为字符串。**仅
影响 JSON 序列化**（`when_used="json"`），Python 内部仍是 `int`。

```python
assembly_id: IdStr = None       # 可空外键 → JSON "123..." 或 null
id: IdStrNonNull                # 非空主键 → JSON "123..."
```

**⚠️ 雪花 ID 入参必须用 `str`（请求 body / path / query）**

`IdStr` 只解决出参。入参若用 `int`，前端把字符串 ID 用 `Number()` 转会因
`Number.MAX_SAFE_INTEGER`（≈9.007×10¹⁵）丢精度，后端拿到错误值查不到行（典型
报错 `applicant X not found`）。

约束：
- **request body / path / query** 中所有雪花 ID 字段类型必须是 `str`（前端 TS 也是 `string`）。
- service 层统一用 `parse_snowflake_id(value, field_name=...)`（`service/_id_parse.py`）
  做 str→int 转换；失败抛 `BIZ_INVALID_VALUE` 400。
- 前端**不要** `Number(id)`，直接传字符串。

```python
# ✅ 正确
class PartCreateRequest(BaseModel):
    applicant_id: str | None = Field(default=None)
# ❌ int 会丢精度
    applicant_id: int | None = None
```

### 4. 状态机校验在 service 层

`PartStatus`（10 个 DB 状态）和 `AssemblyStatus`（7 态，2026-08-03 扩展）定义在
`model/enums.py`。状态流转由 `python-statemachine` (`StateChart`) 管理，详见
§状态机约定。DB 访问型校验（货架存在、区域、工人有效）在 service 层于
`sm.send()` 之前执行；状态机内部不含 DB 访问。

### 5. 错误处理

业务异常用 `raise BizError(code=ErrCode.BIZ_xxx, message=..., http_status=...)`。
**不要**在 service 里 raise `HTTPException`，统一走全局处理器。

### 6. 列表查询约定

`t_part` 列表查询统一走 `PartRepository.list_with_filters(...)`，**不**散写
ad-hoc 查询。

### 7. API 风格约定

**只用 `GET` 和 `POST`**：

| 方法 | 用途 | 例子 |
|---|---|---|
| `GET` | 查询（无副作用），query + path | `GET /api/v1/parts?status=PENDING` |
| `POST` | 创建 / 状态变更 / 登录 / 任何需 body 的请求 | `POST /api/v1/parts/{id}/cancel` |

- **不**使用 `PUT` / `PATCH` / `DELETE`。路径用动词承载语义（`cancel` /
  `release` / `pick-up` / `soft-delete` 等）。
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

Part / Assembly 状态转换由 `python-statemachine` (`StateChart`) 管理。位置
`statemachines/part.py`（`PartStateMachine`）、
`statemachines/assembly.py`（`AssemblyStateMachine`）。ORM 通过 `sm` property
从 `model.status` + `model.location` 恢复当前状态。

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

- `ON_SHELF` / `WITH_WORKER` 都映射 DB `status="IN_PROCESS"`，用 `location`
  （`PRODUCTION_SHELF` / `WORKER`）区分。
- `PROGRAMMING` → DB `status="PROGRAMMING"` + `location="OFFICE"`
  （编程员持有，不占货架）。
- `OUTSOURCE` → DB `status="OUTSOURCE"` + `location="OUTSOURCE_COMPANY"` +
  `current_holder_id = outsource_company.id`。
- 终态：`COMPLETED`、`CANCELLED`。

**Assembly 状态（7 态，2026-08-03 扩展）**：
`PENDING → IN_PROCESS → INSPECTION → READY_TO_SHIP → DELIVERED → COMPLETED`，
可从任何非终态 → CANCELLED。
- 父件状态 = min(非取消子件进度)（见
  `service/_assembly_rollup.py::ASSEMBLY_ROLLUP_TARGET` + 复用
  `service/_batch_ops.py::ROLLUP_PROGRESS`）；含 BACKWARD regression
  （子件 fail_inspection / start_repair 时父件同步回退）。
- 终态：`COMPLETED`（所有非取消子件 COMPLETED）、`CANCELLED`（显式取消）。
- 维护入口：`PartService._check_parent_assembly`（子件流转后）+
  `DeliveryNoteService.pickup`（批量配送后，2026-08-03 新增 — 之前绕过）。
- rollup path 走 `AssemblyStateMachine.recompute(target)`（任意方向），
  显式 cancel 仍走 named transition `cancel`。

**回调与副作用**：PartEvent 创建、流水号释放（COMPLETED/CANCELLED）、
看板广播均在状态机回调中执行，通过 `send()` 的 `**kwargs` 接收依赖。
回调都带 `created_by: int | None = None` kwarg 写入 `TPartEvent.created_by`。

**取消**：Part `POST /parts/{id}/cancel`；Assembly `POST /assemblies/{id}/cancel`
（级联取消所有非终态子件）。

### 10. 一级客户序列号前缀（A-Z）

`t_customer.serial_prefix String(1)`：一级客户必填 A-Z 单字符，叶子客户 NULL 继承父。
DB 约束 `serial_prefix ~ '^[A-Z]$'` + 部分唯一索引 `uq_t_customer_root_prefix`
（仅未软删根客户）。`t_serial_counter` 预置 A-Z 全 26 行。所有
`acquire_serial(prefix)` 调用点用 `core.serial.resolve_root_prefix(root_customer)`
（DB 列优先），**不要**再写 `PARENT_TO_CODE` 硬编码映射。

### 11. 货架 ↔ 工序 映射强制

任何 `(shelf, next_process)` 写入都必须校验 `t_shelf_process` 映射（冗余：
后端硬拦 + 前端 reactive 收窄）。
- 后端：`PartService._assert_shelf_maps_process(shelf, process)` → 无映射 /
  不含目标 → `BIZ_SHELF_PROCESS_NOT_MAPPED 422`；`shelf_process_repo is None` → 500。
  触发点：`place_on_shelf` / `release_from_programming` /
  `receive_from_outsource` / `scan_event RETURNED` / `complete_repair`
  （校验 carried `next_process_id`，`fail_inspection` 清空时跳过）。
- 前端：`composables/useShelfProcessFilter.ts` 双向 reactive 过滤，
  一次性消费 `GET /shelves/processes` 批量端点（避免 N+1）。

### 12. 乐观锁（OCC）

所有 `AuditMixin` 表有 `version Integer NOT NULL DEFAULT 0` 列。
`model/audit.py::AuditMixin.__init_subclass__` 用 `declared_attr.directive` 注入
`__mapper_args__ = {"version_id_col": cls.version}`——所有继承 AuditMixin 的
ORM 自动获 OCC，**无需改业务代码**。
- SQLAlchemy 每次 dirty UPDATE 自动加 `WHERE id=? AND version=?` 并
  `SET version=version+1`；0 行更新 → `StaleDataError` → 全局 handler →
  HTTP 409 + `BIZ_VERSION_CONFLICT`
  「该记录已被其他用户修改，请刷新后重试」。
- `version_id_generator` 默认 True：Python 端同步 `version += 1`，flush 后
  **不 expire / refresh** → 访问 `version` 自身不重现 MissingGreenlet。
  **仅覆盖 `version_id_col`，不防 `updated_at` / `deleted_at` 等其他
  server-side 列的 expire**。
- `model/audit.py` 用 `@event.listens_for(DeclarativeBase, "init")` 兜底：
  构造时未传 `version` 自动填 0（`mapped_column(default=)` 只影响 INSERT SQL，
  不填实例）。
- 响应 schema 加 `version: int`（前端 TS 同步；V1 暂不消费）。
  service `_to_out` 等组装方法显式传 `version=row.version`。
- **不变量**：`t_user.refresh_token_version`（refresh 轮转）与行 `version`
  （OCC）是不同语义，两个字段并存。`TPartEvent`（append-only）不加 version。

---

## 13. 部分数量批次化（2026-07-29 引入，历史；2026-09-24 PR-3 后已下线）

> **2026-09-24 PR-3 状态**：本节描述的批次化子系统（`PartService` +
> `service/_assembly_rollup.py` + `service/_batch_ops.py` +
> `service/_delivery_note_events.py` + `service/_session_refresh.py` +
> `service._to_out` rollup 路径 + 全部 dormant 测试）已随 v1 业务路由下线
> 整体删除。本节保留作为历史设计文档，便于回溯「为什么 PartStateMachine
> 鸭子复用 + 批次 rollup 这么设计」。
>
> 核心 ORM `TPart` / `TPartBatch` 仍保留（供 backend-rust v2 读基表 +
> alembic 迁移管理），但本仓不再持有任何 service / repository 引用它们。
> `service.printing` 仅消费 `notes.get_by_id` / `parts.get_by_id` 等基础
> repository 方法，不走批次化路径。
>
> `t_part_batch.current_process_step_id` 列 + 索引仍由
> `tests/conftest.py::_apply_pr3_test_db_patch` 幂等 DDL 补齐（PR-2 兼容）。

核心思想：**所有数量永远活在批次里**（`t_part_batch.quantity`），
`t_part.quantity` 是工单总容量，向后兼容；批次即可拆可合，
工单 `status / location / holder / next_process_id` 由「最落后」的活跃批次
rollup 派生。

### 模型与不变量
- **t_part_batch**（新表，`AuditMixin`）：
  `id, part_id, batch_no（per-part 递增）, quantity, status, location,
  current_holder_id, next_process_id, placed_at, delivery_note_id,
  parent_batch_id（拆分谱系）`；索引：`(part_id)`,
  `(status, current_holder_id)`, `(location, status, next_process_id)`,
  `unique(part_id, batch_no)`；**删除物理禁用**，取消走 CANCELLED 终态。
- **t_part_event** 新增：`batch_id`（事件归属批次；工单级事件 NULL）、
  `quantity`（本次数量）。
- **不变量**：`Σ(未删除批次.quantity) = t_part.quantity`，service 层强制
  （`_split_batch` 取锁重校验、`_rollup_part_status` 自动维护）。

### 状态机鸭子复用
`PartStateMachine` 通过 `model.status / location` 字段恢复起始状态，
进入态 `on_enter_*` 直接写 `self.model` 属性。`TPartBatch` 与 `TPart` 对该 SM
暴露同名字段，`sm` property 直接复用，零改 SM。所有事件回调经 `_add_event`
统一从 `model` 反射 `part_id / batch_id / quantity`（批次
`part_id = self.model.part_id`、`batch_id = self.model.id`）。批次无 `serial_no`
列，service 在解析/拆出批次时写入 transient 属性 `model._part_serial = part.serial_no`，
SM 的 `_serial_of(model)` 优先读它，否则回退 `model.serial_no`（兼工单级事件）。

### 父子批次生命周期
- **创建工单**：自动生成 `batch_no=1` 的根批次（quantity = 工单总量，
  镜像工单当前 status / location / holder）。
- **拆分**（`_split_batch` in `service/_batch_ops.py`）：`get_for_update`
  （`FOR UPDATE + populate_existing`）锁源批次行；锁内重校验数量边界；
  `MAX(batch_no)+1` 取新号；新批次**继承**源批次的 status / location /
  current_holder_id / next_process_id / placed_at，**不继承**
  `delivery_note_id`（跟单量留在源批）；同步刷源 batch 的 `version`；
  写 SPLIT 事件（挂在新批次上，`quantity=拆出量`）。
- **合并**：v1 未实现（v2 候选）；同架同工序的两个批次需经 service 手动合并。

### 工单 rollup（核心简化点）
`_after_batch_transition` 是所有流转端点的统一收尾：
**先 `refresh_for_state_machine(part, attrs=(status/location/...))` 刷新派生字段**
（防 MissingGreenlet，跟 keepwith CLAUDE.md §12 同源）→ `_rollup_part_status`：
- 有活跃批次 → `part.status / location / holder / next_process_id` =
  `min(active, key=(ROLLUP_PROGRESS[status], batch_no))`
  （`ROLLUP_PROGRESS = {PENDING:0, PROGRAMMING:1, IN_PROCESS:2, REPAIRING:2,
   OUTSOURCE:3, INSPECTION:4, READY_TO_SHIP:5, DELIVERED:6}`）。
- 全部终态 → 全部 `CANCELLED` ⇒ `CANCELLED`，否则 `COMPLETED`；
  `part.serial_no = None`（回池），写 `batch_id=NULL` 的工单级终态事件。
- 全部活跃批次 `DELIVERED` 且 `actual_delivery_date IS NULL` → 写当天
  （送货单 pickup / auto-complete 共用）。

### 通用拆分原语
现有所有流转端点接受可选 `batch_id`（显式目标）+ `quantity`（部分量）参数。
Service 调用 `_maybe_split(part, batch, quantity)`：
- `quantity is None or == batch.quantity` → 不拆，直接拿批次流转；
- 否则 `_split_batch(...)` 后取新批次。

`pick_up_by_scan` / `scan_event` 用专用 `_resolve_scan_batch`：先按 `holder_id`
收窄到唯一者，0 命中抛 `BIZ_INVALID_TRANSITION`，>1 命中要求指定 `batch_id`，
**保持旧错误码语义**（`BIZ_AUTH_SHELF_MISMATCH` 等）。

### 错误码新增
`BIZ_PART_BATCH_NOT_FOUND` / `BIZ_PART_BATCH_INVALID_QUANTITY` /
`BIZ_PART_QUANTITY_LOCKED`（已拆分禁止改工单总量）；
`t_part.delivery_note_id` 列为兼容保留，**新写入仅写
`t_part_batch.delivery_note_id`**；`_to_out` 派生送货单字段时优先取 part 列，
回退到活跃批次的 `delivery_note_id`。

### 集成测试
`tests/test_part_batch.py` 覆盖：拆分守恒 / 边界 / 终态保护、
部分领取 / 归还 / 品检通过 / 打回 / 发货全链路、rollup 终态 + serial 释放、
cancel 级联 / 单批次、update_part 总量保护、送货单部分量入单（自动拆）+
批次送货、多批次必须指定 batch_id。

---

## SQLAlchemy 异步陷阱（务必牢记）

### MissingGreenlet

项目用 SQLAlchemy 2.0 异步 + asyncpg，所有 DB 操作必须在 `async` 函数里 `await`。
任何**同步函数**（Pydantic 校验器 / `from_attributes=True` 反序列化 /
`@property` / `__repr__`）都不能触发 `await` 或访问需 DB IO 的字段，否则抛：
```
sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called; ...
```

常见诱因与规避：
- **`from_attributes=True` 反序列化 ORM**：会访问未加载的列触发 lazy load。
  **正确做法**：始终用关键字传参（`AssemblyOut(id=asm.id, ...)`），
  不要 `from_attributes`。
- **访问 `lazy="raise"` 关系**（如 `TPart.customer`）：
  不要 `part.customer.name`，显式 `await self.customers.get_by_id(part.customer_id)`。
- **在 `field_validator` / `field_serializer` 中访问 ORM 字段**：
  校验器跑在同步上下文，只做值变换，不查 DB。
- **`await self.session.flush()` 之后才能读 server_default 列**。
- **`onupdate=func.now()` 列被 UPDATE 触碰后访问**：`AuditMixin.updated_at` 是
  `onupdate=func.now()`；「先 INSERT 再 UPDATE 同一行」的两阶段写会把
  `updated_at` 标 expired，async session 里 lazy refresh 触发同步 IO →
  MissingGreenlet（可能延后到属性访问才爆）。`expire_on_commit=False` 不防
  这种 expire。

**约束**：任何带 `AuditMixin` 的 ORM **不走「先 INSERT 再 UPDATE 同一行」的
两阶段写**。某列后续才知道，就先在 Python 侧解析完再构造对象一次性 INSERT
（如 `serial_no`：先 `acquire_serial` 再构造）。

> OCC（`version_id_generator=True`）只保护 `version_id_col` 本身（`version`）
> 不 expire；`updated_at`（`onupdate=func.now()`）等 server-side 列被 UPDATE
> 触碰后仍会标 expired，sync-read 仍会触发 implicit SELECT → MissingGreenlet。
> 规避：在 sync-read 前显式 `await session.refresh(obj, attribute_names=("updated_at", ...))`，
> 参见 `service/_session_refresh.py::refresh_for_state_machine`（已有
> `PartService.pass_inspection` / `service/outsource_quote.py` 等调用点）。
> **反例**：2026-07-22 `AssemblyService.cancel_assembly` 因 sync 读
> `asm.updated_at` 抛 MissingGreenlet，后由 commit 修复。

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
- 加急行整行红底 `#fde2e2`（与 Dashboard 同款，`row-class-name="row-urgent"`），
  区别于表单橙底 `#fdf6ec`。
- 申请人补全用 `el-autocomplete`（`useApplicantSearch::querySearch` 纯内存过滤
  缓存的申请人，`:debounce="0"`，不发网络请求）。

### Element Plus 工作流
- `package.json` 声明 `^2.7.0`；实际版本以 `cd frontend && npm ls element-plus`
  为准（当前 2.14.x），文档基准 2.14.1。
- **任何对 `el-*` 组件、`@element-plus/icons-vue` 图标、
  `ElMessage`/`ElMessageBox`/`ElNotification`/`ElLoading` 命令式 API、
  或 `main.ts` 中 `app.use(ElementPlus, ...)` 与 locale 的修改，必须先调用
  `element-plus` skill 并 WebFetch 对应组件官方文档，回复中附
  `> Source: https://element-plus.org/...` 一行；不得凭记忆写 props / events / slots。**
- skill 的 `references/` 是 curated 高频子集；未命中时按 skill 内回退路径去官网
  WebFetch / WebSearch。
- 项目用 full import（`main.ts` 中 `app.use(ElementPlus, { locale: zhCn })` +
  图标全局注册循环），不引入 `unplugin-auto-import`。

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

`alembic/versions/` 下用 **12 位零填充数字** revision id（如
`000000000001_schema_init.py`），不是 hex。模块顶部写明 `revision` /
`down_revision` / `Create Date`，docstring 说明要点。

**当前迁移（5 个 schema + 4 个 prod_data 共 9 文件，schema 链 001 → 003 → 005
→ 009 → 010，单 head = `000000000031`）**：

| 文件 | revision | down | 内容 |
|------|----------|------|------|
| `schema/000000000001_schema_init.py` | `000000000001` | base | 唯一 schema 基线：一次建全部表 + 索引 + 约束。文件表统一 `t_part_file`（另建 2 张 legacy 死表 `t_drawing_file` / `t_cnc_program`）；`t_customer.id` 用 `autoincrement=False`（全表雪花 ID）；所有 AuditMixin 表带 `version` 列。**含 IAM 基表 `t_user` / `t_user_role` / `t_menu` / `t_role_menu`**（2026-09-19 起 ORM 抽象迁至 rust v2，基表 + seed 仍由本仓 alembic 管理）。 |
| `prod_data/000000000002_data_init.py` | `000000000002` | `000000000001` | 唯一数据种子（全部 ON CONFLICT 幂等，无假数据）：工种 / 工序 / 工种↔工序映射 / 真实工人 / **账号（密码 changeme）** / **菜单 + role_menu** / `t_serial_counter` A-Z 全 26 行 / 货架↔工序默认映射（每 active PRODUCTION 架映射 5 个 INHOUSE 工序）。**IAM seed 在此**——rust v2 通过自己的迁移继承 / 验证此数据。 |
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

- `alembic.ini`：`version_locations = schema:prod_data`
  （`recursive_version_locations = true`）。**无 `dev_data/` 目录**。
- `alembic heads` 只返 1 行（`000000000031`）；`alembic upgrade head` 单命令即可
  （Dockerfile 的 `CMD alembic upgrade head && uvicorn ...`）。
- **2026-09-19 IAM 域迁出**：alembic 链零改动。`t_user` / `t_user_role` /
  `t_menu` / `t_role_menu` 基表 + seed 数据保留供 backend-rust v2 直接读写；
  本仓 ORM 抽象（`model/user.py` / `model/user_role.py` / `model/menu.py`）已
  删除，rust v2 通过自家迁移管理 IAM 表结构与版本演进。
- **2026-09-24 PR-3 dormant 全删**：alembic 链零改动（`alembic upgrade head`
  仍成功，head `000000000031` 不变）。`schema_init` 中所有表（含 dormant 业务
  表 / 死表）继续保留供 backend-rust v2 直接读写；本仓 ORM 抽象（model/* /
  repository/* / service/_*.py 的 dormant 业务部分）已删，rust v2 通过自家
  迁移管理业务表结构与版本演进。
- 冷启结果：seed 表有数据（含 IAM seed + 工种 / 工序 / 工人 / 货架↔工序
  默认映射 / 菜单 / role_menu / `t_serial_counter` A-Z 全 26 行）；
  业务表（t_part / t_part_batch / t_part_file / t_assembly / t_customer /
  t_delivery_note）为空。
- **本仓活跃 ORM（model/__init__.py）**：TPart / TPartBatch / TPartFile /
  TAssembly / TCustomer / TDeliveryNote（6 个）。
- **新 schema 迁移放 `schema/` 子目录**，revision id 用下一个 12 位数字，
  `down_revision` 指向当前 head。改 `schema_init` 时验收门：全新库 `upgrade head`
  后 `pg_dump --schema-only` 与旧链对比无意外差异。
- **已有库对齐**：确认 schema 等价后 `alembic stamp <head>` 即可；
  dev 本地假数据另写独立 seed 脚本（不走迁移）。

---

## API 端点速查

> **2026-09-24 PR-3 最终态**：本仓 `/api/v1/*` + `/api/v1/health` 仅保留 8 个
> 活跃端点（3 STS + 4 打印 + 1 health）。其余 22 + 5 = 27 个 v1 + MCP 路由
> 已 git rm；业务由 backend-rust v2 的 `/api/v2/*` 承接。鉴权 / 账号 / 角色 /
> 菜单相关由 `/api/v2/iam/*` 承接。

> 统一信封 `{ code: 0, message: "ok", data: ... }`。**所有 8 个端点裸开鉴权**，
> 靠部署层 nginx / 安全组隔离保证。权限缩写（仅作历史参考）：M=MANAGER,
> C=CLERK, S=SHELF_ACCOUNT, CNC=CNC_PROGRAMMER, I=INSPECTOR, *=任意已登录。

### /files/sts-*（api/v1/sts.py — **保留** STS 凭证端口）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | /api/v1/files/sts-tmp-keys | 裸开 | 2026-09-17 新增：前端直传 COS 临时凭证，TTL 1800s，CAM policy 限定 `tmp/{user_id}/{sha16}/*` 单目录。 |
| POST | /api/v1/files/sts-prefix-credentials | 裸开（内部端口） | 2026-09-18 新增：供 rust 后端按任意 `tmp/...` 前缀签凭证；prefix 必须 `tmp/` 开头 + 至少含一个子目录段 + 无 `* ? .. \x00 \\` 字符。 |
| GET | /api/v1/files/sts-health | 裸开（healthcheck 探针） | 2026-09-18 新增：STS 签发自检，每次 uuid4 hex probe prefix `tmp/__sts_healthcheck__/<hex>/probe`（TTL 60s）真实调 SDK 签发，验证 SDK + 主账号密钥 + CAM policy + 网络整条链路；返回 `{status, probe_prefix, expired_at}`。 |

### /parts/*（api/v1/printing.py — **保留** 打印图纸端口，2026-09-24 PR-2 新增）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | /api/v1/parts/{id}/print | 裸开 | **2026-09-24 PR-2 新增**：单件打印图纸 PDF（图纸正面 + 条码背面）；handler 调 `service.printing.build_part_print_pdf`；支持多图纸分页。 |
| POST | /api/v1/parts/print-batch | 裸开 | **2026-09-24 PR-2 新增**：批量打印图纸 PDF；handler 调 `service.printing.build_parts_print_pdf_batch`。 |

### /delivery-notes/*（api/v1/delivery_note_print.py — **保留** 送货单 / 标签打印端口，2026-09-24 PR-2 新增）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | /api/v1/delivery-notes/{id}/print | 裸开 | **2026-09-24 PR-2 新增**：送货单 Excel（F/L 模板按一级客户前缀分发）；handler 调 `service.delivery_note_print.DeliveryNotePrintService`。 |
| POST | /api/v1/delivery-notes/{id}/print-labels | 裸开 | **2026-09-24 PR-2 新增**：标签 Excel。 |

### /health（main.py — **保留** 健康检查）

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| GET | /api/v1/health | 裸开 | 容器健康检查（compose 探针）；不查 DB（DB 联通由 lifespan 心跳保证）。 |

---

### 历史 v1 + MCP 路由（已 git rm，仅供参考）

> 以下路由自 2026-09-17 / 09-19 / 09-24 起不再由本仓服务；前端调用全部走
> backend-rust v2 的 `/api/v2/*`。仅在 git history 中保留以备审计。共 27 个：
>
> - 2026-09-17 首批（18 个）：applicant / assembly / cnc_program / customer /
>   delivery_note / drawing / outsource_company / outsource_quote /
>   outsource_shipment / part / process / shelf / statistics / user /
>   work_type / worker / ws（18 个 v1 业务路由）
> - 2026-09-19 增补（4 个 IAM 端点）：`POST /auth/login` +
>   `POST /auth/refresh` + `GET /auth/me` + `POST /auth/change-password`
> - 2026-09-24 增补（5 个 MCP 端点，原 PR-1 移除）：`GET /api/mcp/health` +
>   `GET /api/mcp/dashboard-stats` + `POST /api/mcp/parts/by-serial` +
>   `GET /api/mcp/parts/{id}` + `GET /api/mcp/files/{id}/download-url`

---

## Service 层速查（2026-09-24 PR-3 最终态）

> 每个 Repository 继承 `create / get_by_id / update / soft_delete` 标准模式。
> 2026-09-24 PR-3 后，本仓仅保留 3 个 service：
> `StsService` / `PrintingServiceFacade` / `DeliveryNotePrintService` +
> 3 个活跃 helper（`_id_parse` / `_print_back_page` / `_print_front_cache`）。
> 历史业务 service（applicant / assembly / customer / delivery_note /
> outsource_company / outsource_quote / part / process / shelf /
> shelf_process / statistics / work_type / work_type_process / worker /
> mcp_query / part_file / dashboard / auto_complete）已删除。
> 2026-09-24 PR-3 还删除 dormant helper：`service/_assembly_rollup` /
> `service/_batch_ops` / `service/_delivery_note_events` /
> `service/_session_refresh`。

- **PrintingServiceFacade**（`service/printing.py`，2026-09-24 PR-2 新增）：
  单件 / 批量打印图纸 PDF facade；`build_part_print_pdf` /
  `build_parts_print_pdf_batch` 调 `service/_print_back_page.py`（条码背面）
  + `service/_print_front_cache.py`（图纸正面缓存）；handler 在
  `api/v1/printing.py`。
- **DeliveryNotePrintService**（`service/delivery_note_print.py`，2026-09-24
  PR-2 新增）：送货单 Excel + 标签 Excel；调
  `repository/delivery_note.py::notes.get_by_id` 读送货单，模板填表走
  `template/delivery_note_fala.xlsx` / `template/delivery_note_luda.xlsx`
  （F/L 一级客户前缀分发）；handler 在 `api/v1/delivery_note_print.py`。
- **StsService**（`service/sts.py`）：STS 凭证端口薄层 service；只读 settings +
  调 `core.sts.grant_sts_tmp_key`，不持 session / 不写 DB；handler 在
  `api/v1/sts.py`。

活跃 helper：
- **`service/_id_parse.py`** — `parse_snowflake_id(value, field_name=...)`
  把 str 雪花 ID 转 int，失败抛 `BIZ_INVALID_VALUE` 400。
- **`service/_print_back_page.py`** — 背面序列号大字 + Code128 条码 PDF
  生成（图纸 / 图片双面打印用）。
- **`service/_print_front_cache.py`** — 正面图纸 / 图片 SHA-256 缓存 +
  旋转 / 缩放 / 拼接。

---

## Repository 层速查（非标准查询方法，2026-09-24 PR-3 最终态）

> 2026-09-24 PR-3 后，本仓仅保留 6 个 repository：
> `PartRepository` / `PartBatchRepository` / `PartFileRepository` /
> `AssemblyRepository` / `CustomerRepository` / `DeliveryNoteRepository`。
> 历史 dormant repository（applicant / process / worker / shelf /
> shelf_process / work_type / work_type_process / outsource_company /
> outsource_quote / outsource_quote_event / outsource_shipment /
> outsource_company_process / part_event / pickup_skip_event /
> serial_counter）已删除。

| Repository | 特殊方法 |
|------------|---------|
| PartRepository | `get_by_serial`; `list_with_filters` / `count_with_filters`（核心多维过滤+ILIKE）; `list_children`; `list_for_work_type`; `list_held_by_worker` |
| PartBatchRepository | （标准 `create` / `get_by_id` / `update` / `soft_delete`）|
| PartFileRepository | `list_by_part`; `find_active_by_part_kind_sha`; `soft_delete_many` |
| AssemblyRepository | `list_with_filters` / `count_with_filters` |
| CustomerRepository | `list_all` / `list_by_ids` / `list_roots` / `list_children` |
| DeliveryNoteRepository | （2026-09-24 PR-2 从 git 785df37^ 恢复，10 个公开方法 + 2 个私有辅助；专供 `service.delivery_note_print::notes.get_by_id`） |

---

## Model/ORM 速查（2026-09-24 PR-3 最终态）

> 2026-09-19 IAM 域迁出后，`TUser` / `TUserRole` / `TMenu` / `TRoleMenu` ORM
> 已删除；2026-09-24 PR-3 后，dormant 业务 ORM（applicant / process /
> worker / shelf / shelf_process / work_type / work_type_process /
> outsource_company / outsource_quote / outsource_quote_event /
> outsource_shipment / outsource_company_process / part_event /
> pickup_skip_event / serial_counter / drawing_file / cnc_program /
> delivery_note_event / delivery_note_counter / process_chain_step）也已
> 删除。本仓仅保留 6 个 ORM（TPart / TPartBatch / TPartFile / TAssembly /
> TCustomer / TDeliveryNote）。账号 / 角色 / 菜单相关 ORM 抽象由
> backend-rust v2 承接。基表（`t_user` / `t_user_role` / `t_menu` /
> `t_role_menu`）保留供 rust 直接读写，alembic 链不动。

### 核心业务表

| ORM | 表 | 关键列（非审计/非 ID） |
|-----|----|----|
| TPart | t_part | serial_no, name, drawing_no, applicant_name, quantity, unit_price, total_price, request_date, planned_delivery_date, actual_delivery_date, **order_no, system_delivery_date, note**, status(10 态), location(OFFICE/PRODUCTION_SHELF/WORKER/INSPECTION_SHELF/**OUTSOURCE_COMPANY**), is_urgent, current_holder_id(多态→shelf/worker/**outsource_company**), placed_at, customer_id, assembly_id, next_process_id |
| TPartBatch | t_part_batch | part_id, batch_no(per-part 递增), quantity, status, location, current_holder_id, current_process_step_id(→ t_process_chain_step.id), delivery_note_id, parent_batch_id; 索引 `(part_id)` / `(status, current_holder_id)` / `(location, status, next_process_id)` / `unique(part_id, batch_no)` |
| TAssembly | t_assembly | serial_no, drawing_no, name, applicant_name, customer_id, request_date, planned_delivery_date, actual_delivery_date, is_urgent, status(PENDING/IN_PROCESS/INSPECTION/READY_TO_SHIP/DELIVERED/COMPLETED/CANCELLED) |
| TCustomer | t_customer | name, parent_id(自引用邻接表), serial_prefix(A-Z) |
| TDeliveryNote | t_delivery_note | serial_no, customer_id, status(DRAFT/SUBMITTED/PICKED_UP/ARCHIVED), delivery_date(Date), notes；外加 event 子表 `t_delivery_note_event` + 计数器子表 `t_delivery_note_counter`（schema_init 仍建表，本仓不持有 ORM 抽象；service.delivery_note_print 仅读 `notes.get_by_id`） |

### 关联/文件表

| ORM | 表 | 关键列 |
|-----|----|----|
| TPartFile | t_part_file | polymorphic `part_id`(=t_part.id 或 t_assembly.id); kind(DRAWING/THREE_D_MODEL/G_CODE/SETUP_SHEET/ASSEMBLY_MASTER/CAD_2D); file_type, object_key, original_filename, file_size, content_type, content_sha256(CHAR(64) NULL), upload_status |
| TSerialCounter | t_serial_counter | prefix: str(1) PK, counter |

### enums.py 全枚举

- `PartStatus`: PENDING, PROGRAMMING, IN_PROCESS, INSPECTION, READY_TO_SHIP,
  DELIVERED, REPAIRING, **OUTSOURCE**, COMPLETED, CANCELLED（10）
- `PartLocation`: OFFICE, PRODUCTION_SHELF, WORKER, INSPECTION_SHELF,
  **OUTSOURCE_COMPANY**
- `AssemblyStatus`: PENDING, IN_PROCESS, **INSPECTION**, **READY_TO_SHIP**,
  **DELIVERED**, COMPLETED, CANCELLED（7，2026-08-03 扩展）
- `PartEventType`: CREATED, RELEASED, SENT_TO_PROGRAMMING, CNC_RELEASED,
  PLACED_ON_SHELF, PICKED_UP, RETURNED, INSPECTED, INSPECTION_FAILED,
  STATUS_CHANGED, REPAIR_STARTED, REPAIR_COMPLETED, SENT_TO_OUTSOURCE,
  RECEIVED_FROM_OUTSOURCE, QUOTE_CREATED, QUOTE_APPROVED, CANCELLED, RECALLED,
  COMPLETED
- `UserRole`: MANAGER, SHELF_ACCOUNT, CLERK, INSPECTOR, CNC_PROGRAMMER
  （**保留枚举**——被 `core/_file_kind_policy.py` 消费；DTO 在 v2 端继续复用）
- `ShelfZone`: PRODUCTION, INSPECTION
- `PartSortKey`: PLANNED_DELIVERY_DATE, REQUEST_DATE, CREATED_AT, SERIAL_NO,
  DRAWING_NO, NAME · `SortDir`: ASC, DESC
- `ProcessCategory`: INHOUSE, OUTSOURCE
- `PartFileKind`: DRAWING, THREE_D_MODEL(值 `3D_MODEL`), G_CODE, SETUP_SHEET,
  ASSEMBLY_MASTER, CAD_2D
- `DeliveryNoteStatus`: DRAFT, SUBMITTED, PICKED_UP, ARCHIVED ·
  `DeliveryNoteSortKey`: SERIAL_NO, DELIVERY_DATE, CREATED_AT ·
  `DeliveryNoteEventType`: CREATED, EDITED, SUBMITTED, PICKED_UP, ARCHIVED
- `OutsourceQuoteStatus`: DRAFT, SUBMITTED, APPROVED, REJECTED, USED ·
  `OutsourceQuoteEventType`: CREATED, EDITED, SUBMITTED, APPROVED, REJECTED,
  USED · `OutsourceQuoteSortKey`: CREATED_AT, PRICE, REVIEWED_AT
  （**保留枚举**——仅供历史 dormant 测试 + DTO 引用；本仓无活跃调用方）
- `SCAN_EVENT_TYPES`（set，非枚举）: PICKED_UP, RETURNED, INSPECTED

### Mixin

| AuditMixin | version, created_at, created_by, updated_at, updated_by, deleted_at | 业务主表 |
| EventTimestampMixin | created_at | 事件/日志表（append-only）|
| Base | （空 abstract）| 所有 ORM 基类 |

---

## 现状与已知问题

1. **model/DB 漂移**：`uv run alembic check` 会报告预存的 index/comment 差异
  （`t_part.serial_no` partial unique index 等），与近期改动无关，不要在
  处理其他 PR 时混入修复。`t_part_event` / `t_worker` index/comment 漂移已
  不再相关（2026-09-24 PR-3 后对应 ORM 已删）。
2. **`created_by` / `updated_by` 全为 NULL**：2026-09-19 IAM 域迁出后，
   本仓不再持有 user/role 抽象，写操作人字段在 v2 端按 CurrentUser.id 填；
   本仓活跃路径（printing / delivery_note_print / sts）只读不写，
   AuditMixin 字段填入由 backend-rust v2 端负责。
3. **`docs/db-design-part-customer.md` 部分描述已过时**（审计字段说由 `Base`
   声明，实际是 `AuditMixin`）；以本文件和 `model/audit.py` 为准。
4. **历史时间错位不 backfill**：早期 `deleted_at` / `last_login_at` /
   `placed_at` 可能有 8h 偏差（`now_naive()` 接入前），单条 SQL 修正即可，
   不做全量迁移。

---

## 14. v1 业务路由下线 + JWT 完全 Bypass + IAM 域迁出 + dormant 业务下线（2026-09-17/19/24）

2026-09-17 起，本仓 v1 业务路由整体下线，业务由 backend-rust v2 承接；本仓同时
切换为 JWT 完全 Bypass 模式。2026-09-19 进一步把 auth / user / menu 域（IAM）
从本仓源码完全迁出。2026-09-24 PR-3 把全部 dormant 业务代码（49 个源文件 + 49
个测试）下线，本仓仅保留 8 个活跃端点。

### 范围

**保留端点（共 8 个：3 个 STS 端口 + 4 个打印端口 + 1 个健康检查）**：

| 方法 | 路径 | 实现 | 说明 |
|---|---|---|---|
| POST | `/api/v1/files/sts-tmp-keys` | `api/v1/sts.py` | **2026-09-17 新增**：前端直传 COS 临时凭证（裸开鉴权）；CAM policy 限定 `tmp/{user_id}/{sha16}/*` 单目录，TTL 默认 1800s。 |
| POST | `/api/v1/files/sts-prefix-credentials` | `api/v1/sts.py` | **2026-09-18 新增**：内部端口——供 rust 后端按任意 `tmp/...` 前缀签凭证；prefix 必须 `tmp/` 开头 + 至少含一个子目录段 + 无 `* ? .. \x00 \\` 字符。 |
| GET | `/api/v1/files/sts-health` | `api/v1/sts.py` | **2026-09-18 新增**：STS 签发自检（healthcheck 探针）——裸开鉴权；每次 uuid4 hex probe prefix `tmp/__sts_healthcheck__/<hex>/probe`（TTL 60s）真实调 SDK 签发（不 mock），验证 SDK + 主账号密钥 + CAM policy + 网络整条链路；BizError 透传（自带 http_status）让 compose healthcheck 拿到非 2xx 即 fail。 |
| GET | `/api/v1/parts/{id}/print` | `api/v1/printing.py` | **2026-09-24 PR-2 新增**：单件打印图纸 PDF（图纸正面 + 条码背面）；handler 调 `service.printing.build_part_print_pdf`。 |
| POST | `/api/v1/parts/print-batch` | `api/v1/printing.py` | **2026-09-24 PR-2 新增**：批量打印图纸 PDF；handler 调 `service.printing.build_parts_print_pdf_batch`。 |
| POST | `/api/v1/delivery-notes/{id}/print` | `api/v1/delivery_note_print.py` | **2026-09-24 PR-2 新增**：送货单 Excel（F/L 模板）；handler 调 `service.delivery_note_print` 模板填表。 |
| POST | `/api/v1/delivery-notes/{id}/print-labels` | `api/v1/delivery_note_print.py` | **2026-09-24 PR-2 新增**：标签 Excel；handler 调 `service.delivery_note_print`。 |
| GET | `/api/v1/health` | `main.py` | 容器健康检查（compose 探针）；不查 DB（DB 联通由 lifespan 心跳保证）。 |

**下线路由（22 + 5 个，全部 git rm，2026-09-17 / 09-19 / 09-24）**：

- 2026-09-17 首批（18 个）：applicant / assembly / cnc_program / customer /
  delivery_note / drawing / outsource_company / outsource_quote /
  outsource_shipment / part / process / shelf / statistics / user /
  work_type / worker / ws
- 2026-09-19 增补（4 个 IAM 端点，原保留在 `api/v1/auth.py`）：
  `POST /api/v1/auth/login` + `POST /api/v1/auth/refresh` +
  `GET /api/v1/auth/me` + `POST /api/v1/auth/change-password`
- 2026-09-24 增补（5 个 MCP 端点，原保留在 `api/mcp/*.py`，PR-1 移除）：
  `GET /api/mcp/health` + `GET /api/mcp/dashboard-stats` +
  `POST /api/mcp/parts/by-serial` + `GET /api/mcp/parts/{id}` +
  `GET /api/mcp/files/{id}/download-url`

**保留的 repository / service / schema 子集（2026-09-24 PR-3 最终态）**：
- `repository`：`part / part_batch / part_file / assembly / customer /
  delivery_note`（被 `service.printing` + `service.delivery_note_print` +
  `statemachines.assembly` 实际消费）
- `service`：`printing / delivery_note_print / sts` + `_id_parse /
  _print_back_page / _print_front_cache`（活跃 helper）
- `schema`：`sts / _types`
- `model`：所有**业务** ORM 保留——alembic / rust v2 仍引用；**IAM** 相关
  ORM（`TUser` / `TUserRole` / `TMenu` / `TRoleMenu`）已删除；dormant 业务
  ORM（applicant / process / worker / shelf / work_type / outsource_company
  / outsource_quote / part_event / pickup_skip_event / serial_counter /
  drawing_file / cnc_program / delivery_note_event / delivery_note_counter /
  process_chain_step / outsource_shipment / outsource_quote_event /
  outsource_company_process / shelf_process / work_type_process）已删除

### JWT Bypass 与 STS 端口裸开鉴权

历史 `core/permission.py` / `core/security.py` 已于 2026-09-24 PR-3 与
`tests/test_delivery_note_print_api.py` 一并删除。`core.security` 曾仅被该测试
构造 token 验证 `/print` 鉴权链路引用；测试文件删除后，`core/security.py` 不再
有真实下游，`core/permission.py` bypass 壳亦无意义（8 个端点全部裸开鉴权）。

STS 端口与 `/health` 不依赖 `get_current_user` 也不挂 `Depends(require_auth)`；
安全性靠 nginx `/api/v1/files/` / `/health` 不暴露 / 安全组隔离保证。部署层
务必确认此路径不被直连到公网（仅 frontend nginx 同源访问）。

### 验证门

`uv run pytest` 当前 **~160 passed / 0 skipped**（2026-09-24 PR-3 后 dormant
全删；活跃 6 个测试文件 `tests/test_delivery_note_print_merge.py` +
`tests/unit/test_{printing_service,print_back_page,print_front_cache,
sts_health,sts_prefix_credentials,file_hash,time}.py` 全过；2026-09-19 IAM
域迁出前 269 passed，2026-09-19 → 202 passed / 662 skipped，2026-09-24
PR-3 后 -49 个 dormant 测试删除）。

PR-3 前 dormant 测试 662 个全部由 `pytestmark = pytest.mark.skip(reason=...)`
文件级跳过，理由为「2026-09-17 v1 业务路由下线 + JWT bypass：业务由
backend-rust v2 承接」；PR-3 后 dormant 文件全删，无须 skip。

`tests/conftest.py` 已删 `_V1_DORMANT_MODULES` / `_DormantStub` /
`_DormantPackage` / `_V1_REMOVED_FROM_PACKAGE` / `_install_dormant_stubs()`
等 dormant stub 全部（dormant 测试集合已删除，stub 无意义）；保留
`FakeCosClient` / `_FakeGetObjectResponse` / `_FakeRawStream` / `db_session` /
`clean_db` / `seed_root_batch`（活跃 fixture）。

`alembic upgrade head` 仍成功（head `000000000031` 不变；`t_user` /
`t_user_role` / `t_menu` / `t_role_menu` 基表 + seed 保留供 rust v2 读写）。

**重启 v1 业务**需从 git history 还原 22 + 5 = 27 个 v1 + MCP 源文件 +
恢复 `core/permission.py` 原实现 + 还原本节删除的 IAM / dormant 业务源文件，
并补 alembic 030 → 031 迁移的 DB 同步（见上文 §Alembic 迁移）。

### 风险与后续

- `created_by` / `updated_by` 全为 NULL 现状不变（item 2）；本仓不再持有
  user/role 抽象，由 v2 端按 CurrentUser.id 自动填。
- v1 复活指引：见 `_archive/api_v1/` 目录的文件历史 commit log；IAM 源文件
  见 git history（`service/auth.py` 等 commit SHA 可通过 `git log --diff-filter=D` 查）；
  dormant 业务源文件（applicant / process / worker / shelf / work_type 等）
  同样通过 git log --diff-filter=D 还原。