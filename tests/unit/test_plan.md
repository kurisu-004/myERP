# Service 层单元测试计划

## 总体策略

- **隔离范围**: 每个 service 方法只测业务逻辑，所有 repository 调用用 `unittest.mock.AsyncMock` 隔离。
- **不 mock 的部分**: ORM model 构造、状态机（`PartStateMachine`/`AssemblyStateMachine`）、schema 序列化、纯函数（`_parse_status`、`_guess_content_type` 等）。
- **需要 mock 的外部依赖**: COS SDK（`core.cos`）、`utils.id_gen.new_id`、`core.serial.code_for_parent`、`core.security.*`。
- **文件命名**: `tests/unit/test_{service_name}_service.py`
- **pytest mark**: `@pytest.mark.asyncio` + `pytestmark = pytest.mark.asyncio`
- **不需要真实 DB**: 纯单元测试，不 import `conftest.py` 的 DB fixtures。

## Mock 模式

```python
from unittest.mock import AsyncMock, MagicMock, patch

@pytest.fixture
def mock_repos():
    return {
        "parts": AsyncMock(spec=PartRepository),
        "customers": AsyncMock(spec=CustomerRepository),
        ...
    }

@pytest.fixture
def service(mock_repos):
    return PartService(**mock_repos, broadcaster=None, event_broadcaster=None)
```

对于需要 model 实例的方法，直接构造 `TPart(...)` / `TAssembly(...)` 等对象（不经过 repository），mock 好 `sm` property 或用真实状态机。

## 各 Service 测试用例

### 1. PartService (`tests/unit/test_part_service.py`) — 优先级最高

| # | 方法 | 场景 | 断言 |
|---|------|------|------|
| 1 | `list_parts` | customer_id 对应的客户存在，正常返回分页列表 | 验证 `customers.get_by_id` 被调用；`parts.list_with_filters` / `count_with_filters` 被调用；返回 `PartListOut` |
| 2 | `list_parts` | customer_id 对应的客户不存在 → 404 | `pytest.raises(BizError)`，code=`BIZ_CUSTOMER_NOT_FOUND` |
| 3 | `list_parts` | customer_id=None，跳过客户校验 | `customers.get_by_id` 不被调用 |
| 4 | `get_part` | part 存在 → 返回 PartOut | 验证 `parts.get_by_id` 调用；返回正确 |
| 5 | `get_part` | part 不存在 → 404 | `pytest.raises(BizError)` |
| 6 | `list_events` | part 存在，有 events | 验证 `events.list_by_part` 被调用 |
| 7 | `list_events` | part 不存在 → 404 | `pytest.raises(BizError)` |
| 8 | `create_part` | 正常创建（一级客户） | 验证 customer 校验、流水号获取、part.create、event 写入、返回 PartOut |
| 9 | `create_part` | 正常创建（二级客户） | 验证 parent customer 也被查询 |
| 10 | `create_part` | customer 不存在 → 404 | `pytest.raises(BizError)` |
| 11 | `create_part` | parent customer 不存在 → 404 | `pytest.raises(BizError)` |
| 12 | `create_part` | 未配置序列号代码 → 400 | `pytest.raises(BizError)` |
| 13 | `create_parts_batch` | 全部成功 | 返回 `PartBatchCreateResult`，`failed=[]` |
| 14 | `create_parts_batch` | 部分失败（customer 不存在） | `failed` 列表非空 |
| 15 | `update_part` | 正常更新字段 | 验证 `parts.update` 被调用 |
| 16 | `update_part` | part 不存在 → 404 | `pytest.raises(BizError)` |
| 17 | `update_part` | 更新 customer_id 但新 customer 不存在 → 404 | `pytest.raises(BizError)` |
| 18 | `soft_delete_part` | 正常软删 | 验证 `parts.soft_delete` 被调用 |
| 19 | `soft_delete_part` | part 不存在 → 404 | `pytest.raises(BizError)` |
| 20 | `place_on_shelf` | 正常上架（PENDING → IN_PROCESS） | 验证 shelf 校验、状态机调用、update、广播 |
| 21 | `place_on_shelf` | shelf 不存在 → 404 | `pytest.raises(BizError)` |
| 22 | `place_on_shelf` | shelf 未激活 → 400 | `pytest.raises(BizError)` |
| 23 | `place_on_shelf` | shelf zone 不是 PRODUCTION → 400 | `pytest.raises(BizError)` |
| 24 | `pick_up_by_scan` | 正常领取 | 验证 shelf/worker/part 校验、状态机调用 |
| 25 | `pick_up_by_scan` | worker badge_code 不存在 → 404 | `pytest.raises(BizError)` |
| 26 | `pick_up_by_scan` | worker 未激活 → 400 | `pytest.raises(BizError)` |
| 27 | `pick_up_by_scan` | serial_no 找不到 → 404 | `pytest.raises(BizError)` |
| 28 | `pick_up_by_scan` | 状态不对（非 IN_PROCESS/PRODUCTION_SHELF） → 400 | `pytest.raises(BizError)` |
| 29 | `pick_up_by_scan` | holder 不匹配 → 400 | `pytest.raises(BizError)` |
| 30 | `scan_event` | RETURNED 正常放回 | 验证校验链 + 状态机 |
| 31 | `scan_event` | INSPECTED 正常送检 | 含 target_inspection_shelf_id |
| 32 | `scan_event` | INSPECTED 缺 target_inspection_shelf_id → 400 | `pytest.raises(BizError)` |
| 33 | `scan_event` | 无效 event_type → 400 | `pytest.raises(BizError)` |
| 34 | `pass_inspection` | 正常 | INSPECTION → READY_TO_SHIP |
| 35 | `deliver` | 正常 | READY_TO_SHIP → DELIVERED |
| 36 | `complete` | 正常 | DELIVERED → COMPLETED |
| 37 | `start_repair` | 正常 | → REPAIRING |
| 38 | `complete_repair` | 正常 | REPAIRING → IN_PROCESS |
| 39 | `complete_repair` | shelf 不存在/未激活/zone 不对 | 各 → 400/404 |
| 40 | `cancel` | 正常取消 | → CANCELLED |
| 41 | `_to_out` | 空列表 → 空列表 | `[]` in, `[]` out |
| 42 | `_to_out` | 正常转换含 customer_path | 验证输出字段正确 |
| 43 | `_broadcast` / `_broadcast_event` | broadcaster=None 时不抛异常 | 不抛异常 |
| 44 | `_check_parent_assembly` | part 不属于任何 assembly → noop | 不抛异常 |

