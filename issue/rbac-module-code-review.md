# Code Review: RBAC 权限管理模块

> 提交: `8421e11 feat: 新建rbac权限管理模块`
> 审查范围: 63 个文件 / +5842 行
> 审查维度: 安全性、并发、事务、API 设计、前后端一致性

---

## 总览

| 级别 | 数量 | 必须修 |
|---|---|---|
| 🔴 P0 安全/数据 | 5 | 合并前必修 |
| 🟠 P1 事务/逻辑 | 8 | 合并前必须 |
| 🟡 P2 健壮性 | 12 | 建议本周内 |
| 🟢 P3 代码质量 | 7 | 后续清理 |

---

## 🔴 P0 严重（安全/数据正确性）

### Bug 1: 超管判断走"用户名相等"——账号接管 / 提权漏洞

**位置**:
- `core/config.py:28`
- `service/rbac/auth_service.py:35-36`
- `api/rbac_deps.py:77,106`

```python
def is_superadmin(self, user: TUser) -> bool:
    return user.username == settings.superadmin_username   # ← 致命
```

**问题**:
- 任何用户只要把 username 改成 `superadmin` 即可获得全部权限；
- RBAC 管理 API 用 `require_superadmin()` 守护,普通管理员可通过"重置 superadmin 密码 → 注销 → 用同名账号登录"绕过。

**修复**:
```python
# model/user.py 增加字段
is_superadmin: Mapped[int] = mapped_column(
    SmallInteger, nullable=False, default=0, server_default="0"
)

# service/rbac/auth_service.py
def is_superadmin(self, user: TUser) -> bool:
    return user.is_superadmin == 1
```
删除"username 匹配"逻辑；登录时给 `is_superadmin` 写值；alembic 迁移新增列并给历史 superadmin 用户回填。

---

### Bug 2: `update_user` 角色存在性检查后存在 TOCTOU + 重复 id 问题

**位置**: `service/rbac/rbac_service.py:110-118`

```python
for rid in role_ids:
    role = await self.uow.roles.get_by_id(rid)   # 检查
    if role is None:
        raise BizError(...)
await self.uow.user_roles.replace(user.id, role_ids)  # 再操作
```

**问题**:
- TOCTOU (time-of-check to time-of-use): 检查与真正写入之间状态可能变化；
- 检查集合与真正写入集合不一致（`role_ids` 含重复 id 也会被处理两次）。

**修复**:
```python
unique_ids = list(set(role_ids))
if unique_ids:
    found = await self.uow.roles.list_by_ids(unique_ids)
    if {r.id for r in found} != set(unique_ids):
        raise BizError(code=ErrCode.BIZ_ROLE_NOT_FOUND, ...)
await self.uow.user_roles.replace(user.id, unique_ids)
```

---

### Bug 3: `replace()` 重建可绕开"未删除"约束,导致关联表膨胀

**位置**:
- `repository/rbac/user_role.py:36-49`
- `repository/rbac/role_permission.py:36-49`

```python
for link in existing:
    if link.role_id not in new_ids and link.deleted_at is None:
        link.deleted_at = now
for rid in new_ids - existing_ids:
    self.session.add(TUserRole(user_id=user_id, role_id=rid))  # 软删后立刻加新的
```

**问题**:
- 如果用户先被 revoke 又 reassign 同一 role,会产生"deleted_at 非空"的历史行 + 新行；
- 重复调用 `replace` 累积脏数据,关联表会膨胀,长期运行性能下降。

**修复**:
- 在 `t_user_role` / `t_role_permission` 上加唯一索引：
  ```sql
  CREATE UNIQUE INDEX uk_t_user_role_active
      ON t_user_role(user_id, role_id) WHERE deleted_at IS NULL;
  ```
- 把"已软删视为不存在"统一在查询里处理；或用 `INSERT ... ON CONFLICT DO UPDATE SET deleted_at=NULL`。

---

### Bug 4: 软删除 user 时,关联表 `deleted_at` 未级联更新

**位置**: `model/rbac.py:118-145`、`repository/rbac/role.py:73-80`

```python
# model 中 FK: ondelete="CASCADE"
# 但软删除只设置 deleted_at,不真删
```

