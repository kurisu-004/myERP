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
- CI/CD：本地 `./scripts/push-images.sh` → 腾讯云 TCR（`ccr.ccs.tencentyun.com/hsh-erp`） → CVM（`scripts/deploy.sh` 走 `docker compose pull && up -d`）。已无 GHCR / GitHub Actions。

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
- 所有业务表（含 `t_customer`）2026-07-07 起统一雪花 ID：列定义为
  `Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)`。
  历史：`t_customer` 原 BigSerial 1-20，迁移 `000000000006_customer_snowflake_id`
  已重写所有 id + parent_id + FK 引用并 DROP sequence。`t_customer_id_seq`
  不再存在，seed 与测试 `TRUNCATE` 不再带 `RESTART IDENTITY`。
- 雪花参数从 `.env` 读：`SNOWFLAKE_INSTANCE` / `SNOWFLAKE_SEQ` / `SNOWFLAKE_EPOCH`。

**JSON 序列化**（防止 JS 精度丢失）：
- `schema/_types.py` 定义 `IdStr`（可空）和 `IdStrNonNull`（非空）两个类型。
- 所有 Pydantic schema 的 **响应** ID 字段统一使用这两个类型，确保 JSON 响应中 ID 都是字符串。
- **仅影响序列化**（`when_used="json"`），Python 内部仍是 `int`，DB 列保持 `BigInteger` 不动。
- service 层 / repository 层接收时用 `int`，不受影响。

```python
# schema 中使用
assembly_id: IdStr = None       # 可空外键 → JSON: "123456..." 或 null
id: IdStrNonNull                # 非空主键 → JSON: "123456..."
```

**⚠️ 雪花 ID 入参必须用 `str` 类型（请求 body 端）**

`IdStr` / `IdStrNonNull` 只解决 **出参** 的精度问题。**入参**（请求 body）字段如果直接用 `int`，前端把字符串 ID 用 `Number()` 转数字时，19 位雪花 ID 会因为 `JS Number.MAX_SAFE_INTEGER`（≈9.007×10¹⁵）而丢精度，后端 `int()` 拿到一个错误值，查询不到原行（典型报错：`applicant X not found`）。

约束：
- **request body** 中所有雪花 ID 字段类型必须是 `str`（前端 TypeScript 也是 `string`），service 层收到后再 `int(data.field)` 转换后查 repository。
- **path parameter**（如 `/parts/{id}`、`/applicants/{id}`）由 HTTP URL 字符串直接传给 FastAPI，无 JS 中转，可用 `str`（为统一、与 body 保持一致，本项目所有 snowflake ID path 已统一为 str）。
- **query parameter** 同理（`customer_id` 等已统一为 str 入参）。
- 凡是新增的「snowflake ID 入参」字段，参考 `PartCreateRequest.applicant_id: str | None` 与 `PartCreateRequest.customer_id: str` 的写法。
- service 层统一用 `parse_snowflake_id(value, field_name=...)`（`service/_id_parse.py`）做 str→int 转换；转换失败抛 `BIZ_INVALID_VALUE` 400。

```python
# ✅ 正确（snowflake ID 入参用 str）
class PartCreateRequest(BaseModel):
    applicant_id: str | None = Field(default=None, description="雪花 ID 字符串")

# ❌ 错误（int 会丢精度）
class PartCreateRequest(BaseModel):
    applicant_id: int | None = None  # JS Number(199849051720515600) != 199849051720515600
```

```ts
// ✅ 前端：applicant_id 用 string，与后端 schema 一致
const payload: PartCreatePayload = {
  applicant_id: s.applicantId,   // 不要 Number(s.applicantId)！
  ...
}

// ❌ 错误：Number() 会丢精度
applicant_id: Number(s.applicantId)  // 19 位雪花 → 科学计数法
```

**service 层转换模式**（失败时抛 `BIZ_INVALID_VALUE`）：

```python
try:
    applicant_id_int = int(data.applicant_id)
except (TypeError, ValueError) as e:
    raise BizError(
        code=ErrCode.BIZ_INVALID_VALUE,
        message=f"applicant_id 必须是数字字符串：{data.applicant_id!r}",
        http_status=http_status.HTTP_400_BAD_REQUEST,
    ) from e
```

适用范围：**任何新增的雪花 ID 入参**字段（本项目已知在用的有 `PartCreateRequest.applicant_id`、`AssemblyCreateRequest.applicant_id`）。后续若新增 snowflake FK 入参，按此约定。

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

### Element Plus 工作流
- 前端 Element Plus 在 `package.json` 中声明为 `^2.7.0`（caret 范围）；实际安装版本以 `cd frontend && npm ls element-plus` 为准。
- 文档基准版本为 2.14.1（https://element-plus.org/en-US/component/overview）。版本不一致时参考 https://element-plus.org/en-US/guide/migration.html 比对差异，优先已安装版本行为。
- **任何对 `el-*` 组件、`@element-plus/icons-vue` 图标、`ElMessage` / `ElMessageBox` / `ElNotification` / `ElLoading` 等命令式 API、或 `frontend/src/main.ts` 中 `app.use(ElementPlus, ...)` 与 locale 相关的修改，都必须先调用 `element-plus` skill 并 WebFetch 对应组件官方文档，回复中附 `> Source: https://element-plus.org/...` 一行；不得凭记忆写 props / events / slots。**
- skill 的 `references/` 是 curated 高频子集（不收录如 `el-autocomplete`、`el-segmented`、`el-affix`、`el-tour` 等），未命中时按 skill 内「What to do when the component is not in references/」回退路径去官网 WebFetch / WebSearch。
- 项目采用 full import（`main.ts` 中 `app.use(ElementPlus, { locale: zhCn })` + 图标全局注册循环），不要引入 `unplugin-auto-import`。

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

`alembic/versions/` 下的迁移文件使用 **12 位零填充数字** revision id（如 `000000000001_schema_init.py`），不是 hex。

模块顶部写明 `revision` / `down_revision` / `Create Date`，迁移开头用 docstring 说明要点（如"不使用物理外键"）。

**当前迁移（2026-07-10 squash 后，仅 2 个文件；见 §15）**：
- `schema/000000000001_schema_init.py` — 唯一 schema 迁移（所有表/索引/约束的最终状态）
- `prod_data/000000000002_data_init.py` — 唯一数据种子（9 工种 / 19 工人 / 6 账号 / 22 菜单 / role_menu / A-Z 流水号计数器；无任何假数据）

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

