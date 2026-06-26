# [系统/模块名] 数据设计

> 创建日期：YYYY-MM-DD
> 数据库：[MySQL 8.0 / PostgreSQL 15 / MongoDB 6.0 / ...]
> 设计者：[填名字]

## 1. 业务概述

[2-3 段说清楚：
- 做什么（核心功能）
- 给谁用（用户群体）
- 关键场景（最常见的 3-5 个用户操作流程）]

## 2. 核心实体

| 实体 | 说明 |
|---|---|
| [User 用户] | 系统的使用者，登录、查看、下单等 |
| [Order 订单] | 用户下单产生的交易单 |
| [Product 商品] | 商家上架售卖的对象 |
| ... | ... |

## 3. ER 图

```mermaid
erDiagram
    [TODO: 插入 Mermaid ER 图]
```

## 4. 表结构详情

### 4.1 [表名]

**用途**：[一句话说明这张表存什么]

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| id | bigint unsigned | 是 | AUTO_INCREMENT | 主键 |
| [field] | [type] | 是/否 | [default] | [说明] |
| created_at | datetime | 是 | CURRENT_TIMESTAMP | 创建时间 |
| updated_at | datetime | 是 | CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

**索引**：
- `PRIMARY KEY (id)`
- `UNIQUE KEY uk_xxx (field)`
- `KEY idx_xxx (field1, field2)`

**外键**：
- `[fk_field]` → `[ref_table].[ref_field]`，`[ON DELETE/ON UPDATE]`

**业务规则**：
- [自然语言描述的约束、状态机、计算公式]

### 4.2 [下一张表]
...

## 5. 关系说明

| 关系 | 基数 | 业务含义 |
|---|---|---|
| User → Order | 1 对多 | 一个用户可以下多个订单 |
| Order → OrderItem | 1 对多 | 一个订单包含多个订单项 |
| Product ↔ Order | 多对多（通过 OrderItem） | 一个商品可以出现在多个订单中 |
| ... | ... | ... |

## 6. 扩展性考虑

[描述未来可能的变化，以及当前设计如何应对：]
- 业务增长：[如订单量上升需要分表]
- 功能扩展：[如新增直播带货需要扩展 Product 表]
- 性能优化：[如评论数大需要单独存储]

---

## 7. MongoDB 集合 Schema（仅文档型数据库需要）

### 7.1 [集合名]

**用途**：[说明]

```json
{
  "$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "..."],
    "properties": {
      "_id": {
        "bsonType": "objectId",
        "description": "主键"
      },
      "[field]": {
        "bsonType": "string",
        "description": "[说明]"
      },
      "[nested_field]": {
        "bsonType": "object",
        "properties": {
          "[sub_field]": {"bsonType": "string"}
        }
      },
      "[array_field]": {
        "bsonType": "array",
        "items": {"bsonType": "string"}
      }
    }
  }
}
```

**索引建议**：
- `{ "[field1]": 1, "[field2]": -1 }` — [查询场景]
- ...

---

## 附录 A: 字段命名规范

[记录本项目采用的命名约定]

## 附录 B: 状态机定义

[如有状态字段，用状态机图说明转换关系]