**问题**:
- 软删 user 时,`t_user_profile` / `t_user_role` 的 `deleted_at` 不会被同步更新；
- `TUser.profile` 关系 `cascade="all, delete-orphan"` 在软删时不触发；
- 查询关联数据时还能查到已软删用户的历史。

**修复**:
- service 层显式调用：
  ```python
  await uow.user_roles.soft_delete_by_user(user_id)
  await uow.user_profiles.soft_delete_by_user(user_id)
  ```
- 或增加数据库层 `deleted_at` 级联更新触发器。

---

### Bug 5: `get_current_user` 不验证 token 完整性

**位置**: `api/rbac_deps.py:35-64`

只校验签名 + `sub` 存在；无 `iss`/`aud`/`token_type` 强校验。

**修复**:
```python
payload = jwt.decode(
    token, settings.jwt_secret,
    algorithms=[settings.jwt_algorithm],
    options={"require": ["sub", "iat", "exp"]},
)
```
强制 `sub` 是数字；增加黑名单(登出时主动失效)。

---

## 🟠 P1 高（功能正确性/事务边界）

### Bug 6: RBAC service 写操作 `commit()` 在 SAVEPOINT 之后破坏事务语义

**位置**: `service/rbac/rbac_service.py:52, 94, 124, 187, 221, 247, 283, 324`

`async with self.uow.session.begin_nested():` + 紧跟的 `await self.uow.commit()` 会先 `commit()` 父事务（因为没有外层事务）,实际相当于把每个 savepoint 提到顶层自动提交,破坏了"全部成功或全部回滚"的语义。

**修复**:
- 在 service 层用 `async with self.uow.session.begin():`；
- 调用方（如 FastAPI 依赖）负责 commit/rollback；
- 或显式声明事务边界契约。

---

### Bug 7: `assign_user_roles` 接口先 update 后 get_user_detail,中间窗口期拿不到最新数据

**位置**: `api/v1/rbac/rbac.py:234-239`

```python
await svc.update_user(user_id=user_id, role_ids=payload.role_ids)
return await svc.get_user_detail(user_id)  # 跨请求可能读到旧值(尤其有缓存)
```

**修复**:
- 在 service 层返回 `UserDetailOut`,内部一次性查询后封装；
- 或新增 `assign_user_roles_service` 聚合方法。

---

### Bug 8: `create_user` / `update_user` 角色参数语义不清

**位置**: `service/rbac/rbac_service.py:73-81, 109-118`

`role_ids=None` 表示不改、`role_ids=[]` 表示清空——但代码可读性差,且无单元测试覆盖两种语义。

**修复**:
- service 显式区分 `None`(不改)和 `[]`(清空),加注释；
- 增加单元测试覆盖。

---

### Bug 9: 权限缓存 key 失效路径不全

**位置**: `service/rbac/rbac_service.py`、`core/permission_cache.py`

只在 `create/update/delete_user` 时清缓存,但以下场景未失效：
- 给角色加/减权限 (`update_role`) → 该角色所有用户缓存脏读；
- 启用/停用权限点 (`update_permission`) → 同上；
- `PUT /users/{id}/roles` 路径未刷缓存(已有 update_user 路径覆盖,但应统一)。

**修复**:
- 封装 `permission_cache.invalidate_user(user_id)` 和 `permission_cache.invalidate_role(role_id)`(后者反查用户列表)；
- 在所有影响 user→permission 路径的服务层调用。

---

### Bug 10: `replace()` 调用未立即 commit,IntegrityError 延迟暴露

**位置**: `repository/rbac/user_role.py:49, role_permission.py:49`

`await self.session.flush()` 之后立即 return,但调用方在 `await self.uow.commit()` 之间如果还有后续操作,可能因为 `IntegrityError` 异常延迟到 commit 才暴露,定位困难。

**修复**:
- 调用方在 `replace` 之后立即 `await self.uow.commit()`,或在 service 层用单一 `commit` 收尾。

---

### Bug 11: alembic 迁移对历史数据破坏性变更

**位置**: `alembic/versions/a1b2c3d4e5f6_rbac_module.py:22-39`