7. **2026-07-06 客户管理 + 申请人表 + 雪花 ID 溢出修复（统一记录于此）**：

   **新增功能**
   - 新增「客户管理」一级菜单 + 「客户一览」「申请人一览」两个二级菜单（`alembic/versions/000000000005_customer_management.py`）。新增 `t_applicant` 表（雪花 ID + partial unique `(name, customer_id) WHERE deleted_at IS NULL`），与 `t_customer` 多对一。MANAGER + CLERK 都可读写。
   - `CustomerService` 扩展 CRUD（`get_customer / create_customer / update_customer / soft_delete_customer`），一级客户若有二级子节点或被 part/assembly 引用 → 拒软删。`CustomerRepository.create/update/soft_delete` 同步新增。
   - `ApplicantService`（新建）：CRUD + `get_or_create` + `search_for_customer`（前序查询，给零件/装配体对话框自动补全用）。申请人只能挂一级客户（service 层校验 `parent_id IS NULL`）；软删前用 `PartRepository.count_by_applicant_name_in_customers` 校验是否被未软删零件引用。
   - 5 个新错误码：`BIZ_APPLICANT_NOT_FOUND / DUPLICATE_NAME / BAD_CUSTOMER / IN_USE`、`BIZ_CUSTOMER_IN_USE`（20109）。
   - 新建 `frontend/src/views/customers/CustomerList.vue`（el-tree 树形展示，hover 显示 +子客户/编辑/删除按钮）；`frontend/src/views/applicants/ApplicantList.vue`（筛选 + 表格 + 新增/编辑）。
   - `PartBatchNew.vue` + `AssemblyCreate.vue` 改造：客户移到申请人之前；申请人改为 `el-select filterable remote`（联动一级客户下拉，前序查询）；移除「单价」「总价」「实际送货」；请购日期预填今天；提交时自动新增不存在的申请人。

   **雪花 ID 溢出修复（关键）**
   - 用户反馈新建零件报 `applicant 199849051720515600 not found` —— 19 位雪花 ID 超过 JS `Number.MAX_SAFE_INTEGER`（≈9.007×10¹⁵），前端把 `applicant_id` 用 `Number()` 转数字后丢精度，后端拿到错误值。
   - 修复：`schema/part.py::PartCreateRequest.applicant_id` 与 `schema/assembly.py::AssemblyCreateRequest.applicant_id` 由 `int | None` 改为 `str | None`；service 层 `int(data.applicant_id)` 转换后查 repository（失败抛 `BIZ_INVALID_VALUE`）。前端 `PartCreatePayload.applicant_id` 与 `AssemblyCreatePayload.applicant_id` 同步改为 `string | null`；`PartBatchNew.vue::onSubmit` 移除 `Number(s.applicantId)`，直接传字符串。
   - 约定已写入本文件「约定 3 · 雪花 ID 入参必须用 `str` 类型」一节；后续新增 snowflake ID 入参字段都按此模式。
   - 端到端验证：以字符串 `"199852260920918016"` 作 `applicant_id` POST `/parts`，返回 200，零件成功创建并关联申请人姓名。

   **`/shelves` 权限放开给 CLERK / CNC_PROGRAMMER（仅读）**
   - 用户反馈文员下发订单时选货架对话框报 `GET /shelves?zone=PRODUCTION&...` 403。根因：`api/v1/shelf.py` 之前是 router 级 MANAGER-only，而 `PartsList.vue:432` / `PartDetail.vue:737` / `ScanPartsWork.vue:354` 等多处业务页面（文员、编程员都要用）都依赖该端点。
   - 修复：拆 read_router（`GET /shelves`、`GET /shelves/{id}`，MANAGER + CLERK + CNC_PROGRAMMER）与 write_router（POST `/shelves`、`/{id}/update`、`/{id}/deactivate`，MANAGER-only）。`api/v1/__init__.py` 注册两个 router。货架是组织结构资源，写仍 MANAGER-only；读是业务前置数据，按 `[客户管理 / 工人一览]` 同款模式放开。
   - 端到端验证：CLERK / CNC_PROGRAMMER / MANAGER 三角色 GET `/shelves` 均 200；CLERK POST `/shelves` 仍 403。

8. **2026-07-06 图纸双面打印（图纸 + 反面条形码）**
   - 新增 `service/printing.py` + 后端端点 `GET /api/v1/parts/{id}/print-drawing`：根据零件的 master 图纸（`page_index IS NULL`，否则取任意最新一条）类型合成 A4 双页 PDF：
     * 上传 PDF → `pypdf.PdfReader` 读原页 + `PdfWriter.add_page` 追加条码页
     * 上传 PNG/JPG → `pillow` 居中按比例贴到 A4 第 1 页 + 第 2 页条码页
     * 无图纸 / STEP/DWG/DXF（不可纸面渲染） → 第 1 页用 `pillow` 渲染「零件信息卡」占位（图号 / 名称 / 客户 / 流水号）
     * 第 2 页通用：白底 A4 + 右下角 Code128 条形码（编码 `serial_no`，无则用占位 `NO-SERIAL`）+ 文字标签
   - `pyproject.toml` 新增 `pypdf>=4.0` 依赖；`api/deps.py` 新增 `get_part_repository` / `get_drawing_repository`。
   - 前端 `components/FileListCard.vue` 新增 `showPrint` prop + 头部「打印图纸（含条形码）」按钮（success green outline），点击：
     * 调 `printPartDrawing(partId)` 拿 Blob
     * 注入隐藏 iframe（1×1 像素不阻塞 UI）的 src
     * iframe.onload → `iframe.contentWindow.print()` 触发浏览器打印对话框（不弹新窗、不被拦截）
     * sandbox 失败时 fallback 到 `window.open`
   - `PartDetail.vue` 给 `FileListCard` 传 `:show-print="true"`（装配体详情不显示）。
   - 权限：MANAGER + CLERK（与创建/下发一致；CNC_PROGRAMMER / SHELF_ACCOUNT 无须打印）。
   - 端到端验证：CLERK GET 200（103KB 两页 A4 PDF）；CNC GET 403；不存在 part → 404。`uv run pytest tests/unit` → 191 passed；`npm run build` 通过；用 `sips` 渲染 PDF 看到 page 1 信息卡 + page 2 右下角条形码。

9. **2026-07-07 打印布局 / 朝向 / 序列号醒目 迭代**
   - **打印按钮与上传按钮并排**：`FileListCard.vue` 头部增加 `.header-actions` flex 容器（gap 8px），两个按钮天然左右并排。
   - **PDF 朝向与图纸同步**：图纸常用横向 A4，所以条码页 / 信息卡页必须跟着图纸的 `mediabox.width > height` 判断 `landscape/portrait`，避免双面打印翻转后上下颠倒。`service/printing.py::_detect_pdf_orientation`（PDF 走 mediabox）与 `_detect_image_orientation`（图片走 PIL 宽高比），默认 landscape。无图纸 fallback 也用 landscape。同一 part 的所有页强制同朝向。
   - **序列号醒目显示**：第 2 页右下角的整块由 4 部分组成（自上而下）：
     1. 「序列号」灰色 pill 标签（landscape 80px / portrait 64px）
     2. **大字号序列号**（landscape 120px / portrait 96px）+ 米黄底 + 橙色边框 + 黑字，方便人工目视对单
     3. Code128 条形码本体（按页面短边 55% 自适应）
     4. 条码下方小号标签（与 3 等宽居中）
   - **sips 仅作开发验证工具**：项目所有 PDF 生成链路（pillow / python-barcode / pypdf）都是跨平台纯 Python，部署到 Linux Docker / 云服务器 **零影响**；仅在 macOS 上 dev 阶段用 `sips` 把 PDF 渲染成 PNG 用来肉眼检查版面。
   - 端到端验证：3 种场景都生成 842×595 pt（landscape）双页 PDF — ①上传 PDF 图纸（mediabox=842×595）→ 沿用 landscape ②上传 STEP → fallback 到横向信息卡 ③无图纸 → 默认横向信息卡 + 反面条码。`sips` 渲染可见 page 1 = 上传的横向技术图纸，page 2 = 右下角「序列号」灰色标签 + 黄色边框超大 `L2014` + Code128 条形码 + 小号 `L2014` 扫描标签。`uv run pytest tests/unit` → 195 passed；`npm run build` 通过。

10. **2026-07-07 零件图纸单文件 + 覆盖 + 仅 PDF**
    - 后端 `service/drawing.py::upload_to_part`：扩展名校验由「白名单」改成硬编码 `== "pdf"`；上传前 `list_by_part(part_id)` 拿到旧图纸，逐个 `soft_delete` 并收集 `object_key`，**先删后建**在同一事务里完成（保证一致性）；新建成功后才 `asyncio.create_task(_safe_delete_cos(old_key))` 异步清 COS 旧对象。
    - 装配体的图纸不受此限制（沿用多文件语义；装配件是图文档归档）。
    - 前端 `components/FileListCard.vue`：`ACCEPT = '.pdf'`（之前接受 `.pdf,.step,.stp,.dwg,.dxf`）；上传按钮文案按 `files.length` 切换「上传图纸」/「替换图纸」明示语义。删除按钮仍保留（用户可手动归零）。
    - 端到端验证：连传两次 PDF → `t_drawing_file` 第 2 次只剩 1 行（id 新的），第 1 行 `deleted_at` 已设；`.step` 上传 → 400 拒绝。`uv run pytest tests/unit` → 195 passed；`npm run build` 通过。

