from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Postgres ----
    database_url: str = Field(alias="DATABASE_URL")

    # ---- Snowflake ID ----
    snowflake_instance: int = Field(
        default=0, alias="SNOWFLAKE_INSTANCE", ge=0, le=1023
    )
    snowflake_seq: int = Field(
        default=0, alias="SNOWFLAKE_SEQ", ge=0, le=4095
    )
    snowflake_epoch: int = Field(
        default=1735689600000, alias="SNOWFLAKE_EPOCH"
    )

    # ---- JWT (t_user 登录) ----
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(
        default=720, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES", ge=1
    )
    # 2026-07-10 新增：refresh token TTL（双 token 轮转方案）。
    # 默认 7 天（10080 min），通过 JWT_REFRESH_TOKEN_EXPIRE_MINUTES 覆盖。
    jwt_refresh_token_expire_minutes: int = Field(
        default=10080, alias="JWT_REFRESH_TOKEN_EXPIRE_MINUTES", ge=1
    )
    jwt_issuer: str = Field(default="myerp", alias="JWT_ISSUER")

    # ---- Dev seed (t_user / t_shelf 迁移后自动 seed) ----
    shelf_seed_on_migrate: bool = Field(
        default=False, alias="SHELF_SEED_ON_MIGRATE"
    )

    # ---- 腾讯云 COS（图纸上传 / 下载 / 预签）----
    # 本进程用长期 SecretId/Key 调 SDK（仅后端内部用）；前端要走 STS 临时凭证。
    cos_secret_id: str = Field(alias="COS_SECRET_ID")
    cos_secret_key: str = Field(alias="COS_SECRET_KEY")
    cos_region: str = Field(default="ap-guangzhou", alias="COS_REGION")
    cos_bucket: str = Field(alias="COS_BUCKET")
    cos_scheme: str = Field(default="https", alias="COS_SCHEME")
    # 留空则走 region 默认 endpoint；私有化 / 加速域名可填完整 URL。
    cos_endpoint: str = Field(default="", alias="COS_ENDPOINT")
    # 上传 key 公共前缀，所有图纸 key 都挂在这之下。
    cos_upload_prefix: str = Field(default="drawings/", alias="COS_UPLOAD_PREFIX")
    # GET 预签 URL 默认有效期（秒）。前端轮询 / 预览场景给 15 min 即可。
    cos_presign_expire_seconds: int = Field(
        default=900, alias="COS_PRESIGN_EXPIRE", ge=1
    )
    # 单文件大小硬上限（字节）。≥ 5GB 走 upload_file 分块上传；这里给 100MB。
    cos_max_file_size_bytes: int = Field(
        default=100 * 1024 * 1024, alias="COS_MAX_FILE_SIZE", ge=1
    )
    # 注：扩展名白名单自 2026-07-14 起由 `core/_file_kind_policy.py::ALLOWED_EXTS_BY_KIND`
    # 统一管控（kind → set of exts），不再用逗号分隔 env；本字段保留仅做向后兼容。
    cos_allowed_types: str = Field(
        default="", alias="COS_ALLOWED_TYPES"
    )

    # ---- 送货单 Excel 模板（PR-F 2026-07-17 重设计；2026-07-20 切换到 template/ 新模板）----
    # 按 L1 客户的序列号前缀（A-Z）映射各自的 xlsx 模板路径。
    # service 层根据所选零件所属 L1 root 的 serial_prefix 选对应模板；
    # 调用方也可通过 API 显式传 `template` 字段覆盖自动分发。
    # 未映射的前缀 → 400 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED。
    # 模板由用户提供（含公司抬头 / 列头 / 签字栏），代码只填值。
    # 2026-07-20 起切换到 `template/` 下的新模板（法 = Sheet1 / 路 = 杏南）；
    # 老的 `docs/example/送货单_*.xlsx` 不再使用。
    delivery_note_template_by_prefix: dict[str, str] = Field(
        default={
            "F": "template/delivery_note_fala.xlsx",
            "L": "template/delivery_note_luda.xlsx",
        },
        alias="DELIVERY_NOTE_TEMPLATE_BY_PREFIX",
        description='{"F": "template/delivery_note_fala.xlsx", "L": "template/delivery_note_luda.xlsx"}',
    )

    # ---- DELIVERED → COMPLETED 自动完成（PR-D 2026-07-10）----
    # 最近一次发货事件超过 N 天 且 中间无返修 → 自动 COMPLETED。
    auto_complete_threshold_days: int = Field(
        default=7, alias="AUTO_COMPLETE_THRESHOLD_DAYS", ge=1,
    )
    # 后台循环间隔（小时）。
    auto_complete_interval_hours: int = Field(
        default=24, alias="AUTO_COMPLETE_INTERVAL_HOURS", ge=1,
    )


settings = Settings()