```python
op.execute("ALTER TABLE t_user DROP CONSTRAINT t_user_pkey CASCADE")  # CASCADE
op.add_column("t_user", sa.Column("password_hash", ..., server_default=""))
op.add_column("t_user", sa.Column("employee_no", ..., server_default="LEGACY"))
```

**问题**:
- `CASCADE` 会级联删除所有引用 `t_user.id` 的外键约束 + 索引,回滚时大量数据不一致；
- 默认密码空字符串让历史用户可以空密码登录；
- 默认 `name='legacy'` 重复,后续 UNIQUE 约束会冲突。

**修复**:
1. 改用 `op.drop_constraint(..., type_='primary')` 不带 CASCADE；
2. 真正迁移老数据：用一次性 UPDATE 把历史用户的 `password_hash` 设为必须重置的临时哈希,把 `employee_no` 用 id 填充；
3. 增加 `is_active=0` 让老账号必须重置密码。

---

### Bug 12: alembic downgrade 不重建外键,回滚后脏库

**位置**: `a1b2c3d4e5f6_rbac_module.py:137-159`

downgrade 只 `drop_table` / `drop_column`,没重建被 CASCADE 删掉的 FK；测试环境一旦 downgrade,业务表的外键会消失。

**修复**:
- downgrade 中按依赖顺序重建 FK；
- 或显式提示"不可逆迁移",避免误操作。

---

### Bug 13: 种子迁移的 ID 自增器与 snowflake 冲突

**位置**: `alembic/versions/b2c3d4e5f6a7_seed_rbac.py:54-62`

```python
counter = [100_000]
def _next():
    counter[0] += 1
    return counter[0]
```

种子 ID 从 100001 开始,但生产 snowflake 生成的 ID 也会落在该区间,后期插入业务数据时与种子权限点 ID 语义混乱。

**修复**:
- 用更明确的隔离区段,如 `0~10000` 固定为"系统种子",生产 ID 走 snowflake；
- 工具层校验 id 范围。

---

## 🟡 P2 中（健壮性/可维护性）

### Bug 14: 前端 `http.ts` 401 重定向后 axios 仍抛出未捕获错误

**位置**: `frontend/src/api/http.ts:62-67`

```js
if (status === 401) {
  setToken(null)
  if (location.pathname !== '/login') {
    location.href = `/login?redirect=${encodeURIComponent(location.pathname)}`
  }
}
```

`location.href =` 已跳转,但 axios 还在 `await` 里,后续 `throw new ApiError(...)` 会给业务层未捕获异常。

**修复**:
- 401 时不抛错,或 `return Promise.reject({ silent: true })`。

---

### Bug 15: 登录失败时 `auth.me` 状态被覆盖

**位置**:
- `frontend/src/store/auth.ts:23-33`
- `frontend/src/views/Login.vue:69-85`

`auth.login` 内部 `loadMe` 失败时不会 `logout`,失败后 `me` 仍是上一次值(若有),导致 `isAuthenticated` 短暂为 true。

**修复**:
- 在 `auth.login` 失败路径上 catch 后 `auth.logout()`。

---

### Bug 16: `router.beforeEach` 动态 import store,首次切换引入延迟

**位置**: `frontend/src/router/index.ts:62-72`

```js
const { useAuthStore } = await import('@/store/auth')
```

每次路由切换都 `import`,Pinia store 复用由其内部维护,但 `await import` 是异步操作,首次切换会引入微小延迟;且 `main.ts:23-31` 已经 import 过一次,这里再次 dynamic import 没有意义。

**修复**:
- 改为静态 `import { useAuthStore } from '@/store/auth'`。

---

### Bug 17: `MainLayout.vue` 菜单超管走静态,普通用户走后端下发,两套逻辑

**位置**: `frontend/src/layouts/MainLayout.vue:221-228`

```js
const displayMenus = computed(() => {
  if (auth.isSuperadmin) return allStaticMenus
  return auth.menus
})
```

**问题**:
- 普通用户看到的菜单依赖后端下发,新增菜单后所有用户需重新登录；
- 两套渲染逻辑不一致,容易出 bug。

**修复**:
- 菜单完全走后端下发,前端做静态映射 `code → { icon, sort_order }` 仅用于美化；
- 或超管也走后端(后端对超管下发全量)。

---