11. **2026-07-08 装配体流水号体系 + 一览筛选 + 表头 popover UX**
    - **装配体流水号**：新迁移 `000000000008_assembly_serial_no` 给 `t_assembly` 加 `serial_no String(8) nullable + partial unique (serial_no) WHERE deleted_at IS NULL AND serial_no IS NOT NULL`。老装配件保持 NULL（不迁移）。新装配件创建时一次性 `acquire_serial(code)` → 子件派生为 `f"{serial}-{i:02d}"`（两位零填充，上限 99 件；超限抛 `BIZ_ASSEMBLY_TOO_MANY_CHILDREN 400`）。terminal（cancel/soft_delete）时 `asm.serial_no = None` 释放 partial unique 槽位。
    - **后端 schema 瘦身**：新增 `PartListItem`（去掉 `assembly_id / next_process_* / current_holder_* / placed_at`）/ `AssemblyListItem`（与 `AssemblyOut` 字段一致 + `serial_no`）两个窄 schema，专门服务于列表端点。`PartOut` / `AssemblyOut` 仍用于详情 / 创建响应。
    - **客户筛选级联**：service `list_parts` / `list_assemblies` 选 L1 时自动展平为 `[L1, *L2-children]` 传给 repo（`customer_id IN (...)`）。Repository 增加 `customer_ids_in: list[int] | None` 入参，与旧单值 `customer_id` 二选一。
    - **新增 sort key**：`PartSortKey` / `AssemblySortKey` 加 `SERIAL_NO / DRAWING_NO / NAME` 三项；列表列头点击即可排序。计划交期仍为默认（升序）。
    - **前端重写 `PartsList.vue` / `AssemblyList.vue`**：
      - 顶部只保留「图号/名称搜索 + Reset + 共 N 条」；CNC 编程员额外 banner；装配件列表新增「新建装配件」按钮。
      - 所有筛选下沉到列头 `el-popover`：状态（多选 + 仅加急）/ 客户（el-cascader，checkStrictly + emitPath）。
      - **draft → 确定/重置** 模式：popover 用本地 draft 缓冲，**确定** 才把 draft 拷到实际 filter 并发起 query；**重置** 立即清空并重查。注意：勿把 sentinel 字符串（如 `URGENT_ONLY`）混入 `statuses` 数组传入后端（曾触发 `is not a valid PartStatus` ValueError）——「仅加急」**必须**是独立 boolean checkbox。
      - 列重排：序列号 | 图号 | 名称 | 数量 | 计划交期 | 状态 | 客户 | 所在位置 | 操作（移除原「下一道工序 / 装配 / 加急独占列」）。
      - 加急行 `row-class-name="row-urgent"` → 红底 `#fde2e2`（与 `Dashboard.vue` 同款），跟现有表单的橙底 `#fdf6ec` 区分。
    - **装配件详情页承担关键操作**：`AssemblyDetail.vue` 加「取消装配件（CLERK+）」「删除装配件（MANAGER-only）」按钮；共用一个 `el-dialog` 输入 `asm.serial_no` 才可提交。`serial_no` 为 NULL（老数据）时按钮禁用 + tooltip 提示「该装配体暂无序列号，请在数据库手工处理」。
    - **API 权限收紧**：`POST /assemblies/{id}/soft-delete` 端点级 `dependencies=[require_role(MANAGER)]`，OVERRIDE 路由级 MANAGER+CLERK；CLERK 调用现 403。
    - **新增 composable**：`composables/useCustomerTree.ts` 抽取 cascader 树形构造，`PartBatchNew.vue` / `AssemblyCreate.vue` / `PartsList.vue` / `AssemblyList.vue` 共用。
    - TDD 红 → 绿 三步走全在 commit `feat(parts/assembly):` 内；`tests/unit/test_assembly_serial.py` 9 个 + `test_assembly_service.TestAssemblySerial*` 4 个 + `test_part_service_query_crud.TestListParts` 重写并扩 5 个用例。`uv run pytest tests/unit` → 231 passed；`npm run build` 通过。

12. **2026-07-08 申请人补全切换 el-select → el-autocomplete**
    - `composables/useApplicantSearch.ts` 暴露 `querySearch(queryString, callback)` 给 `el-autocomplete` 的 `:fetch-suggestions`，客户端同步子串过滤已缓存的 200 条申请人，**不**触发网络请求。
    - `PartBatchNew.vue` / `AssemblyCreate.vue` 把原来的 `el-select filterable + el-option` 模板换成 `el-autocomplete`；`:debounce="0"` 避免叠加 Element Plus 默认的 300ms 防抖（纯内存过滤场景不需要）。
    - `frontend/src/components.d.ts` 自动重生成（`ElAutocomplete` 已注入），无需手改。
    - `el-autocomplete` 未在 `element-plus` skill `references/` 列出的高频子集内 → WebFetch 官方文档（`https://element-plus.org/en-US/component/input` 的 `Autocomplete` 段）确认 `value-key` / `:fetch-suggestions` / `@select` 用法。

