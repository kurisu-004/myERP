from enum import IntEnum


class ErrCode(IntEnum):
    SUCCESS = 0

    BAD_REQUEST = 40000
    VALIDATION_ERROR = 40001
    UNAUTHORIZED = 40100
    FORBIDDEN = 40300
    NOT_FOUND = 40400
    CONFLICT = 40900
    BIZ_VERSION_CONFLICT = 40901   # 乐观锁冲突：当前 version 与 DB 不一致
    BIZ_REQUEST_TOO_LARGE = 41301  # 413  请求体超过 settings.max_request_body_size_bytes（RequestSizeLimitMiddleware 在 multipart 解析前拦截）

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
    BIZ_PART_PRICE_LOCKED_BY_ASSEMBLY = 20110  # 2026-07-24：父装配体已设总价，子件不能再单独改价（PartService.update_part 抛）

    BIZ_WORKER_NOT_FOUND = 20201
    BIZ_WORKER_INACTIVE = 20202

    # ---- 装配体（图文档 + 子件）----
    # 203xx：装配体相关
    BIZ_ASSEMBLY_NOT_FOUND = 20301
    BIZ_ASSEMBLY_BAD_CUSTOMER = 20302  # 客户节点不允许（一级集团 / 不存在）
    BIZ_ASSEMBLY_TOO_MANY_CHILDREN = 20303  # 子件 > 99，序列号 {serial}-{i:02d} 派生失败

    # ---- 图纸文件（t_drawing_file + COS）----
    # 204xx：图纸文件相关
    BIZ_DRAWING_FILE_NOT_FOUND = 20401
    BIZ_DRAWING_FILE_BAD_TYPE = 20402   # 扩展名不在 COS_ALLOWED_TYPES 白名单
    BIZ_DRAWING_FILE_TOO_LARGE = 20403  # 文件大小 ≤0 或 > cos_max_file_size_bytes
    BIZ_DRAWING_UPLOAD_FAILED = 20404   # COS SDK 抛错（含 put/delete/get/head）

    # ---- 零件文件（t_part_file，2026-07-10 起统一 5 类）----
    # 211xx：零件文件相关（取代/扩展 204xx）
    BIZ_PART_FILE_NOT_FOUND = 21101
    BIZ_PART_FILE_BAD_TYPE = 21102       # 扩展名与 kind 不匹配
    BIZ_PART_FILE_TOO_LARGE = 21103      # 文件大小 ≤0 或 > cos_max_file_size_bytes
    BIZ_PART_FILE_UPLOAD_FAILED = 21104  # COS SDK 抛错
    BIZ_PART_FILE_OWNER_NOT_FOUND = 21105  # polymorphic owner (part/assembly) 不存在
    BIZ_CNC_PROGRAM_REQUIRED = 21106     # 下发前必须上传 G 代码
    BIZ_CNC_SETUP_SHEET_REQUIRED = 21107 # 下发前必须上传设定单
    BIZ_PART_FILE_DUPLICATE = 21108      # 2026-07-14：同 part+kind+content_sha256 撞唯一索引（并发兜底）
    BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED = 21109  # 2026-07-17：root customer prefix 未在 DELIVERY_NOTE_TEMPLATE_BY_PREFIX 配置
    BIZ_DELIVERY_PARTS_MULTIPLE_CUSTOMERS = 21110  # 所选零件分属多个 L1 root customer
    BIZ_DELIVERY_PART_STATUS_INVALID = 21111       # 所选零件状态非 READY_TO_SHIP
    BIZ_DELIVERY_TEMPLATE_TOO_MANY_PARTS = 21112   # 2026-07-20：所选零件超过模板数据行容量（法 14 / 路 25）

    # ---- 货架（t_shelf）----
    # 205xx：货架相关
    BIZ_SHELF_NOT_FOUND = 20501
    BIZ_SHELF_DUPLICATE_CODE = 20502
    BIZ_SHELF_IN_USE = 20503            # 还有 IN_PROCESS/INSPECTION 零件 → 拒软删
    BIZ_SHELF_PROCESS_SHELF_NOT_FOUND = 20504  # 货架不存在
    BIZ_SHELF_PROCESS_PROCESS_NOT_FOUND = 20505  # 工序不存在
    BIZ_SHELF_NO_MATCH_FOR_PROCESS = 20506  # 没有 active 货架映射指定 process → RETURN picker 无候选
    BIZ_SHELF_PROCESS_NOT_MAPPED = 20507  # 货架未映射该工序（place/release/receive/complete_repair 422）

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
    BIZ_AUTH_REFRESH_INVALID = 40103    # 401  refresh token 失效 / 版本不匹配 / 用户已停用
    BIZ_AUTH_OLD_PASSWORD_MISMATCH = 40104  # 401  修改密码时旧密码错误
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

    # ---- 外协公司（t_outsource_company + t_outsource_company_process）----
    # 212xx：外协公司相关（2026-07-15 新增）
    BIZ_OUTSOURCE_COMPANY_NOT_FOUND = 21201
    BIZ_OUTSOURCE_COMPANY_DUPLICATE = 21202    # uk_t_outsource_company_name 部分唯一兜底
    BIZ_OUTSOURCE_COMPANY_BAD_PROCESS = 21203  # 工序不存在 / 不是 OUTSOURCE/INHOUSE 类别
    BIZ_OUTSOURCE_PROCESS_NOT_MAPPED = 21204   # 公司未映射该 OUTSOURCE 工序
    BIZ_OUTSOURCE_COMPANY_IN_USE = 21205       # 被 part OUTSOURCE 引用 / 仍映射工序
    BIZ_PART_NOT_OUTSOURCEABLE = 21206         # 当前状态不允许发送外协（兜底，正常流不该撞）
    BIZ_OUTSOURCE_DIRECT_REQUIRES_C2_SHELF = 21207  # 直接发送外协要求零件位于 C2 / 生产/外协 C2 货架

    # ---- 外协报价（t_outsource_quote，2026-07-16 新增）----
    # 213xx：外协报价相关
    BIZ_OUTSOURCE_QUOTE_NOT_FOUND = 21301
    BIZ_OUTSOURCE_QUOTE_INVALID_TRANSITION = 21302  # 当前状态不允许此操作
    BIZ_OUTSOURCE_QUOTE_DUPLICATE = 21303           # 同 (part,company,process) 已存在活跃报价
    BIZ_OUTSOURCE_QUOTE_NOT_APPROVED = 21307        # send_to_outsource 找不到该 tuple 的 APPROVED 报价

    # ---- 送货单（t_delivery_note，2026-07-22 新增）----
    # 214xx：送货单相关
    BIZ_DELIVERY_NOTE_NOT_FOUND = 21401           # 404  找不到指定的送货单
    BIZ_DELIVERY_NOTE_INVALID_TRANSITION = 21402  # 400  当前状态不允许此操作（如 PICKED_UP 后不能再 recall）
    BIZ_DELIVERY_NOTE_NOT_DRAFT = 21403           # 400  非 DRAFT 状态不能 soft_delete
    BIZ_DELIVERY_NOTE_NOT_SUBMITTED = 21404       # 400  非 SUBMITTED 状态不能 recall / pickup
    BIZ_DELIVERY_NOTE_PART_NOT_READY = 21405      # 400 零件状态非 READY_TO_SHIP（submit / pickup 时）
    BIZ_DELIVERY_NOTE_PART_ALREADY_ASSIGNED = 21406  # 400  零件已在另一张送货单上
    BIZ_DELIVERY_NOTE_PARTS_MULTIPLE_CUSTOMERS = 21407  # 400 同一单内混客户（与老 21110 同义，21407 便于按模块检索）
    BIZ_DELIVERY_NOTE_SCAN_MISMATCH = 21408       # 400  扫码的 serial_no 不在本单范围内
    BIZ_DELIVERY_NOTE_DRIVER_INVALID = 21409      # 400  司机非送货司机 / 不活跃
    BIZ_DELIVERY_NOTE_SCAN_INCOMPLETE = 21410     # 400  pickup 时还没扫齐
    BIZ_DELIVERY_NOTE_INVALID_VALUE = 21411       # 400 空单 / 等其他非法入参
    BIZ_DELIVERY_NOTE_PARTS_LOCKED = 21412        # 409  SUBMITTED/PICKED_UP 后禁止 add_parts / remove_parts；想改动必须先 recall → DRAFT（2026-07-23 Bug 5）