### Bug 18: `RoleList.vue` 编辑角色后 `nextTickReset` 总清空树 → 丢失已有权限

**位置**: `frontend/src/views/rbac/RoleList.vue:214-226`

```js
async function openEdit(row: Role) {
  ...
  nextTickReset()   // setCheckedKeys([]) 
}
```

编辑时打开对话框会**清空**已勾选的权限,然后 `onSubmit` 又收集 checked 提交,导致编辑模式实际保存为"无权限"(如果用户不再点勾)。

**修复**:
- 编辑模式加载已有权限并回显：
  ```js
  async function openEdit(row) {
    const current = await rbacApi.getRolePermissions(row.id)
    Object.assign(form, { ..., permission_ids: current.data.map(Number) })
    setTimeout(() => treeRef.value?.setCheckedKeys(form.permission_ids.map(String)), 50)
  }
  ```
- 后端需新增 `GET /rbac/roles/{id}/permissions` 接口。

---

### Bug 19: `RoleList.vue` 权限树只显示 MENU 类型,API 类型看不到

**位置**: `frontend/src/views/rbac/RoleList.vue:155-160`

```js
return roots.filter((r) => allPerms.value.find((p) => p.id === r.id)?.type === 'MENU')
```

种子数据里 API 权限的 `parent_id = NULL`,被过滤后**永远不出现**在树里。导致 API 权限无法分配给角色。

**修复**:
- 把 API 权限挂到对应 MENU 节点下；
- 或在 UI 上分两个 tab:菜单 / API 接口。

---

### Bug 20: `PermissionList.vue` 父级菜单可选列表中包含自己,可能成环

**位置**: `frontend/src/views/rbac/PermissionList.vue:142-144`

```js
const parentOptions = computed(() =>
  list.value.filter((p) => p.type === 'MENU' && String(p.id) !== String(form.id ?? '')),
)
```

只排除自己,但允许把 A 的父设为 B、B 的父设为 A(前端编辑时)。后端 `update_permission` 完全没校验会成环。

**修复**:
- 后端 `update_permission` 增加防环:向上回溯 parent_id,若命中自己则报错；
- 前端只显示"祖先候选"。

---

### Bug 21: `UserList.vue` 列表"角色"列每行都请求一次详情,N+1

**位置**: `frontend/src/views/rbac/UserList.vue:256-263`

```js
for (const u of list.value) {
  const d = await rbacApi.getUserDetail(String(u.id))
  u.roles = d.data.roles
}
```

20 条用户 = 21 次请求；分页 size=100 时就 101 次。

**修复**:
- 后端 `list_users` 返回 `UserDetailOut[]`(包含 roles)；
- 或新增 `?with=roles` 参数。

---

### Bug 22: `delete_user` 接口不校验"不能删除自己/超管"

**位置**: `service/rbac/rbac_service.py:123-134`

`superadmin` 用户可被自己或其他超管删除；删后无人能登录管理后台。

**修复**:
```python
if user.is_superadmin:
    raise BizError(code=BIZ_USER_BUILTIN, ...)
if user.id == current_user.id:
    raise BizError(code=BIZ_USER_SELF_DELETE, ...)
```

---

### Bug 23: `login` 不刷新 `is_active` 实时状态

**位置**: `service/rbac/auth_service.py:39-73`

`is_active=0` 后 token 仍有效 8 小时(默认)。无强制下线机制。

**修复**:
- 在 `get_current_user` 已检查 `is_active==1`(已实现 ✓)；
- 但用户被踢下线时缓存的 JWT 仍可用 —— 标准 token 撤销难题,可加入 `token_version` 字段或短期 token + refresh。

---

### Bug 24: 错误信息泄露与生产模式不一致

**位置**: `core/exception_handler.py:75-80`

```python
return _json(ErrCode.INTERNAL_ERROR, str(exc) or "internal server error", 500)
```

直接把异常 message 返回给前端。`SQLAlchemyError`/`ValueError`/`KeyError` 的 message 可能含 SQL 片段、表名、堆栈信息。

**修复**:
- 生产环境只返回 `"internal server error"`,详细堆栈写日志。

---

### Bug 25: `tests/conftest.py` 硬编码 DB 凭据

