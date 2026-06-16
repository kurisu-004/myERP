# 账号/权限模块 — 数据设计 v5

## 修订记录

| 版本 | 修订内容 |
|---|---|
| v4 → v5 | ① 恢复雪花 ID 主键（64 位 Long，PostgreSQL `BIGINT`），前端传输转字符串；② `t_permission` 精简为 `MENU/API` 两类（`BUTTON/DATA` 预留），不再融合四类；③ `t_user` 进一步精简：移除 `department_id`，纯登录表；④ 重写 ER 图 |

---

## 1. 业务概述

本模块负责系统**账号、组织、权限**的统一管理，是整个 ERP 的基础。

**核心职责**：
- 工厂组织架构（部门、工种）
- 系统账号（登录身份 + 人事档案分离）
- 工种兼任（一人多工种）
- RBAC 权限模型（User → Role → Permission）

**与生产业务模块的关系**：
- 业务表通过 `user_id`（`BIGINT`，雪花 ID）引用本模块的 `t_user.id`
- 业务表的 `created_by` 审计字段引用本模块的 `t_user.id`

## 2. 核心实体

| 实体 | 含义 | 主键策略 |
|---|---|---|
| Department (部门) | 工厂组织架构 | 雪花 ID |
| Position (工种) | 车/铣/磨/CNC 操机/CNC 编程/文员/经理/厂长/品检/司机 | 雪花 ID |
| DepartmentPosition (部门-工种) | 不同部门下设不同工种，多对多 | 雪花 ID |
| User (账号) | 系统使用者，登录身份 | 雪花 ID |
| UserProfile (档案) | 人事/薪资/医保/银行档案，1对1 | 与 user 同 ID |
| UserPosition (兼任) | 一个账号可兼任多个工种 | 雪花 ID |
| Role (角色) | 权限角色（超管/经理/文员/工人/品检/...） | 雪花 ID |
| Permission (权限点) | 菜单/API 两类（本阶段） | 雪花 ID |
| RolePermission (角色-权限) | 多对多 | 雪花 ID |
| UserRole (账号-角色) | 多对多，一个账号可多角色 | 雪花 ID |

**10 张表**

### 2.1 雪花 ID 方案

**位数**：64 位 Long（标准 Twitter Snowflake）

```
0 | 0000000 00000000 00000000 00000000 00000000 0 | 00000 00000 | 0000 00000000
1 |                  41 位时间戳(ms)               | 10 位机器  | 12 位序列
符号
```

| 段 | 位数 | 说明 |
|---|---|---|
| 符号位 | 1 | 固定 0 |
| 时间戳 | 41 | 毫秒级，可用 ~69 年（从配置起始时间算） |
| worker_id | 10 | 支持 1024 个节点，配置文件中指定 |
| 序列号 | 12 | 每毫秒每节点 4096 个 ID |

**PostgreSQL 存储**：`BIGINT`（有符号，因为 Long 转 BIGINT 时最高位是 0，不影响）

**前后端传输**：JSON 序列化为字符串
```json
{ "id": "1892345678123456789", "username": "zhangsan" }
```
后端 Go/Python/Java 用 `str(snowflake_id)` 或自定义 JSON 序列化器；前端 TypeScript 用 `string` 类型接收，避免 JS Number 精度丢失（JS Number 最大安全整数 2^53）。

**worker_id 分配**：配置文件动态指定

```yaml
# config/snowflake.yaml
snowflake:
  worker_id: 1            # 1~1023，单机部署固定为 1
  epoch: 1700000000000    # 起始时间戳(ms)，项目启动时确定
```

**生成器位置**：`utils/snowflake.py`（或 `pkg/snowflake/snowflake.go`）

**唯一性保障**：
- 单节点：毫秒内序列号自增，无重复
- 多节点：worker_id 不同，无重复
- 时钟回拨：检测到回拨时抛异常，等待或报错

## 3. ER 图

