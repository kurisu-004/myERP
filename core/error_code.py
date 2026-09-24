from enum import IntEnum


class ErrCode(IntEnum):
    """2026-09-24 PR-3 重构：dormant 业务错误码全部下线。

    仅保留活跃 service / handler / middleware 实际消费的枚举值：

    - 通用：`SUCCESS` / `BAD_REQUEST` / `VALIDATION_ERROR` / `UNAUTHORIZED` /
      `FORBIDDEN` / `NOT_FOUND` / `CONFLICT` / `BIZ_VERSION_CONFLICT` /
      `BIZ_REQUEST_TOO_LARGE` / `INTERNAL_ERROR` / `DATABASE_ERROR`
    - 业务默认：`BIZ_USER_NOT_FOUND`（`BizError.__init__` 默认值；保守保留）
    - 打印 / 送货单：`BIZ_PART_NOT_FOUND` / `BIZ_CUSTOMER_NOT_FOUND` /
      `BIZ_INVALID_TRANSITION` / `BIZ_INVALID_VALUE` /
      `BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED` / `BIZ_DELIVERY_PRINT_BAD_ORDER` /
      `BIZ_DELIVERY_NOTE_NOT_FOUND`
    - STS：`BIZ_STS_GRANT_FAILED` / `BIZ_STS_PREFIX_INVALID`

    已删除（2026-09-19 IAM 域迁出 + 2026-09-24 PR-3 dormant 业务下线）：
    IAM (`BIZ_AUTH_*` / `BIZ_USER_ACCOUNT_NOT_FOUND` / `BIZ_USER_DUPLICATE_USERNAME`
    / `BIZ_USER_INACTIVE` / `BIZ_USER_ROLE_DUPLICATE` / `BIZ_USER_ROLE_NOT_FOUND` /
    `BIZ_USER_DUPLICATE` / `BIZ_USER_NO_ROLE`)、外协 (`BIZ_OUTSOURCE_*`)、
    装配体 (`BIZ_ASSEMBLY_*`)、批次 (`BIZ_PART_BATCH_*`)、送货单过渡码
    (`BIZ_DELIVERY_NOTE_*` 除 `BIZ_DELIVERY_NOTE_NOT_FOUND`)、
    跳序 (`BIZ_PART_BATCH_NOT_FOUND` 等)、工人 / 货架 / 工序 / 工种、
    applicant / drawing 旧码等。
    """

    SUCCESS = 0

    BAD_REQUEST = 40000
    VALIDATION_ERROR = 40001
    UNAUTHORIZED = 40100
    FORBIDDEN = 40300
    NOT_FOUND = 40400
    CONFLICT = 40900
    BIZ_VERSION_CONFLICT = 40901  # 乐观锁冲突：当前 version 与 DB 不一致
    BIZ_REQUEST_TOO_LARGE = (
        41301  # 413  请求体超过 settings.max_request_body_size_bytes
    )

    INTERNAL_ERROR = 50000
    DATABASE_ERROR = 50001

    # ---- 默认值：BizError.__init__ 兜底 ----
    BIZ_USER_NOT_FOUND = 20001

    # ---- 零件 ----
    BIZ_PART_NOT_FOUND = 20101
    BIZ_CUSTOMER_NOT_FOUND = 20102
    BIZ_INVALID_TRANSITION = 20103
    BIZ_INVALID_VALUE = 20104

    # ---- COS 上传 ----
    BIZ_DRAWING_UPLOAD_FAILED = 20404  # COS SDK 抛错（含 put/delete/get/head）

    # ---- 送货单打印 ----
    BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED = 21109  # root customer prefix 未配置模板
    BIZ_DELIVERY_PRINT_BAD_ORDER = 21113  # custom_order 含非法 batch id 或漏行

    # ---- 送货单（service.delivery_note_print / api.v1.delivery_note_print）----
    BIZ_DELIVERY_NOTE_NOT_FOUND = 21401  # 404  找不到指定的送货单

    # ---- STS（service.sts / core.sts / api.v1.sts）----
    BIZ_STS_GRANT_FAILED = 21502  # STS 临时凭证签发失败
    BIZ_STS_PREFIX_INVALID = 21503  # STS prefix 非法（未以 tmp/ 开头）
