# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

myERP —— 零件加工订单管理系统。覆盖法拉电子、路达两家一级客户及其下分厂/部门的零件下单、生产跟踪、交付闭环。

- 后端：FastAPI + SQLAlchemy 2.0 异步 + asyncpg + Alembic + Pydantic v2
- 前端：`frontend/`（Vue + Vite，单独管理）
- 数据库：PostgreSQL 18（`docker-compose.yml` 提供容器）
- 包管理：uv（依赖在 `pyproject.toml` / `uv.lock`）
- ID 方案：`utils/id_gen.py` 生成的雪花 ID；个别表（`t_customer`）用自增

## 架构总览

请求从 `api/` 进，按 `api → service → repository → model` 分层流转：

```
api/v1/*.py          # FastAPI 路由（薄）
   ↓ Depends
api/deps.py          # get_uow / get_*_service 依赖注入
   ↓
service/*.py         # 业务逻辑、状态机校验、抛 BizError
   ↓
repository/*.py      # 数据访问（每个聚合一个 Repository）
repository/unit_of_work.py   # UnitOfWork 聚合 Repos，同一会话
   ↓
model/*.py           # SQLAlchemy ORM
```

`core/` 放横切关注点：
- `database.py` — 异步 engine + `SessionLocal` + `get_db`
- `config.py` — `Settings`（pydantic-settings，从 `.env` 读）
- `exception.py` — `BizError`（业务异常，含 `ErrCode` 与 `http_status`）
- `error_code.py` — 业务错误码枚举
- `exception_handler.py` — 把 `BizError` 等转成统一响应
- `middleware.py` — `UnifiedResponseMiddleware`（统一响应包装）
- `response.py` — 统一响应结构

## 关键约定（务必遵守）

### 1. 数据库中禁止使用物理外键

所有跨表引用都是普通列 + 普通索引，**不**在 `model/*.py` 写 `ForeignKey(...)`，也**不**在 alembic 迁移里写 `sa.ForeignKey(...)`。

引用完整性、级联删除防悬空、防自环等由 **service 层** 校验：
- 写入前用 repository `get_by_id` 校验目标存在
- 删除聚合根前检查子记录
- `t_customer.parent_id` 写入前在 service 校验不会形成环

新加列如果逻辑上指向别表，照此办理；需要"防单行自引用"这种纯本地约束，用 `CheckConstraint` 即可（如 `parent_id IS NULL OR parent_id <> id`）。

### 2. 审计字段（Base 统一声明）

所有表通过继承 `model.base.Base` 自动获得 5 个审计字段，**不要**在子类重复声明：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `created_at` | DATETIME | 是 | `now()` | DB 默认值，应用层不必赋值 |
| `created_by` | BIGINT | 否 | NULL | 创建人 id（无用户体系时 NULL） |
| `updated_at` | DATETIME | 是 | `now()` on update | SQLAlchemy `onupdate` 自动维护 |
| `updated_by` | BIGINT | 否 | NULL | 最后修改人 id，由 service 显式赋值 |
| `deleted_at` | DATETIME | 否 | NULL | 软删标记，NULL = 未删除 |

约定：
- `Base` 用 `__abstract__ = True`，子类继承即获得全部字段。
- **禁止**直接 `session.delete()`；统一走 repository 的 `soft_delete(part)` 方法写 `deleted_at = utcnow()`。
- 默认查询条件是 `deleted_at IS NULL`；repository 已自动加。需要查全部时显式传 `include_deleted=True`。
- `created_by` / `updated_by` 由调用方（service 层）显式赋值；当前没有鉴权中间件时，按 NULL 处理。

### 3. ID 生成

- `t_part` 等业务表：雪花 ID，列定义为 `Mapped[int] = mapped_column(BigInteger, primary_key=True, default=new_id)`。
- `t_customer`：用户指定用自增 `autoincrement=True`。
- 雪花参数从 `.env` 读：`SNOWFLAKE_INSTANCE` / `SNOWFLAKE_SEQ` / `SNOWFLAKE_EPOCH`。

### 4. 状态机校验在 service 层

`PartStatus` 等枚举定义在 `model/enums.py`。状态流转规则（如 `t_part` 的 8 个状态之间的合法跳转）**不**在 DB 约束中实现，而是在 `service/part.py`（待写）中校验。允许的跳转规则参见 `docs/db-design-part-customer.md` 第 4.3 节。

### 5. 错误处理

业务异常用 `raise BizError(code=ErrCode.BIZ_xxx, message=..., http_status=...)`。
**不要**在 service 里 raise `HTTPException`，统一通过 `BizError` 走全局处理器。

### 6. 示例数据格式

`docs/example/` 下两份 Excel（法拉生产明细、路达加工明细）定义了真实业务字段语义；新加字段前先来这里对一遍。

### 7. 列表查询约定

`t_part` 列表查询统一走 `PartRepository.list_with_filters(...)`，**不**要散写 ad-hoc 查询。详见 `docs/db-design-part-customer.md` 第 4.5 节。

### 8. API 风格约定

**只使用 `GET` 和 `POST` 两种 HTTP 方法**：

| 方法 | 用途 | 参数位置 | 例子 |
|---|---|---|---|
| `GET` | 查询（无副作用） | URL query string + path | `GET /api/v1/parts?status=PENDING` |
| `POST` | 创建 / 状态变更 / 登录登出 / 任何需要 body 的请求 | URL path 表达动作 + JSON body | `POST /api/v1/parts`、`POST /api/v1/parts/{id}/change-status` |

约定：
- **不**使用 `PUT` / `PATCH` / `DELETE`。
- 路径用动词承载语义，避免 `/resource/{id}/status` 这种"操作对象式"路由：改成 `POST /resource/{id}/change-status`。
- 新增 action 类端点时，命名按 `change-*` / `assign-*` / `release-*` / `login` / `logout` 等动词短语。
- `WebSocket` 不受此约束（不是 HTTP 方法）。

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

`alembic/versions/<rev_id>_<slug>.py`，12 位 hex revision id（如 `a1f9c2d8e3b4`）。
模块顶部写明 `revision` / `down_revision` / `Create Date`，迁移开头用 docstring 说明要点（如"不使用物理外键"）。

## 现状注意

- `repository/part.py` 已重写：`create / create_many / get_by_id / list_with_filters / count_with_filters / update / soft_delete`，旧的 `list_by_order` / `list_by_worker` 已删除（对应表 `t_order` / `t_worker` 不再存在）。
- `repository/__init__.py` 已清理悬挂导出，只导出 `PartRepository`。`UnitOfWork` 待补。
- `model/__init__.py` 只导出 `Base / TCustomer / TPart / PartStatus / PART_STATUS_ENUM / PartSortKey / SortDir`。
- `t_part` / `t_customer` 当前对应的 alembic 迁移：`a1f9c2d8e3b4_create_t_customer_and_t_part.py`（尚未在 DB 上运行）。