### 2. AssemblyService (`tests/unit/test_assembly_service.py`)

| # | 方法 | 场景 | 断言 |
|---|------|------|------|
| 1 | `list_assemblies` | 正常分页查询 | 验证 repo 调用 |
| 2 | `get_assembly_detail` | assembly 存在 | 返回 AssemblyDetail |
| 3 | `get_assembly_detail` | assembly 不存在 → 404 | `pytest.raises(BizError)` |
| 4 | `get_assembly_for_child` | 正常反查 | 返回 AssemblyDetail |
| 5 | `get_assembly_for_child` | part 不存在 → 404 | `pytest.raises(BizError)` |
| 6 | `get_assembly_for_child` | part 不属于任何 assembly → 404 | `pytest.raises(BizError)` |
| 7 | `cancel_assembly` | 级联取消子件 | 验证子件 sm.cancel 被触发 |
| 8 | `cancel_assembly` | assembly 不存在 → 404 | `pytest.raises(BizError)` |
| 9 | `soft_delete_assembly` | 级联软删 + COS 清理 | 验证子件、文件、assembly 都被 soft_delete |
| 10 | `create_assembly` | 空 PDF → 400 | `pytest.raises(BizError)` |
| 11 | `create_assembly` | 非 PDF 扩展名 → 400 | `pytest.raises(BizError)` |
| 12 | `create_assembly` | customer 不存在 → 404 | `pytest.raises(BizError)` |

### 3. CustomerService (`tests/unit/test_customer_service.py`)

| # | 方法 | 场景 | 断言 |
|---|------|------|------|
| 1 | `list_customers` | 正常返回含 parent_name | 验证 repo 调用顺序 |
| 2 | `list_customers` | 空列表 | 返回 `[]` |

### 4. WorkerService (`tests/unit/test_worker_service.py`)

| # | 方法 | 场景 | 断言 |
|---|------|------|------|
| 1 | `list_workers` | 正常分页查询 | 验证 repo 调用 |
| 2 | `get_worker` | worker 存在 | 返回 WorkerOut |
| 3 | `get_worker` | worker 不存在 → 404 | `pytest.raises(BizError)` |
| 4 | `verify_badge` | 正常 | 返回 WorkerOut |
| 5 | `verify_badge` | badge_code 不存在 → 404 | `pytest.raises(BizError)` |
| 6 | `verify_badge` | worker 未激活 → 400 | `pytest.raises(BizError)` |
| 7 | `verify_badge` | badge_code 为空 → 404 | `pytest.raises(BizError)` |
| 8 | `create_worker` | 正常创建 | 验证 badge_code 唯一性检查 |
| 9 | `create_worker` | badge_code 重复 → 409 | `pytest.raises(BizError)` |
| 10 | `update_worker` | 正常更新 | 含 badge_code 变更 + 冲突检查 |
| 11 | `update_worker` | worker 不存在 → 404 | `pytest.raises(BizError)` |
| 12 | `deactivate` | 正常停用 | 验证 is_active=False, deleted_at 设置 |
| 13 | `reactivate` | 正常启用 | `include_deleted=True`，验证恢复 |

