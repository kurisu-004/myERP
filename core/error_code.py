from enum import IntEnum


class ErrCode(IntEnum):
    SUCCESS = 0

    BAD_REQUEST = 40000
    VALIDATION_ERROR = 40001
    UNAUTHORIZED = 40100
    FORBIDDEN = 40300
    NOT_FOUND = 40400
    CONFLICT = 40900

    INTERNAL_ERROR = 50000
    DATABASE_ERROR = 50001

    BIZ_USER_NOT_FOUND = 20001
    BIZ_USER_DUPLICATE = 20002
    BIZ_ORDER_NOT_FOUND = 20003

    # ---- 零件（Part / SerialCounter）----
    BIZ_PART_NOT_FOUND = 20101
    BIZ_CUSTOMER_NOT_FOUND = 20102
    BIZ_INVALID_TRANSITION = 20103
    BIZ_INVALID_VALUE = 20104
    BIZ_PART_SERIAL_EXHAUSTED = 20105  # 序列号池耗尽（>5000 活跃/PREFIX）
    # 20106/20107 留空，避免与未来 PART 相关码冲突
    BIZ_SERIAL_PREFIX_UNKNOWN = 20108  # t_serial_counter 找不到对应 prefix

    BIZ_WORKER_NOT_FOUND = 20201
    BIZ_WORKER_INACTIVE = 20202

    # ---- 装配体（图文档 + 子件）----
    # 203xx：装配体相关
    BIZ_ASSEMBLY_NOT_FOUND = 20301
    BIZ_ASSEMBLY_BAD_CUSTOMER = 20302  # 客户节点不允许（一级集团 / 不存在）

    # ---- 图纸文件（t_drawing_file + COS）----
    # 204xx：图纸文件相关
    BIZ_DRAWING_FILE_NOT_FOUND = 20401
    BIZ_DRAWING_FILE_BAD_TYPE = 20402   # 扩展名不在 COS_ALLOWED_TYPES 白名单
    BIZ_DRAWING_FILE_TOO_LARGE = 20403  # 文件大小 ≤0 或 > cos_max_file_size_bytes
    BIZ_DRAWING_UPLOAD_FAILED = 20404   # COS SDK 抛错（含 put/delete/get/head）

    # ---- 货架（t_shelf）----
    # 205xx：货架相关
    BIZ_SHELF_NOT_FOUND = 20501
    BIZ_SHELF_DUPLICATE_CODE = 20502
    BIZ_SHELF_IN_USE = 20503            # 还有 IN_PROCESS/INSPECTION 零件 → 拒软删

    # ---- 账号（t_user / t_user_role）----
    # 206xx：账号相关
    BIZ_USER_ACCOUNT_NOT_FOUND = 20601
    BIZ_USER_DUPLICATE_USERNAME = 20602
    BIZ_USER_INACTIVE = 20603
    BIZ_USER_ROLE_DUPLICATE = 20604
    BIZ_USER_ROLE_NOT_FOUND = 20605
    BIZ_USER_NO_ROLE = 20606            # 账号存在但没有任何 role

    # ---- 鉴权 (401 / 403) ----
    BIZ_AUTH_INVALID = 40101            # 401  Bearer 无效或密码错
    BIZ_AUTH_TOKEN_EXPIRED = 40102      # 401  JWT 过期
    BIZ_AUTH_SHELF_MISMATCH = 40301     # 403  SHELF_ACCOUNT 账号操作的 shelf 与 JWT 中不一致

    # ---- 工序（t_process）----
    # 208xx：工序相关
    BIZ_PROCESS_NOT_FOUND = 20801
    BIZ_PROCESS_DUPLICATE_CODE = 20802
    BIZ_PROCESS_IN_USE = 20803          # 仍有 part.next_process_id 或 mapping 引用时拒软删

    # ---- 工种（t_work_type）----
    # 209xx：工种相关
    BIZ_WORK_TYPE_NOT_FOUND = 20901
    BIZ_WORK_TYPE_DUPLICATE_CODE = 20902
    BIZ_WORK_TYPE_IN_USE = 20903        # 仍有 worker.work_type_id 或 mapping 引用时拒软删

    # ---- 客户（t_customer）----
    # 20109 补到 201xx 段（客户相关），与 BIZ_CUSTOMER_NOT_FOUND 同段。
    BIZ_CUSTOMER_IN_USE = 20109         # 还有 active 子节点 / 被 part 或 assembly 引用 → 拒软删

    # ---- 申请人（t_applicant）----
    # 210xx：申请人相关
    BIZ_APPLICANT_NOT_FOUND = 21001
    BIZ_APPLICANT_DUPLICATE_NAME = 21002   # 同一一级客户下重名（DB partial unique 兜底）
    BIZ_APPLICANT_BAD_CUSTOMER = 21003     # customer 不存在或不是一级
    BIZ_APPLICANT_IN_USE = 21004           # 被未软删 part.applicant_name 引用 → 拒软删