```mermaid
erDiagram
    t_department ||--o{ t_department : "parent"
    t_department ||--o{ t_department_position : "scopes"
    t_position ||--o{ t_department_position : "scoped_by"
    t_user ||--|| t_user_profile : "has"
    t_user ||--o{ t_user_position : "holds"
    t_user ||--o{ t_user_role : "granted"
    t_position ||--o{ t_user_position : "assigned"
    t_role ||--o{ t_user_role : "assigned_to"
    t_role ||--o{ t_role_permission : "contains"
    t_permission ||--o{ t_role_permission : "granted_to"
    t_permission ||--o{ t_permission : "parent"

    t_department {
        bigint id PK
        varchar code UK
        varchar name UK
        bigint parent_id FK
        int sort_order
    }

    t_position {
        bigint id PK
        varchar code UK
        varchar name
        int sort_order
    }

    t_department_position {
        bigint id PK
        bigint department_id FK
        bigint position_id FK
        smallint is_required
    }

    t_user {
        bigint id PK
        varchar employee_no UK
        varchar username UK
        varchar password_hash
        varchar name
        smallint is_active
        timestamp last_login_at
    }

    t_user_profile {
        bigint user_id PK_FK
        varchar id_card
        varchar gender
        date birth_date
        date hire_date
        date leave_date
        varchar phone
        varchar wechat
        varchar email
        text address
        varchar emergency_contact_name
        varchar emergency_contact_phone
        text remark
    }

    t_user_position {
        bigint id PK
        bigint user_id FK
        bigint position_id FK
        smallint is_primary
    }

    t_role {
        bigint id PK
        varchar code UK
        varchar name
        varchar description
        smallint is_builtin
    }

    t_permission {
        bigint id PK
        varchar code UK
        varchar name
        varchar type
        bigint parent_id FK
        varchar path
        varchar icon
        varchar component
        int sort_order
        smallint is_enabled
    }

    t_role_permission {
        bigint id PK
        bigint role_id FK
        bigint permission_id FK
    }

    t_user_role {
        bigint id PK
        bigint user_id FK
        bigint role_id FK
    }
```

## 4. 通用约定

### 4.1 主键策略

**全表统一使用雪花 ID（64 位 Long）**：

| DB 类型 | PostgreSQL |
|---|---|
| 主键字段类型 | `BIGINT` |
| 取值范围 | -9223372036854775808 ~ 9223372036854775807 |
| 实际使用范围 | 0 ~ 2^63 - 1（符号位固定 0） |
| 容量 | 每节点每毫秒 4096 个，可运行 69 年 |

**外键类型必须与主表一致**：`BIGINT`

**前后端传输**：字符串类型
```typescript
// TypeScript
interface User {
  id: string                    // "1892345678123456789"
  username: string
  // ...
}
```

### 4.2 审计字段

每张表都有：
- `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
- `updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP`
- `deleted_at TIMESTAMP DEFAULT NULL` — 软删除标记
- `created_by BIGINT DEFAULT NULL` — 创建人，关联 `t_user.id`

> PostgreSQL 推荐使用 `TIMESTAMP`（不带时区），应用层统一 UTC。

### 4.3 软删除

- 软删除 = `deleted_at` 填时间戳
- 离职 = 软删除 + 登录禁用（`t_user.is_active = 0`）
- 所有业务查询必须过滤 `deleted_at IS NULL`

## 5. 表结构详情

### 5.1 t_department（部门）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| code | VARCHAR(20) | 是 | — | 部门编码（唯一），如 `D0001` |
| name | VARCHAR(50) | 是 | — | 部门名（唯一） |
| parent_id | BIGINT | 否 | NULL | 父部门，FK→t_department.id |
| sort_order | INT | 是 | 0 | 排序 |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_code ON t_department(code) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uk_name ON t_department(name) WHERE deleted_at IS NULL;
CREATE INDEX idx_parent ON t_department(parent_id);
```

**预置数据**：

| code | name | parent_code |
|---|---|---|
| D0001 | 生产部 | — |
| D0002 | 管理部 | — |
| D0003 | 品检部 | — |
| D0004 | 物流部 | — |
| D0005 | 车间A | D0001 |
| D0006 | 车间B | D0001 |

### 5.2 t_position（工种字典）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| code | VARCHAR(20) | 是 | — | 工种代码（唯一） |
| name | VARCHAR(50) | 是 | — | 工种名 |
| sort_order | INT | 是 | 0 | 排序 |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_code ON t_position(code) WHERE deleted_at IS NULL;
```

**预置数据**：

| code | name |
|---|---|
| TURN | 车床 |
| MILL | 铣床 |
| GRIND | 磨床 |
| CNC_OP | CNC 操机 |
| CNC_PROG | CNC 编程 |
| CLERK | 文员 |
| MANAGER | 经理 |
| FACTORY_HEAD | 厂长 |
| QC | 品检 |
| DRIVER | 司机 |

### 5.3 t_department_position（部门-工种）

> 表达"哪个部门下设哪些工种"。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| department_id | BIGINT | 是 | — | FK→t_department.id |
| position_id | BIGINT | 是 | — | FK→t_position.id |
| is_required | SMALLINT | 是 | 0 | 是否该部门必备工种（1=是 0=可选） |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_dept_pos ON t_department_position(department_id, position_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_position ON t_department_position(position_id);
```