13. **SQLAlchemy 异步 `MissingGreenlet` 陷阱**（2026-07-08 踩坑）：症状
    ```
    sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called;
    can't call await_only() here. Was IO attempted in an unexpected place?
    ```
    含义：项目用 SQLAlchemy 2.0 异步 + asyncpg，所有 DB 操作必须在 `async` 函数里 `await`。
    任何**同步函数**（包括 Pydantic 校验器 / `from_attributes=True` 反序列化 / `@property` / `__repr__`）
    都不能触发 `await` 或访问需要 DB IO 的字段，否则会抛此异常。

    常见诱因（务必避开）：
    - **`from_attributes=True` 反序列化** ORM 对象：构造 Pydantic 响应模型时
      `AssemblyOut(asm)` 会访问 `asm.id` / `asm.serial_no` 等列；这些列如未在当前
      session 中显式 `await self.session.refresh(...)`，可能需要 lazy load 触发 IO。
      **正确做法**：始终用关键字传参（`AssemblyOut(id=asm.id, ...)`），不要 `from_attributes`。
    - **访问 `lazy="raise"` 关系**（如 `TPart.customer`）：抛 `InvalidRequestError`，
      但若关系触发器内部有 IO，错误会变成 `MissingGreenlet`。**不要**在 service
      层用 `part.customer.name`，显式 `await self.customers.get_by_id(part.customer_id)`。
    - **在 `field_validator` / `field_serializer` 中访问 ORM 字段**：Pydantic 校验器跑在
      同步上下文，访问未加载的列会抛 `MissingGreenlet`。校验器只做值变换（strip /
      str / enum 转换），不查 DB。
    - **将 ORM 对象传给 `jsonable_encoder` / `model_dump_json` 之前**未确保属性已加载：
      `await self.session.flush()` 后属性才会从 server_default 填回本地对象。
    - **新写 ORM 字段忘了给 server_default 或 `default=`**（如 `created_at` / `updated_at`）：
      flush 后访问该字段会触发 refresh。
    - **`onupdate=func.now()` 列被 UPDATE 触碰后访问**：`AuditMixin.updated_at` 是
      `onupdate=func.now()`。如果对 ORM 对象做了任何 UPDATE（哪怕只动了一列），
      SQLAlchemy 都会把 `updated_at` 标 expired 以便刷新回填。在 async session
      里这个 lazy load 触发的同步 IO 直接抛 `MissingGreenlet`——而且**不一定在
      `flush()` 内立即抛**，也可能延后到下次 `asm.updated_at` 访问才爆。
      **正确做法**：尽量不要走「先 INSERT 再 UPDATE 同一行」的两阶段写；构造
      对象时就把所有列都填好（连 `serial_no` 这种后续才知道的，也应该在 INSERT
      之前先 `acquire_serial` 再构造），单次 INSERT 完成。详见 [item 15](#15-sqlalchemy-onupdate--missinggreenlet--2026-07-08)。
      `expire_on_commit=False` **不**防这种 expire（它只防 commit）。

    排查方法：搜 `lazy=`、搜 `from_attributes=True`、搜 `@property` 里的 ORM 字段访问、
    搜 `field_validator`/`field_serializer`。**最关键**：所有 DB 操作必须 `await`，
    `await self.session.flush()` 之后才能读 server_default 列。

    项目里出现过的真实案例：
    - `service/assembly.py::add_child` 曾把单个 `TPart` 直接传给
      `self.part_service._to_out(...)`（签名是 `list[TPart]`），导致后续
      `cust_map.get(p.customer_id)` 等访问触发错误。修复：包成 `[tpart]` 列表。

14. **2026-07-08 装配体创建流**（统一记录于此）：
    - 详情见 [第 11 节 /assemblies](#assembliesapiv1assemblypy--3-router)：
      「创建空装配体」「详情页上传总装 PDF（自动按页拆分）」「详情页单独添加子件」
      三种模式。`service/assembly.py` 三个公开方法：`create_assembly`（可选 PDF）、
      `upload_total_pdf`、`add_child`。
    - 新增 `t_part.customer_id` 必须是 `int`（雪花 ID int，序列化到 JSON 时转 str），
      之前 `service/assembly.py` 传 `data.customer_id`（str）会导致 asyncpg 报
      `'str' object cannot be interpreted as an integer`。统一改用 `parse_snowflake_id`
      转出来的 `cid`（int）。
    - `AssemblyCreateRequest.children` 改为 `default_factory=list`（允许空 → 创建空装配体）；
      若同时提供 `data.children` 和 `pdf_bytes`，后端校验 `len(children) == page_count - 1`。
    - `TDrawingFile.page_index` 在创建流程中永远是 `NULL`（每行就是该子件的单页 PDF，
      不再通过 `page_index` 引用 master）。老装配件行（`page_index=2..N`）继续可读可预览。
    - `t_part.unit_price` / `t_part.total_price` 装配体创建时一律写 0；详情页手工添加的
      子件同样写 0。价格字段对文员是隐藏的；后续单独加价编辑入口再说。
    - 装配体创建成功后跳哪儿？
      - 一次性传 PDF → `/assemblies?status=PENDING`（列表核对刚创建的）
      - 空装配体 → `/assemblies/{id}`（到详情页用「上传总装 PDF」/「添加子件」补充）

15. **SQLAlchemy `onupdate` 触发的 `MissingGreenlet`**（2026-07-08 踩坑 + 修复）：
    - **症状**：`POST /api/v1/assemblies`（带 PDF + children）抛
      `sqlalchemy.exc.MissingGreenlet`，堆栈停在 `service/assembly.py`
      `_assembly_to_out_obj` 里的 `updated_at=asm.updated_at,`（[item 13] 提到的
      `_load_expired` 同步 IO 链路）。
    - **根因**：`service/assembly.py::create_assembly` 原实现是两步走写装配体：
      ```python
      assembly = TAssembly(..., status="PENDING")  # 没 serial_no
      await self.assemblies.create(assembly)        # 第一次 flush (INSERT)
      ...
      assembly_serial = await self.serial_counters.acquire_serial(code)
      assembly.serial_no = assembly_serial
      await self.assemblies.session.flush()         # 第二次 flush (UPDATE serial_no)
      ```
      `AuditMixin.updated_at = mapped_column(..., onupdate=func.now())`。
      SQLAlchemy 2.0 看到 UPDATE 涉及 `onupdate` 列，flush 时会把它标 expired
      以便刷新回填。async session 里这个 refresh 触发同步 IO → `MissingGreenlet`。
      **不一定在 `flush()` 内立即抛**，也可能延后到下次属性访问才爆（视 session
      flush 模式而定）。`expire_on_commit=False` 不防这种 expire。
    - **修复**：把 `parent` / `code` / `acquire_serial` 挪到 `TAssembly(...)` 之前，
      `serial_no=assembly_serial` 在构造时就带进去；删除那次多余的
      `await self.assemblies.session.flush()`，单次 INSERT 搞定。
      `server_default=func.now()` 走 PostgreSQL RETURNING 一次性把 `created_at` /
      `updated_at` 填回 Python 对象，没有 onupdate expire 触发。
    - **约束（今后写 service 务必遵守）**：
      - **任何带 `AuditMixin` 的 ORM 都不应该走「先 INSERT 再 UPDATE 同一行」
        的两阶段写**。如果某个列后续才知道，**先在 Python 侧解析完再构造对象
        一次性 INSERT**；不要先 INSERT 一行大部分列 NULL / 默认值，再 UPDATE
        补字段。
      - 配套：「先 acquire serial 再 INSERT」也是为了避免 UPDATE partial unique
        索引的行（原 `t_assembly.serial_no` partial unique，UPDATE 改 serial_no
        会瞬时破索引唯一性，理论上并发场景有竞态）—— 一并消除。
    - **次要修正**：`TAssembly.customer_id` 之前传 `data.customer_id`（schema
      端是雪花 ID 字符串）会触发 asyncpg 报 `'str' object cannot be interpreted
      as an integer`。统一改用 `parse_snowflake_id` 转出来的 `cid`（int）。
    - **端到端验证**：
      - 最小复现脚本：原两阶段「先 INSERT 无 serial 再 UPDATE 填 serial」必抛
        `MissingGreenlet`；修复后单次 INSERT 带 serial，访问 `updated_at` 正常
        返回 `2026-07-08 12:31:41.258440`。
      - `uv run pytest tests/unit/` → **250 passed**。
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
│   └── versions/                  # 3 层目录（schema/dev_data/prod_data，详见 §15）
│       ├── schema/                # 纯 DDL：001-005 + 013
│       ├── dev_data/              # dev 种子：006-011
│       └── prod_data/             # 生产种子：012 + 014
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
| POST | /assemblies/{id}/soft-delete | soft_delete_assembly | M | 级联软删装配体+子件+文件（MANAGER-only；端点级 override 路由级 M,C） |
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
| POST | /parts/{id}/drawings | upload_part_drawing | M,C | 上传图纸（PDF + 9 种图片，2026-07-14 扩）|
| POST | /parts/{id}/3d-models | upload_part_3d_model | M,C | 上传 3D 模型（STEP/STP/IGES/IGS/STL/OBJ/3MF）|
| POST | /parts/{id}/cad-files | upload_part_cad_file | M,C | 上传 CAD 源文件（DWG/DXF，2026-07-14 新增）|
| GET | /parts/{id}/files | list_part_files | M,C,CNC | 列出文件（kind 可选过滤）|
| GET | /files/{id}/download-url | get_download_url | M,C,CNC | 签发临时下载 URL |
| GET | /files/{id}/content | get_file_content | M,C,CNC | 后端代理预览/下载 |
| POST | /files/{id}/delete | delete_file | 按 file.kind 自动派 | 软删+COS 异步清理 |

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
| **TPartFile** | t_part_file | polymorphic `part_id`(=t_part.id 或 t_assembly.id)；kind(DRAWING/3D_MODEL/G_CODE/SETUP_SHEET/ASSEMBLY_MASTER/CAD_2D)；file_type, object_key(COS CAS key), original_filename, file_size, content_type, content_sha256(CHAR(64) NULL, 2026-07-14), upload_status(READY) |
| ~~TDrawingFile~~ | ~~t_drawing_file~~ | 2026-07-10 起合并到 t_part_file；旧表保留但无 writer |
| ~~TCncProgram~~ | ~~t_cnc_program~~ | 同上 |
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
1. 创建装配体（POST /assemblies，multipart：JSON+PDF）→ 一次性 `acquire_serial(code)` 给装配体分配顶级流水号 `L1067`；子件派生为 `L1067-01 / L1067-02 / ... / L1067-99`（两位零填充，上限 99）；自动创建子零件 + 上传 PDF→COS + 写 drawing_file 行
2. 任一子件进入生产 → Assembly 自动 IN_PROCESS（PartService 状态机回调触发）
3. 全部子件 COMPLETED → Assembly 自动 COMPLETED
4. 取消 → Assembly CANCELLED（`serial_no` 释放回池；级联取消所有非终态子件）

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

---

14. **2026-07-09 一级客户序列号前缀（A-Z）可编辑 + t_serial_counter 预置 26 行（修 20108 BIZ_SERIAL_PREFIX_UNKNOWN）**：
    - **新功能**
      - `t_customer` 新增 `serial_prefix String(1) nullable`（schema/013）：一级客户必填 A-Z 单字符，叶子客户 NULL 继承父。
      - DB 层：`ck_t_customer_serial_prefix_uppercase`（`serial_prefix IS NULL OR serial_prefix ~ '^[A-Z]$'`）+ 部分唯一索引 `uq_t_customer_root_prefix`（仅约束未软删的根客户，避免二级或已删的客户干扰）。
      - 历史回填：迁移 013 内置 `UPDATE` 把 法拉电子/路达/宏发 三个一级客户的 prefix 分别置 F/L/H（与原 `PARENT_TO_CODE` 兜底一致）。
      - `t_serial_counter` 预置 A-Z 全 26 行（prod_data/014）：`INSERT ... SELECT chr(ascii('A') + i), 0 FROM generate_series(0, 25) i ON CONFLICT DO NOTHING`，幂等。任意字母可立即用，**不再触发 20108**。
      - 后端新增 `core.serial.resolve_root_prefix(root_customer)` helper：DB 列优先，`PARENT_TO_CODE` 兜底（保留兼容回退，已标 deprecated）。所有 4 个 `acquire_serial(prefix)` 调用点（`service/part.py::create_part` + `create_parts_batch` 缓存键改为 root_id；`service/assembly.py::create_assembly` + `upload_total_pdf` + `add_child`）统一改用 helper，无新 DB 调用。
      - `CustomerService.create_customer`：一级客户未带 `serial_prefix` → `BIZ_INVALID_VALUE 400`；叶子客户忽略 payload 里的 `serial_prefix`。
      - `CustomerService.update_customer`：仅在 payload 显式给出非 None 时更新；DB `IntegrityError`（撞部分唯一索引）→ `BIZ_INVALID_VALUE 409`。
      - 前端 `CustomerList.vue` 弹窗新增 `序列号前缀` `el-select` A-Z（`el-form-item`，运行时生成 26 个 option 而非硬编码）；仅一级客户启用 + 必填，叶子客户禁用。树行根节点名前挂 `el-tag` 显示当前 prefix，未设置时显示灰色「未设置」。
      - 权限沿用 `CustomerService` 的 MANAGER + CLERK 写权限；无新增角色约束。
    - **修改后行为**：已生成的 `t_part.serial_no` / `t_assembly.serial_no` **不变**（列在创建时一次性写入并随状态机流转，事后不回填）；只有后续新建的零件 / 装配体会用新前缀。
    - **约定（写入 §1 后续参考）**：任何新增的「序列号前缀」字段都应改 `t_customer.serial_prefix` + 调 `resolve_root_prefix`，**不要**再写 `PARENT_TO_CODE` 硬编码映射。`core/serial.py::code_for_parent` 标 deprecated，仅作迁移兜底保留。
    - **端到端验证**：`uv run alembic upgrade head` 14 步线性成功；`SELECT count(*) FROM t_serial_counter` = 26；`SELECT name, serial_prefix FROM t_customer WHERE parent_id IS NULL AND deleted_at IS NULL` = 3 行 (F/L/H)。新建 root「测试客户A」prefix=G → 200；建零件 → serial_no=G1000；编辑 prefix G→Z → 200；再建零件 → serial_no=Z1000；之前 G1000 零件 serial_no 不变。`uv run pytest tests/unit` → **259 passed**；`npm run build` 通过。

---

## 15. Alembic 迁移（2026-07-10 squash 为两个文件）

历史上迁移分三层（schema / dev_data / prod_data）共 20 步，其中 dev_data 是假数据种子（50 假零件 / 20 假工人 / 20 假客户 / changeme dev 账号），且后端 `Dockerfile` 启动即跑 `alembic upgrade head`——生产库会被灌进假数据。**2026-07-10 起把整条链 squash 为两个文件**，删除全部 dev 假 seed：

| 文件 | revision | down | 内容 |
|------|----------|------|------|
| `schema/000000000001_schema_init.py` | `000000000001` | base | **唯一 DDL 迁移**：一次建全部 17 张表 + 索引 + 约束（等价于旧 001-005/013/015/016/020 叠加后的最终 schema）。文件表已统一为 `t_part_file`（无 `t_drawing_file` / `t_cnc_program`）；`t_customer.id` 用 `autoincrement=False`（不建 sequence，全表雪花 ID）。 |
| `prod_data/000000000002_data_init.py` | `000000000002` | `000000000001` | **唯一数据种子**：9 工种 / 19 真实工人 / 6 账号（admin·MANAGER + 陈燕·翁美月·CLERK + 童敏华·CNC_PROGRAMMER + 黄道玉·曾学辉·INSPECTOR，密码 changeme）/ 22 菜单 / 35 role_menu / `t_serial_counter` 预置 A-Z 全 26 行。全部 ON CONFLICT 幂等，**无任何假数据**。 |

`alembic.ini` 的 `version_locations` 现为 `schema:prod_data`（已移除 dev_data），`recursive_version_locations = true` 保留。

### 15.1 升级命令（dev / prod 统一）

```bash
uv run alembic upgrade head      # 2 步：建 schema → 灌必要数据
```

- `alembic heads` 只返回 1 行（`000000000002`）；`alembic history` 只有 2 条。
- 冷启结果：`t_work_type`=9, `t_worker`=19, `t_user`=6, `t_user_role`=6, `t_menu`=22, `t_role_menu`=35, `t_serial_counter`=26，其余业务表（part/customer/assembly/applicant）=0。
- 后端容器 `Dockerfile` 的 `CMD alembic upgrade head && uvicorn ...` 无需改，现在自动产出干净 prod 库。
- **验收门（改 schema_init 时务必复跑）**：全新库 `upgrade head` 后 `pg_dump --schema-only --no-owner --no-privileges`，与「旧链 prod 路径」的 schema dump diff，唯一允许差异是 `t_customer_id_seq` 及其 `DEFAULT nextval(...)` 被移除。

### 15.2 已有库怎么办

已按旧链迁到 head=`000000000020` 的库（老 dev / 老 prod），新链 revision id 变了，`alembic_version` 指向的 020 不复存在。确认其 schema 与新 `schema_init` 等价后，`alembic stamp 000000000002` 即可对齐；本次改动主要面向**全新 prod 库冷启**。dev 本地若要假数据，另写独立 seed 脚本（不走迁移）。

### 15.3 新 schema 迁移加在 schema/ 子目录

- revision id 用下一个 12 位零填充数字（如 `003` 在 `001` 之后）。
- `down_revision` 指向 **schema 层 head**（如 `003 → 001`），不指向 `prod_data/002`。
- 拓扑：`schema/001 → schema/003` + `schema/001 → prod_data/002` 两条平行 branch；`alembic heads` 返回 003 + 002 两条 head。
- `alembic upgrade head` 会因多 head 报错，须显式指定：`alembic upgrade 000000000003` + `alembic upgrade 000000000002`（或维护脚本分别跑）。
- docstring 固定格式：模块 docstring + Revision ID / Revises / Create Date + 简短说明。

> **2026-07-11 修订**：squash 到 `000000000001_schema_init.py`（commit `14b0e5f`）时，
> 原 §17.B 描述的 `000000000003_add_part_event_operator.py`（事件表加 `created_by` 列）
> **已直接合入 schema/001**，所以当前 `alembic versions/schema/` 只有 `000000000001_schema_init.py`
> 一个文件，`alembic heads` 只返回 `000000000002`（prod_data）。§17.B 的「多 head 拓扑」是
> squash 前的历史描述，与当前磁盘状态不符；如未来真的新增 003 迁移，再恢复上文多 head 写法。

---

## 16. JWT 双 token 自动刷新（2026-07-10 接入）

解决用户反馈「操作到一半 token 过期跳回登录页」—— 引入业界标准的 **access + refresh 双 token + 轮转** 方案。

### 16.1 token 分类

| Token | TTL | 用途 | Payload 关键字段 |
|---|---|---|---|
| **access token** | dev 720 min / prod 2880 min（保持不变）| 业务请求携带 | `sub`, `username`, `roles`, `shelf_ids`, `type="access"`, `iat`, `exp`, `iss`, 可选 `shelf_wildcard` |
| **refresh token** | 默认 7 天（10080 min，通过 `JWT_REFRESH_TOKEN_EXPIRE_MINUTES` 覆盖） | 仅用于换新 access；轮转 | `sub`, `type="refresh"`, `ver=<当前 t_user.refresh_token_version>`, `iat`, `exp`, `iss` |

新错误码 `BIZ_AUTH_REFRESH_INVALID = 40103`：refresh token 失效 / 类型不匹配 / 版本落后 / 用户已停用。

### 16.2 关键文件

| 文件 | 改动 |
|---|---|
| `alembic/versions/schema/000000000001_schema_init.py` | t_user 的 `refresh_token_version` 列（NOT NULL DEFAULT 0）已并入 squash 后的唯一 schema 迁移（原 020 迁移文件已删）|
| `model/user.py` | + `refresh_token_version` 字段 |
| `repository/user.py` | + `increment_refresh_token_version(user)` |
| `core/error_code.py` | + `BIZ_AUTH_REFRESH_INVALID = 40103` |
| `core/config.py` | + `jwt_refresh_token_expire_minutes`（默认 10080） |
| `core/security.py` | 重构：`_build_token_claims` 共用工厂；`create_access_token` 加 `type="access"`；新 `create_refresh_token`（带 ver）；`_decode_token(expected_type=)` 共用解码器，对**老 access token 无 type 字段**保持兼容 |
| `schema/user.py` | `LoginResponse.refresh_token: str`（新增必填） |
| `service/auth.py` | `_build_token_pair` 抽取（login + refresh 复用）；`refresh(refresh_token_str)` 新方法 |
| `api/v1/auth.py` | + `POST /auth/refresh`（公开端点，body `{refresh_token}`） |

### 16.3 轮转机制（关键不变量）

`AuthService.refresh()` 调用顺序**严格**：
1. `decode_refresh_token` → 失败抛 40103；
2. `users.get_by_id(sub)` → 不存在 / 软删 / 停用 → 40103；
3. 比对 `u.refresh_token_version == payload.ver` → 落后 → 40103；
4. 重新读 roles/shelf_ids/menus（反映最新 DB）；
5. **`users.increment_refresh_token_version(u)` 必须先于 `_build_token_pair`**——先让旧 refresh 失效（DB +1），再用新 ver 签发新 refresh；
6. 返回新 LoginResponse。

⚠️ **集成测试发现的真实 bug**：把 `increment_refresh_token_version` 放在 `_build_token_pair` 之后 → 新 refresh token 仍带旧 ver → 下次刷新立刻被自己顶掉。已修复（`service/auth.py`）。

### 16.4 前端：axios 拦截器（`frontend/src/api/http.ts`）

**两路刷新 + 一个 stampede 队列**：

- **Reactive**：响应拦截器收到 `code === 40102`（access 过期）→ 触发 `getOrCreateRefresh()` → 拿到新 access → 用 `_isRetryAfterRefresh` 标记重试原请求（防递归）。
- **Proactive**：每次成功响应都 `decodeJwt(token).exp - now` < 5 分钟 → fire-and-forget 调一次 refresh（30s 节流避免短时间内反复刷）。绝大多数 40102 在 proactive 阶段就拦下了。
- **Stampede 队列**：模块级 `refreshPromise: Promise<LoginResponse> | null`。首个 40102 创建 promise；后续 40102 复用同一个 promise（`finally` 里 `setTimeout` 清空）。同一时刻 N 个并发 401 → 只发 1 个 `/auth/refresh`。

**专用 refresh 客户端**（`refreshClient`，独立 axios 实例，**无任何拦截器**）：避免响应拦截器里的 40102 → refresh → 拦截器递归。

**失败兜底**：`doRefresh()` 失败 → `window.dispatchEvent('auth:logout')` → `main.ts` 监听 → `router.replace('/login')`。

**状态同步**：拦截器 `persistTokens(pair)` 写 localStorage + dispatch `auth:tokens-refreshed` → `useAuthSession.ts` 监听 → 同步 module-level refs（避免组件层看到陈旧 token）。

### 16.5 测试覆盖

- `tests/unit/test_security.py`（**新，13 用例**）：token 工厂端到端、解码兼容、过期 / 篡改 / 错误 issuer 失败路径
- `tests/unit/test_auth_service.py::TestRefresh`（**新，7 用例**）：happy path / 篡改 / access 当 refresh / 用户停用 / 软删 / **轮转 / 版本 mismatch / 降级生效**
- `tests/test_auth_refresh.py`（**新，7 用例**，走真实 PG）：login 返回双 token、refresh 真实轮转、access 当 refresh、用户停用后 refresh 失败、降级后再 refresh roles 反映最新

端到端：`uv run pytest tests/unit/ tests/test_auth_refresh.py` → **347 passed**；`cd frontend && npm run build` 通过。

### 16.6 兼容与回滚

- **老 access token 无 `type` 字段**：`_decode_token` 兼容视为 `access`；`refresh` 端**严格**校验 `type="refresh"`，老 token 不能蒙混。
- **回滚**：DB 列可 drop（`refresh_token_version` 默认 0，回滚无破坏）；前后端代码 git revert 即可，老用户凭 `auth_session` 里只剩 token 的旧 storage 仍能跑（`refresh_token` 缺省当 null）。

### 16.7 用户决策记录

- access token TTL **保持 12h / 48h 不动**（dev / prod 各自现有值；零破坏）
- refresh token TTL = **7 天**（10080 min）
- **启用 refresh token 轮转**（每次成功 refresh → 旧 refresh 立即失效）

---

17. **2026-07-10 一波整合改动（时间 + 操作者 + 备注 + RETURN 新流程）**：

    **A. 时间统一到 Shanghai**
    - 新增 `core/time.py::now_naive()` helper：返回 Asia/Shanghai 当前时间的 naive datetime；`SHANGHAI_TZ = timezone(timedelta(hours=8))` 显式构造，**不**依赖环境 TZ。
    - 新增 `now_shanghai_iso()` helper：WS 推送用，输出 `YYYY-MM-DDTHH:MM:SSZ`。
    - 18 处 `datetime.utcnow()` / `_dt.utcnow()` 改为 `now_naive()`：`repository/{shelf,applicant,part_file,worker,work_type_process,customer,assembly,user,shelf_process,work_type,process,part}.py`（含 `last_login_at`）、`service/{worker,auto_complete}.py`、`statemachines/part.py:133` (`placed_at`)。
    - `service/auto_complete.py:49` threshold 改 `now_naive()`（与 DB `now()` 同源，避免自动完成晚 8h 触发）。
    - `service/dashboard.py:179` + `api/v1/ws.py:90,169` WS `ts` 字段改 `now_shanghai_iso()`。
    - DB 列保留 `timestamp without time zone`（naive），**不**改 `timestamptz`。
    - 历史 `deleted_at` / `last_login_at` / `placed_at` 错位 8h **不 backfill**（单条 SQL 修正即可）。
    - `tests/unit/test_time.py`（新，5 用例）：naive、UTC+8、TZ 独立、SHANGHAI_TZ 常量、ISO 格式。

    **B. 事件操作者字段（沿用 AuditMixin `created_by` 命名）**
    - `t_part_event` 新增 `created_by` (BigInteger, nullable) — 与 AuditMixin 的 `created_by` 命名对齐；与 `created_at` 形成语义对偶。
    - **不**冗余 username：list_events 通过 JOIN t_user 现算，避免列表展示 N 次 JOIN 单独建索引的代价。
    - 迁移：`alembic/versions/schema/000000000003_add_part_event_operator.py`，`revision="000000000003"` + `down_revision="000000000001"`（**schema 层 001**，不指 prod_data/002）。
    - 多 head 拓扑：`schema/001 → schema/003` + `schema/001 → prod_data/002` 两条平行 branch；`alembic heads` 返回 003 + 002。
    - 状态机 `statemachines/part.py` 13 个 `on_*` 回调全部加 `created_by: int | None = None,` kwarg 并写入 `TPartEvent`。
    - `service/part.py::_write_event` 加 `created_by` kwarg；`__init__` 缓存 `self._user_id` + `self._username`；14 处状态机调用点 + `_write_event CREATED` 全部显式传 `created_by=self._user_id`。
    - `service/assembly.py` 2 处 `TPartEvent`（`create_assembly` 子件 + `_create_single_child`）+ `__init__` 缓存 `_user_id` / `_username` 同步。
    - `schema/part.py::PartEventOut` 加 `created_by: IdStr` + `operator_username: str | None`（后者是 list_events 通过 `TUser` JOIN 现算的展示字段，不进 model）。
    - `service/part.py::list_events` 一次性 `select(TUser.id, TUser.username).where(TUser.id.in_(operator_ids))` 拼 `user_map`，构造 PartEventOut 时填 `operator_username=user_map.get(e.created_by)`。
    - 前端 `PartDetail.vue` `event-line-1` 在 `worker_name` 后加 `v-if="evt.operator_username"` 显示操作者（Setting icon）；`frontend/src/api/parts.ts::PartEvent` interface 加 `created_by` + `operator_username`。
    - 历史事件 `created_by` 全 NULL（前端兜底不显示）。

    **C. 备注中文化 + 去背景**
    - `on_place_on_shelf` / `on_release_from_programming` note 模板：`f"shelf={c}; next_process={c}"` → `f"下发货架：{c} 下一工序：{c}"`（空格分隔）。
    - `on_return_to_shelf` note：`f"from={p}; to={n}; by={w}"` → `f"从工序 {p} 放回到工序 {n}（{w}）"`。
    - `on_inspect` note：`f"to inspection shelf {c}"` → `f"送检到货架：{c}"`。
    - `on_fail_inspection` note：`f"back to shelf {c}"` → `f"打回到货架：{c}"`（c 空时显示「打回到原货架」）。
    - 前端 `.event-note` CSS 去 `background: #fdf6ec` + `padding` + `border-radius`（仅 `color` + `font-size` + `margin-top`）。
    - 前端 `types/parts.ts::PartEventType` union 补 `'INSPECTION_FAILED'`；`PART_EVENT_LABEL` 加 `INSPECTION_FAILED: '品检打回'`；`PART_EVENT_TAG_TYPE` 加 `INSPECTION_FAILED: 'danger'`。
    - `PartDetail.vue` icons import 补 `Setting`。

    **D. 扫码台 RETURN 新流程**
    - 新建 `frontend/src/views/scan/ScanReturnParts.vue`（仿 `ScanPickParts.vue` 范式）：列持有件 → 选件 → 弹工序 picker（el-radio-group）→ 弹 `ShelfPickerDialog` → 提交 `scanPart({event_type:'RETURNED', ...})` → 自动 refresh。
    - 后端新增：
      - `repository/part.py::list_held_by_worker(worker_id, include_deleted=False)`：status=IN_PROCESS + location=WORKER + current_holder_id=worker_id，排序 `is_urgent DESC, planned_delivery_date ASC, id DESC`。
      - `service/part.py::list_parts_held_by_worker(worker_id)`：worker_id=0/None 短路返 []。
      - `api/v1/part.py::GET /parts/by-worker/{worker_id}`：`require_auth()`，路径参数 `worker_id: str` 用 `parse_snowflake_id` 转 int。
    - 前端 `api/parts.ts::listPartsHeldByWorker(workerId)` wrapper（URL encode workerId）。
    - 路由 `frontend/src/router/index.ts` 新增 `/scan/return` → `ScanReturnParts.vue`（继承 `menuCode: 'scan_badge'` 守卫）。
    - `ScanActionPicker.vue::selectAction` RETURN 跳转 `router.push('/scan/return')`（替换原 `/scan/parts?action=return`）。
    - 老 `ScanPartsWork.vue` RETURN 流程**替换不保留**；INSPECT 流程仍走 `ScanPartsWork.vue`。
    - `tests/unit/test_part_service_query_crud.py::TestListPartsHeldByWorker`（新，3 用例）：worker_id=0 短路、worker_id=None 短路、happy path。

    **端到端**：`uv run pytest tests/unit/` → **358 passed**；`cd frontend && npm run build` 通过。
    **alembic 拓扑**：`schema/001 + schema/003 + prod_data/002` 三文件，多 head；`alembic heads` 返 003 + 002。

18. **2026-07-14 COS 命名 + 内容去重 + 文件格式扩展（PDF + 9 种图片 + 3D 6 种 + CAD 2D）**：

    **A. COS 命名（CAS + 文件名内嵌，便于 DB 丢失时人工恢复）**
    - 新模板：`{prefix}{owner_kind}/{owner_id}/{KIND}/{sha16}_{safe_filename}`，例如
      `drawings/part/199852260920918016/DRAWING/3a7f4b2c9e1d8f06_pulley_bracket_v2.pdf`
      `drawings/assembly/199852260920918017/ASSEMBLY_MASTER/2d8e1c4f9a3b7056_master_v1.pdf`
      - `sha16` = SHA-256 前 16 个 hex 字符（64 位，用于人眼识别；DB 存完整 64 字符做精确去重）
      - `safe_filename` = ASCII 折叠（中文 / 特殊字符 → `_`）+ 保留扩展名
      - DB `original_filename` 仍保留完整 UTF-8 给 UI 显示
    - 新增 `core/file_hash.py`：`compute_sha256_hex(data)` / `safe_filename(name, max_len=80)` /
      `make_object_key(owner_id, owner_kind, kind, content_sha256, original_filename, ext)`
    - 历史 `object_key`（`drawings/part/{pid}/{file_id}.{ext}`）继续可用 —— service 直读
      DB `object_key` 直传 COS SDK，不解析格式。**项目未上线，无需做兼容代码**。

    **B. 内容去重（SHA-256）**
    - `t_part_file` 加 `content_sha256 CHAR(64) NULL` 列 + 部分唯一索引
      `uk_t_part_file_part_kind_sha`（`WHERE deleted_at IS NULL AND content_sha256 IS NOT NULL`）。
    - 上传流程：`hash → 查 (part_id, kind, sha) 活跃行 → 命中则复用（单文件 kind 改
      original_filename/updated_*/updated_by，跳过 COS PUT；G_CODE 多版本 no-op）→ 未命中走
      COS PUT + insert`。
    - **跨 part 不共享**：唯一索引在 `(part_id, kind, content_sha256)` 上；跨 part 同字节
      上传 → service 捕获 `IntegrityError` → 转 `BIZ_PART_FILE_DUPLICATE 409`。
    - 新增错误码 `BIZ_PART_FILE_DUPLICATE = 21108`。
    - 新增 `repository/part_file.py::find_active_by_part_kind_sha(...)`。

    **C. 文件格式扩展（与 §11 PartFileKind 表对应）**
    - **DRAWING** 加 9 种图片格式：PNG / JPG / JPEG / GIF / BMP / TIF / TIFF / WEBP / HEIC。
      **图片与 PDF 同槽**（用户确认：「一个零件可能用不同形式的文件，但同一种类型应该唯一」），
      单文件覆盖语义。**图片打印背面也要打序列号**（沿用 `service/printing.py` 双面 PDF）。
    - **THREE_D_MODEL** 加 5 种：IGES / IGS / STL / OBJ / 3MF（保留 STEP / STP）。
    - **新增 kind `CAD_2D`**：DWG / DXF 源文件单文件 kind，与 DRAWING PDF 图纸生命周期分离。
      WRITE roles：MANAGER + CLERK。
    - `core/_file_kind_policy.py::ALLOWED_EXTS_BY_KIND` 集中维护扩展名白名单；
      `core/config.py::cos_allowed_types` 失效（保留字段向下兼容）。
    - `service/part_file.py::_EXT_TO_CONTENT_TYPE` 同步扩所有新扩展名（MIME）。

    **D. 打印服务（service/printing.py）扩图片格式支持**
    - `_detect_image_orientation` 对 PNG/JPG/GIF/BMP/TIFF/WEBP 全部由 pillow 原生支持；
      HEIC 走 `pillow_heif.register_heif_opener()` 运行时 try，缺失则降级到信息卡占位。
    - 信息卡占位提示文案更新为「PDF / PNG / JPG / GIF / BMP / TIFF / WEBP / HEIC 任一格式」。
    - `tests/unit/test_printing_service.py` 新增：每种图片格式 → 双面 PDF；PDF 走原路径；无图纸
      走信息卡；竖图保持 portrait 朝向。

    **E. 前端**
    - `frontend/src/types/part_file.ts::PartFileKind` union 加 `CAD_2D`；
      `PartFileItem` 加 `content_sha256: string | null`。
    - `frontend/src/api/assembly.ts` 加 `uploadPartCadFile(partId, file)`；
      `listPartFiles` kind union 同步。
    - `frontend/src/components/FileListCard.vue`：
      - `ACCEPT_BY_KIND` 扩 DRAWING（加 9 种图片）+ 3D_MODEL（加 5 种）+ CAD_2D；
      - `TITLE_BY_KIND` / `EMPTY_TEXT_BY_KIND` / `UPLOAD_LABEL_BY_KIND` 加 CAD_2D；
      - 新增 `isImage(t)` / `isHeic(t)` helpers + `IMAGE_TYPES` 集合；
      - 预览弹窗加图片分支：HEIC 走下载，其它图片走 `<el-image :preview-src-list>`；
      - 颜色：图片用绿色 `#67c23a`；3D 模型 / STEP/IGES 用蓝色 `#3a7bd5`；DWG/DXF 用橙色 `#ff9800`。
    - `frontend/src/views/parts/PartDetail.vue`：
      - 修 `.txt` bug（line 282 移除；后端拒收）；
      - 加 CAD_2D `FileListCard` 实例；
      - 新增 `cadFiles` ref + `fetchCadFiles()` + `uploadPartCadFile` 绑定。

    **F. 端点**
    - `api/v1/drawing.py` 加 `POST /parts/{part_id}/cad-files`（kind=CAD_2D，
      MANAGER + CLERK）。DRAWING 端点本身扩白名单接受 9 种图片格式即可，
      不再单建 IMAGE 端点。

    **G. 数据库迁移**
    - `alembic/versions/schema/000000000004_part_file_sha_and_kinds.py`：
      DDL 加 `content_sha256 CHAR(64) NULL` + 改 ck constraint（+CAD_2D）+ 新增
      `uk_t_part_file_part_kind_sha` + 重做 `uk_t_part_file_single`（+CAD_2D）。
      `down_revision = "000000000001"`（schema 层 head）。

    **端到端**：`uv run pytest tests/unit/` → **426 passed**（baseline 358 + 新 68 用例）；
    `cd frontend && npm run build` 通过。
    **alembic 拓扑**：`schema/001 + schema/004 + schema/003 + prod_data/002` 四文件，
    多 head（004 新分支 001 + 003）；`alembic heads` 返 004 + 003 + 002。

19. **2026-07-14 菜单权限重整 + 待编程一览页面**：

    **角色菜单改动**
    - **CLERK（文员）**：从「首页 + 订单管理组（5 子项）+ 待品检 + 生成送货单」扩到「首页 + 订单管理组 + 客户管理组（3 个）= 11 个 code」。此前 backend customer/applicant API 早已允许 CLERK，仅前端侧栏入口缺失。
    - **CNC_PROGRAMMER（编程员）**：从「首页 + 零件一览 + 车间」缩到「首页 + 待编程一览 = 2 个 code」（**严格**移除 parts_list / floor_group / shelves_list；scan_badge 在 003 已删）。
    - **MANAGER / INSPECTOR / SHELF_ACCOUNT**：未变（MANAGER 21 个 = 全部菜单）。

    **种子改动**
    - `alembic/versions/prod_data/000000000002_data_init.py` 同步更新 `_CLERK_MENUS` / `_CNC_PROGRAMMER_MENUS` 常量（让全新冷启库开箱即用）。
    - **新建 `alembic/versions/prod_data/000000000005_role_menu_refine.py`**：
      * `revision = "000000000005"`，`down_revision = "000000000003"`。
      * 注意：原计划用 004，与既有的 `schema/000000000004_part_file_sha_and_kinds.py` 撞号 → 改 005。
      * upgrade()：CLERK 增 3 个客户管理 code（INSERT … ON CONFLICT DO NOTHING，按 code 反查 menu_id）+ CNC_PROGRAMMER 真删 3 个越权 code（DELETE FROM t_role_menu WHERE role='CNC_PROGRAMMER' AND menu_id IN :mids — 用 `IN` + `bindparam(..., expanding=True)` 模板，不是 `ANY(:mids)`）+ CNC 新增 pending_programming。
      * downgrade()：反向 CLERK 删 3 行 + CNC 删 1 行 + CNC 还原 3 行。
      * `_load_menu_ids(bind, codes)` helper：批量 SELECT id, code FROM t_menu WHERE code IN :codes AND deleted_at IS NULL。

    **前端改动**
    - `frontend/src/router/index.ts` 加 `/cnc/pending` 路由（`name: 'PendingProgramming'`，`menuCode: 'pending_programming'`，`icon: 'Cpu'`，`breadcrumb: [{ label: '待编程一览' }]`）。挂 MainLayout children 内。
    - **新建 `frontend/src/views/cnc/PendingProgrammingList.vue`**（首次引入 `cnc/` 目录；仿 `inspection/InspectionPending.vue` 范式）：
      * 顶部 filter-card：图号/名称 keyword + 手动刷新 + 自动刷新（10s，可选）+ 共 N 条。
      * el-table 列：serial_no / drawing_no / name（router-link 到详情）/ quantity / planned_delivery_date / customer_path / 操作。
      * 行操作三件套：详情（跳 `/parts/{id}`）/ 下发（弹 dialog）/ 文件（弹 drawer）。
      * 「下发到生产」el-dialog 同时选 PRODUCTION 货架（`listShelves({ zone: 'PRODUCTION', is_active: true })`）+ 下一道工序（`listProcesses({ limit: 200 })`）→ `releaseFromProgramming(partId, shelfId, processId)`。ElMessageBox.confirm 二次确认；后端 400 时 catch 显示。
      * 「文件」el-drawer（size 520px）并发拉 5 类文件（`listPartFiles(partId, kind)`：`DRAWING / 3D_MODEL / CAD_2D / G_CODE / SETUP_SHEET`），按 kind 分组渲染，每行一个「下载」按钮直接 `window.open(file.download_url)`（后端在 list 响应里同步签发 900s 临时 URL）。
      * 加急行整行红底 `#fde2e2`（与 PartsList / InspectionPending 同款 `:deep(.row-urgent)`）。
      * 复用：`listPendingProgramming` / `releaseFromProgramming` / `listPartFiles`（注：在 `@/api/assembly` 不在 `@/api/parts`）/ `listShelves` / `listProcesses` / `PartListItem` / `PartFileItem` / `Shelf` / `Process`。
      * 用到的 Element Plus 组件（**v2.14.2，与文档基准 2.14.1 一致**）：el-card / el-input / el-button / el-checkbox / el-tag / el-table / el-pagination / el-form / el-form-item / el-radio-group / el-radio / el-dialog / el-drawer / el-icon / el-empty / ElMessage / ElMessageBox。

    **端到端验证**
    - 冷启库（全新走 002 种子）：`uv run alembic upgrade 000000000002` → 已包含新映射。
    - 回填（已有 003 stamped 库）：`uv run alembic upgrade 000000000005` → 4 个新 INSERT + 3 个真删 CNC 越权，幂等。
    - **降级 + 重新升级 round-trip 已测**：downgrade 003 → upgrade 005 后，CLERK=11 / CNC=2（仅 home + pending_programming）/ MANAGER=21 / INSPECTOR=1 / SHELF_ACCOUNT=1，结果与首次一致。
    - `uv run pytest tests/unit` → **426 passed**（无新增测试；纯菜单/路由改动）。
    - `cd frontend && npm run build` → ✓ built in 5.45s（TS 0 错；仅 `@vueuse/core` 的 `/* #__PURE__ */` annotation 警告，与本改动无关）。
    - **手动验证（部署后）**：登录陈燕 / 翁美月（CLERK）→ 侧栏出现首页 / 订单管理（含 6 子项）/ 客户管理（含 2 子项），**无「权限管理」「设置」「车间」「待编程一览」**；登录童敏华（CNC）→ 侧栏**仅** 首页 + 待编程一览；CNC 进 `/cnc/pending` → 列表 / 详情 / 下发 / 文件 4 个动作全部可用。

    **注意事项**
    - alembic 单 head：2026-07-14 重新 squash 后 `alembic heads` 只返 `000000000002`；Dockerfile 的 `alembic upgrade head` 单数命令可直接跑。已不存在多 head 部署问题。
    - 缓存陈旧：用户改完菜单后未重新登录前，侧栏仍是旧菜单；接口调用权限本就 OK（backend 早已允许），重新登录即同步。
    - `listPartFiles` 的 import path：`@/api/assembly`（不是 `@/api/parts`，与 `uploadPartDrawing` / `uploadPart3DModel` / `uploadPartCadFile` 同模块）。

---

20. **2026-07-14 累计 push 整合（squash 后的落地）**：

    把 §18 的 part_file SHA/CAD_2D/格式扩展 + §19 的菜单重整 + 这一轮的
    其他零散改动（应标 Excel 导入、扫码台卡片化、vitest 接入）按 10 个
    atomic commit 推到 master：

    1. `chore(alembic): 重新 squash 迁移为 schema_init + data_init 两文件`
       — 把 003/004/005 全部合回 001/002，删 3 个独立文件；回到 CLAUDE.md §15 的两文件结构。
    2. `feat(backend): part file SHA-256 dedup + CAS object_key + CAD_2D + 图片格式扩展`
    3. `feat(backend): bulk-get-or-create 申请人（应标 Excel 导入后端）`
    4. `test(backend): part_file dedup + CAD_2D + 打印图 + file_hash + applicant bulk`
    5. `chore(frontend): vitest 配置 + 包优化 + ElImage 全局类型`
    6. `feat(frontend): FileListCard 扩图片预览 + CAD_2D kind + 详情页集成`
    7. `feat(cnc): 待编程一览页 + 路由 /cnc/pending（CNC 编程员专属）`
    8. `feat(parts): 应标 Excel 批量导入零件（bid 解析 + bulk applicant + 上传页）`
    9. `refactor(scan): 扫码台卡片布局统一 + 图纸/图片全屏预览弹窗`
    10. `docs: CLAUDE.md 同步 7-14 改动 + 部署路径修正`（本 commit）

    + 已知遗留 bug（不动）：`service/applicant.py::get_or_create` 在
    `IntegrityError` 后没用 `SAVEPOINT` / `begin_nested()`，并发 race 会
    `PendingRollbackError`。记入 follow-up，不在本 push 范围。