### 5. DrawingService (`tests/unit/test_drawing_service.py`)

| # | 方法 | 场景 | 断言 |
|---|------|------|------|
| 1 | `list_for_part` | part 存在 | 验证返回含 download_url |
| 2 | `list_for_part` | part 不存在 → 404 | `pytest.raises(BizError)` |
| 3 | `list_for_assembly` | assembly 存在 | 验证返回 |
| 4 | `list_for_assembly` | assembly 不存在 → 404 | `pytest.raises(BizError)` |
| 5 | `get_download_url` | file 存在 | 返回签名 URL |
| 6 | `get_download_url` | file 不存在 → 404 | `pytest.raises(BizError)` |
| 7 | `delete_file` | 正常软删 + 异步 COS 清理 | 验证 soft_delete + _safe_delete_cos 被调度 |
| 8 | `upload_to_part` | part 不存在 → 404 | `pytest.raises(BizError)` |
| 9 | `upload_to_assembly` | assembly 不存在 → 404 | `pytest.raises(BizError)` |
| 10 | `delete_files_silently` | 空列表 → noop | 不抛异常 |

### 6. ShelfService (`tests/unit/test_shelf_service.py`)

| # | 方法 | 场景 | 断言 |
|---|------|------|------|
| 1 | `list_shelves` | 正常分页查询 | 验证 repo 调用 |
| 2 | `get_shelf` | shelf 存在 | 返回 ShelfOut |
| 3 | `get_shelf` | shelf 不存在 → 404 | `pytest.raises(BizError)` |
| 4 | `create_shelf` | 正常创建 | 验证 code 唯一性 |
| 5 | `create_shelf` | code 重复 → 409 | `pytest.raises(BizError)` |
| 6 | `create_shelf` | zone 无效 → 400 | `pytest.raises(BizError)` |
| 7 | `update_shelf` | 正常更新 | 验证 update 调用 |
| 8 | `soft_delete_shelf` | 正常（无活跃零件） | 验证软删 |
| 9 | `soft_delete_shelf` | 有活跃零件 → 409 | `pytest.raises(BizError)` |

### 7. UserService (`tests/unit/test_user_service.py`)

| # | 方法 | 场景 | 断言 |
|---|------|------|------|
| 1 | `list_users` | 正常分页查询 | 验证 repo 调用 |
| 2 | `get_user` | 正常 | 返回 UserOut |
| 3 | `get_user` | 不存在 → 404 | `pytest.raises(BizError)` |
| 4 | `create_user` | 正常创建 | 验证 username 唯一性、密码 hash |
| 5 | `create_user` | username 重复 → 409 | `pytest.raises(BizError)` |
| 6 | `update_user` | 正常更新 | 含密码变更 |
| 7 | `soft_delete_user` | 正常软删 | 验证 soft_delete 调用 |
| 8 | `add_role` | SHELF_ACCOUNT 正常 | 验证 scope 校验 |
| 9 | `add_role` | SHELF_ACCOUNT scope 不对 → 400 | `pytest.raises(BizError)` |
| 10 | `remove_role` | 正常 | 验证 soft_delete |
| 11 | `remove_role` | role 不属于该 user → 404 | `pytest.raises(BizError)` |

### 8. AuthService (`tests/unit/test_auth_service.py`)

| # | 方法 | 场景 | 断言 |
|---|------|------|------|
| 1 | `login` | 正常登录 | 返回 token + CurrentUserOut |
| 2 | `login` | 密码错误 → 401 | `pytest.raises(BizError)` |
| 3 | `login` | 用户不存在 → 401 | `pytest.raises(BizError)` |
| 4 | `login` | 用户未激活 → 401 | `pytest.raises(BizError)` |
| 5 | `login` | 无角色 → 403 | `pytest.raises(BizError)` |
| 6 | `me` | 正常返回当前用户 | 返回 CurrentUserOut |
| 7 | `me` | 用户已停用 → 401 | `pytest.raises(BizError)` |

## 实施顺序

1. **PartService** — 最复杂，测试用例最多（~44 个），优先开始
2. **WorkerService** — 简单独立，可与 PartService 并行
3. **CustomerService** — 最简单，快速完成
4. **DrawingService** — 中等复杂度，需要 mock COS
5. **ShelfService** — 中等复杂度
6. **UserService** — 中等复杂度
7. **AuthService** — 依赖 menu 树，需要 mock JWT
8. **AssemblyService** — 最复杂（依赖 PartService + DrawingService + COS），最后做