### 5.4 t_user（系统账号）★ 纯登录表

> **v5 修订**：移除 `department_id`（数据权限将来由专门的关联表处理，本阶段不需要）。仅保留登录必需 + 高频显示字段。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| employee_no | VARCHAR(20) | 是 | — | 工号（唯一），如 `U20260001` |
| username | VARCHAR(50) | 是 | — | 登录用户名（唯一） |
| password_hash | VARCHAR(255) | 是 | — | 密码哈希（bcrypt/argon2） |
| name | VARCHAR(50) | 是 | — | 真实姓名（首页/日志高频显示） |
| is_active | SMALLINT | 是 | 1 | 是否在职，1=是 0=否 |
| last_login_at | TIMESTAMP | 否 | NULL | 最近登录时间 |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_employee_no ON t_user(employee_no) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uk_username ON t_user(username) WHERE deleted_at IS NULL;
CREATE INDEX idx_active ON t_user(is_active) WHERE deleted_at IS NULL;
```

**业务规则**：
- 离职 = `is_active = 0` + `deleted_at` 填时间，登录禁用
- 密码失败 5 次锁定 30 分钟（应用层 + Redis）
- 超级管理员（`username='superadmin'`）绕过所有权限校验

### 5.5 t_user_profile（人事档案）★ 扩展

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| user_id | BIGINT | 是 | — | PK + FK→t_user.id，与 user 主键共用 |
| id_card | VARCHAR(32) | 否 | NULL | 身份证号 |
| gender | VARCHAR(8) | 否 | NULL | 性别 |
| birth_date | DATE | 否 | NULL | 出生日期 |
| hire_date | DATE | 否 | NULL | 入职日期 |
| leave_date | DATE | 否 | NULL | 离职日期 |
| phone | VARCHAR(20) | 否 | NULL | 手机号 |
| wechat | VARCHAR(50) | 否 | NULL | 微信号 |
| email | VARCHAR(100) | 否 | NULL | 邮箱 |
| department_id | BIGINT | 否 | NULL | 所属部门 ★（数据权限/组织归属从这里取） |
| address | TEXT | 否 | NULL | 家庭住址 |
| emergency_contact_name | VARCHAR(50) | 否 | NULL | 紧急联系人姓名 |
| emergency_contact_phone | VARCHAR(20) | 否 | NULL | 紧急联系人电话 |
| remark | TEXT | 否 | NULL | 备注 |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (user_id)
CREATE INDEX idx_id_card ON t_user_profile(id_card) WHERE deleted_at IS NULL;
CREATE INDEX idx_hire_date ON t_user_profile(hire_date) WHERE deleted_at IS NULL;
CREATE INDEX idx_department ON t_user_profile(department_id) WHERE deleted_at IS NULL;
```

**预留扩展（按需建子表）**：
- `t_user_salary` 薪资发放记录（1对多）
- `t_user_insurance` 社保缴纳记录（1对多）
- `t_user_bank_card` 银行账户（1对多）
- `t_user_leave` 请假记录（1对多）