**位置**: `tests/conftest.py:31-33`

```python
TEST_DATABASE_URL = "postgresql+asyncpg://postgres:991027@127.0.0.1:5433/mydb_test"
```

密码明文硬编码在仓库里。

**修复**:
- 从环境变量读取；
- `.env.test` 加入 `.gitignore`,提供 `.env.test.example`。

---

### Bug 26: alembic 迁移缺少 `if_exists` 防护

**位置**: `a1b2c3d4e5f6_rbac_module.py:24, 25, 158`

`DROP SEQUENCE IF EXISTS` 用了 IF EXISTS,但 `CREATE SEQUENCE ... AS BIGINT` 在 downgrade 中无防护；如果生产库序列已被新结构替换,downgrade 会冲突。

**修复**:
- 所有 DDL 加 `IF EXISTS` 或统一用 `op.execute("DROP SEQUENCE IF EXISTS ...")` 防护。

---

### Bug 27: `pytest` 默认依赖全局 setup_database fixture

**位置**: `tests/conftest.py:42-48`

```python
@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database(): ...
```

无 TEST_DB 时所有非 RBAC 测试(如 order 测试)也会尝试连 `5433` 端口,CI 上失败信息会误导。

**修复**:
- 拆出 `tests/rbac/conftest.py` 局部 fixture；
- 加 `pytest.mark.integration` 跳过条件。

---

## 🟢 P3 低（代码质量）

### Bug 28: `auth_service.py:144` `load_me` 在超管分支没刷菜单

注释说"前端全部放行",但实际超管前端 `displayMenus` 已用静态列表,与后端 `menus: []` 同步,但 seed 阶段 `superadmin` 角色已绑全权限——后端只判 `is_superadmin`,不查询全量菜单。

**修复**:
- 保持现样,但加注释 + 测试覆盖超管"前端能渲染所有菜单"。

---

### Bug 29: `_utcnow()` 函数在多个文件中复制

`model/rbac.py:14`、`model/user.py:20`、`repository/user.py:12`、`repository/rbac/permission.py:11`、`repository/rbac/role.py:76`、`repository/rbac/user_role.py:30,37`、`repository/rbac/role_permission.py:30,37`。

**修复**:
- 抽到 `utils/datetime.py` 统一导出。

---

### Bug 30: `core/permission_cache.py` 没设最大容量

**位置**: `core/permission_cache.py:21`

长期运行 key 只增不减(虽然有 TTL,但 1800s 内可能堆积百万用户)。

**修复**:
- 增加 LRU + max_size；
- 或迁移到 Redis。

---

### Bug 31: 大量未使用的 import

- `service/rbac/rbac_service.py:8` 等多个文件;
- 多个文件 `from typing import Sequence` 后未使用。

**修复**:
- 跑 `ruff check --fix`。

---

### Bug 32: 前端 `rbac.ts` 多个 API 返回类型用 `unknown` hack 写法

**位置**: `frontend/src/api/rbac.ts:13-17`

```js
export function login(username, password) {
  return http.post<unknown, { data: TokenResponse }>(...)
}
```

**修复**:
- 封装 `request<T>` 自动解包 envelope：
  ```ts
  async function request<T>(cfg: AxiosRequestConfig): Promise<T> { ... }
  ```

---

## 🧪 测试覆盖缺口

1. **未测**: 超管删除自己 / 删除最后一个超管
2. **未测**: 角色绑定权限后,该角色用户的 `/auth/me` 缓存是否被清掉
3. **未测**: 并发登录同一用户的 token 版本号
4. **未测**: `replace` 重复调用 + 软删后再 reassign
5. **未测**: alembic 升级/降级在已有数据情况
6. **未测**: CORS 配置 + 前端跨域 cookie

---

## 优先级建议

最关键的是以下四个 bug,建议立即修复后重跑相关测试验证：

- **Bug 1**: 超管提权
- **Bug 3**: 关联表重复行
- **Bug 11**: 迁移破坏性
- **Bug 18**: 编辑角色丢权限

相关测试:
- `tests/apis/rbac/test_rbac_api.py::test_role_crud_flow`
- `tests/apis/rbac/test_auth_api.py::test_superadmin_me_shows_superadmin_flag`