### 5.6 t_user_position（账号-工种兼任）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| user_id | BIGINT | 是 | — | FK→t_user.id |
| position_id | BIGINT | 是 | — | FK→t_position.id |
| is_primary | SMALLINT | 是 | 0 | 是否主工种（1=主 0=兼） |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_user_position ON t_user_position(user_id, position_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_position ON t_user_position(position_id);
```

### 5.7 t_role（角色）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| code | VARCHAR(50) | 是 | — | 角色代码（唯一），如 `SUPER_ADMIN` |
| name | VARCHAR(50) | 是 | — | 角色名 |
| description | VARCHAR(255) | 否 | NULL | 描述 |
| is_builtin | SMALLINT | 是 | 0 | 是否内置（内置不可删） |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_code ON t_role(code) WHERE deleted_at IS NULL;
```

**预置数据**：

| code | name | is_builtin | 说明 |
|---|---|---|---|
| SUPER_ADMIN | 超级管理员 | 1 | 绕过所有权限 |
| FACTORY_HEAD | 厂长 | 1 | 看所有数据 |
| MANAGER | 经理 | 1 | 订单全流程 |
| CLERK | 文员 | 1 | 录入订单/零件/工序 |
| WORKER | 工人 | 1 | 领取/报工 |
| QC | 品检 | 1 | 检验 |
| DRIVER | 司机 | 1 | 送货 |

### 5.8 t_permission（权限点）★ 精简

> **v5 修订**：本阶段只实现**菜单（MENU）** 和 **API（API）** 两类权限。`BUTTON` 和 `DATA` 类型字段预留，**本期不填值**，未来扩展时无需改表结构。

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| code | VARCHAR(100) | 是 | — | 权限代码（唯一） |
| name | VARCHAR(50) | 是 | — | 权限名 |
| type | VARCHAR(20) | 是 | — | 类型：MENU / API / BUTTON / DATA |
| parent_id | BIGINT | 否 | NULL | 父权限，FK→t_permission.id（MENU 自引用，构菜单树） |
| path | VARCHAR(255) | 否 | NULL | 前端路由（MENU）或 API 路径 |
| icon | VARCHAR(50) | 否 | NULL | 前端图标（仅 MENU） |
| component | VARCHAR(255) | 否 | NULL | 前端组件路径（仅 MENU） |
| sort_order | INT | 是 | 0 | 排序 |
| is_enabled | SMALLINT | 是 | 1 | 是否启用 |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**type 字段本期取值范围**：

| type | 本期 | 说明 |
|---|---|---|
| MENU | ✅ 使用 | 前端菜单项，构菜单树 |
| API | ✅ 使用 | 后端接口权限 |
| BUTTON | ❌ 预留 | 前端按钮权限，未来追加 |
| DATA | ❌ 预留 | 数据范围权限，未来追加 |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_code ON t_permission(code) WHERE deleted_at IS NULL;
CREATE INDEX idx_parent ON t_permission(parent_id, sort_order);
CREATE INDEX idx_type ON t_permission(type);
```

**code 命名规范**：

| 类型 | 格式 | 示例 |
|---|---|---|
| MENU（菜单） | `{module}:menu` | `order:menu` |
| API（HTTP 方法） | `{module}:{action}` | `order:list`、`order:create` |
| BUTTON（预留） | `{module}:btn:{action}` | `order:btn:export` |
| DATA（预留） | `{module}:data:{scope}` | `order:data:self` |

**MENU 字段使用约定**：

| 字段 | MENU | API | BUTTON（预留） | DATA（预留） |
|---|---|---|---|---|
| path | 前端路由 `/order` | API 路径 `/api/orders` | — | — |
| icon | 图标名 | NULL | NULL | NULL |
| component | 组件路径 `@/views/order/Index.vue` | NULL | NULL | NULL |
| parent_id | 父菜单 ID | NULL | NULL | NULL |

### 5.9 t_role_permission（角色-权限）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| role_id | BIGINT | 是 | — | FK→t_role.id |
| permission_id | BIGINT | 是 | — | FK→t_permission.id |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_role_permission ON t_role_permission(role_id, permission_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_permission ON t_role_permission(permission_id);
```

### 5.10 t_user_role（账号-角色）

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | BIGINT | 是 | 雪花 ID | 主键 |
| user_id | BIGINT | 是 | — | FK→t_user.id |
| role_id | BIGINT | 是 | — | FK→t_role.id |
| created_at | TIMESTAMP | 是 | CURRENT_TIMESTAMP | |
| updated_at | TIMESTAMP | 是 | 自动更新 | |
| deleted_at | TIMESTAMP | 否 | NULL | |
| created_by | BIGINT | 否 | NULL | |

**索引**：
```sql
PRIMARY KEY (id)
CREATE UNIQUE INDEX uk_user_role ON t_user_role(user_id, role_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_role ON t_user_role(role_id);
```

## 6. RBAC 权限模型

### 6.1 模型图

```
User ──(多对多)──> Role ──(多对多)──> Permission
                                              │
                                              ├── MENU (菜单树)
                                              └── API  (接口)
                                              
                                       (BUTTON/DATA 未来追加)
```

### 6.2 权限校验流程

#### 前端（动态菜单）

1. 登录成功后调用 `/api/auth/me`，返回：
   ```json
   {
     "user": { "id": "1892345678123456789", "username": "zhangsan", "name": "张三" },
     "permissions": ["order:menu", "order:list", "part:menu", "part:list"],
     "menus": [
       {
         "id": "1892345678900000001",
         "code": "order:menu",
         "name": "订单管理",
         "path": "/order",
         "icon": "Document",
         "component": "@/views/order/Index.vue",
         "children": [
           { "id": "...", "code": "order:list", "name": "订单列表", "path": "/order/list" }
         ]
       }
     ]
   }
   ```
2. Vue Router `beforeEach` 守卫：目标路由需要的 `meta.code` 是否在 `permissions` 中，无权限跳 403
3. 按钮级控制（**本阶段简化**：暂时前端硬编码或写死，后续追加 BUTTON 类型后用 `v-permission` 指令）

#### 后端（API 权限）

1. 中间件解析 JWT 获取 `user_id`
2. 查 `t_user_role` → `t_role_permission` → `t_permission` 拿所有 `code`
3. 缓存到 Redis：
   - `user_perms:{user_id}` → SET，TTL 30 分钟
   - `user_menus:{user_id}` → JSON，TTL 30 分钟
4. 装饰器校验：
   ```python
   @router.get("/orders")
   @require_permission("order:list")  # ① 中间件从 Redis 查 "order:list" 是否在 user_perms 中
   async def list_orders():
       ...
   ```
5. **超级管理员绕过**：`username == 'superadmin'` 直接放行（白名单）

### 6.3 权限加载实现

```python
async def load_user_permissions(user_id: int) -> dict:
    """登录后加载权限，缓存到 Redis"""
    
    # 查询所有权限码
    perm_rows = await db.execute(
        select(TPermission.code, TPermission.type)
        .join(TRolePermission, TPermission.id == TRolePermission.permission_id)
        .join(TUserRole, TRolePermission.role_id == TUserRole.role_id)
        .where(
            TUserRole.user_id == user_id,
            TUserRole.deleted_at.is_(None),
            TPermission.is_enabled == 1,
            TPermission.deleted_at.is_(None)
        )
    )
    
    all_codes = {row.code for row in perm_rows}
    
    # 查询菜单树
    menu_rows = await db.execute(
        select(TPermission).where(
            TPermission.code.in_(all_codes),
            TPermission.type == 'MENU',
            TPermission.deleted_at.is_(None)
        ).order_by(TPermission.sort_order)
    )
    menus = build_menu_tree(menu_rows)  # 应用层组装树
    
    # 缓存
    cache_data = {
        "codes": list(all_codes),
        "menus": menus
    }
    await redis.set(f"user_auth:{user_id}", json.dumps(cache_data), ex=1800)
    
    return cache_data
```

### 6.4 中间件校验

```python
async def require_permission(required_code: str, user_id: int):
    # 1. 超管放行
    user = await get_user(user_id)
    if user.username == 'superadmin':
        return
    
    # 2. 查缓存
    cached = await redis.get(f"user_auth:{user_id}")
    if not cached:
        cached = await load_user_permissions(user_id)
    
    codes = set(json.loads(cached)["codes"])
    
    # 3. 校验
    if required_code not in codes:
        raise HTTPException(403, f"permission denied: {required_code}")
```

### 6.5 预置角色与权限示例（本阶段）

| 角色 | 权限示例 |
|---|---|
| SUPER_ADMIN | `*` 全部 |
| FACTORY_HEAD | `*:menu`、`*:list/view`、`report:*` |
| MANAGER | `order:*`、`part:*`、`process:*`、`report:menu`、`report:list` |
| CLERK | `order:menu/list/create/edit`、`part:menu/list/create/edit`、`process:menu/list/create/edit` |
| WORKER | `process:menu/list`、`work_report:menu/list/create` |
| QC | `qc:*`、`part:menu/list` |
| DRIVER | `delivery:menu/list/create/edit` |

### 6.6 菜单权限点示例

```
订单管理 (MENU)
  code: order:menu
  path: /order
  component: @/views/order/Index.vue
  ├─ 订单列表 (MENU)
  │    code: order:list
  │    path: /order/list
  │    component: @/views/order/List.vue
  ├─ 订单新建 (MENU，本阶段用菜单粒度)
  │    code: order:create
  │    path: /order/create
  │    component: @/views/order/Create.vue
  └─ 订单编辑 (MENU)
       code: order:edit
       path: /order/edit/:id
       component: @/views/order/Edit.vue

生产管理 (MENU)
  code: production:menu
  ├─ 零件管理 (MENU)
  │    code: part:menu
  │    └─ 零件列表 (MENU)    code: part:list
  └─ 工序管理 (MENU)
       code: process:menu
       └─ 工序列表 (MENU)    code: process:list

报工管理 (MENU)
  code: work_report:menu
  ├─ 报工录入 (MENU)          code: work_report:create
  └─ 报工查询 (MENU)          code: work_report:list
```

### 6.7 未来扩展：BUTTON 和 DATA 类型

**BUTTON 权限（后续追加）**：

| 字段 | 值 |
|---|---|
| type | `BUTTON` |
| code | `order:btn:export` |
| name | 导出订单 |
| parent_id | 指向所属菜单 |
| path/component/icon | NULL |

前端用自定义指令：
```vue
<el-button v-permission="'order:btn:export'">导出</el-button>
```

**DATA 权限（后续追加）**：

新增 `t_data_scope` 关联表 或 在 `t_permission.type='DATA'` 中定义 scope：
- `order:data:self`（仅自己）
- `order:data:dept`（本部门，从 `t_user_profile.department_id` 取）
- `order:data:all`（全部）

后端查询时根据 DATA 权限码注入 WHERE 条件，详见 v4 文档 6.3 节（保留作为未来参考）。

## 7. 关系说明

| 关系 | 基数 | 业务含义 |
|---|---|---|
| t_department → t_department | 1 : N | 部门自引用，部门树 |
| t_department → t_department_position | 1 : N | 部门下设多个工种 |
| t_position → t_department_position | 1 : N | 工种被多个部门启用 |
| t_user → t_user_profile | 1 : 1 | 账号对应人事档案 |
| t_user → t_user_position | 1 : N | 账号可兼任多工种 |
| t_position → t_user_position | 1 : N | 工种可被多人担任 |
| t_user → t_user_role | 1 : N | 账号可拥有多角色 |
| t_role → t_user_role | 1 : N | 角色可分配给多人 |
| t_role → t_role_permission | 1 : N | 角色包含多权限 |
| t_permission → t_role_permission | 1 : N | 权限可授权给多角色 |
| t_permission → t_permission | 1 : N | 权限自引用，菜单树 |

## 8. 扩展性考虑

| 未来需求 | 现在的应对 |
|---|---|
| 按钮权限 | `t_permission.type` 增加 `BUTTON`，新增 `parent_id` 指向菜单 |
| 数据范围权限 | `t_permission.type` 增加 `DATA`，新增 scope 字段或用 code 后缀 `self/dept/all` |
| 薪资管理 | 新增 `t_user_salary`（1对多） |
| 医保/社保 | 新增 `t_user_insurance`（1对多） |
| 银行账户 | 新增 `t_user_bank_card`（1对多） |
| 请假记录 | 新增 `t_user_leave`（1对多） |
| 一人多部门 | 新增 `t_user_department`（关联表），不影响 t_user |
| 多工厂 | 所有表加 `factory_id` |
| 通用审计日志 | 新增 `t_audit_log` |
| 工种细分 | `t_position` 加 `parent_id` 构分类树 |

## 9. 总结

**10 张表**：

```
组织（雪花 ID）           权限（雪花 ID）
───────────────         ────────────────
t_department          部门    t_role              角色
t_position            工种    t_permission        权限点(MENU/API)
t_department_position 部门-工种  t_role_permission 角色-权限
t_user              ★ 账号    t_user_role         账号-角色
t_user_profile      ★ 人事档案
t_user_position       兼任工种
```

**核心机制**：
1. **统一雪花主键**：64 位 Long，PostgreSQL `BIGINT`，前后端字符串传输
2. **部门-工种多对多**：`t_department_position` 表达"哪个部门用哪些工种"
3. **账号/档案分离**：`t_user`（纯登录）+ `t_user_profile`（人事扩展含 department_id）
4. **一人多工种**：`t_user_position` 多对多兼任
5. **RBAC 权限（本阶段）**：`User → Role → Permission`，仅 `MENU` 和 `API` 两类
6. **动态菜单**：登录后返回菜单树，前端按权限渲染
7. **API 权限**：中间件从 Redis 校验，后端 `@require_permission` 装饰器
8. **超管绕过**：`username='superadmin'` 直接放行
9. **软删除**：PostgreSQL 部分唯一索引 + `deleted_at IS NULL` 过滤
10. **未来扩展**：`BUTTON` 和 `DATA` 类型权限预留字段，本期不填值
