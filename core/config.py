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

    # ---- 单次请求体大小上限（2026-07-22 新增；与 nginx client_max_body_size 对齐）----
    # 用于 RequestSizeLimitMiddleware 在 multipart 解析之前拦截，避免批量 PDF (300 MB)
    # 全量进内存导致 OOM；前端 / 后端共享同一个值，便于两处都看 .env 调整。
    max_request_body_size_bytes: int = Field(
        default=300 * 1024 * 1024, alias="MAX_REQUEST_BODY_SIZE", ge=1,
        description=(
            "单次请求体大小上限（字节）；批量 PDF 上传 /parts/batch-with-pdfs 等 "
            "大 body 端点的 nginx / 后端双层 413 守卫共享值。"
        ),
    )

    # ---- 送货单 Excel 模板（PR-F 2026-07-17 重设计；2026-07-20 切换到 template/ 新模板）----
    # 按 L1 客户的序列号前缀（A-Z）映射各自的 xlsx 模板路径。
    # service 层根据所选零件所属 L1 root 的 serial_prefix 选对应模板；
    # 调用方也可通过 API 显式传 `template` 字段覆盖自动分发。
    # 未映射的前缀 → 400 BIZ_DELIVERY_TEMPLATE_NOT_CONFIGURED。
    # 模板由用户提供（含公司抬头 / 列头 / 签字栏），代码只填值。
    # 2026-07-20 起切换到 `template/` 下的新模板（法 = Sheet1 / 路 = 杏南）；
    # 2026-07-24 法拉换新模板（洪升宏 26.7.24，单份 10 行 R3-R12，超出自动分页）。
    # 老的 `docs/example/送货单_*.xlsx` 不再使用。
    delivery_note_template_by_prefix: dict[str, str] = Field(
        default={
            "F": "template/delivery_note_fala.xlsx",
            "L": "template/delivery_note_luda.xlsx",
        },
        alias="DELIVERY_NOTE_TEMPLATE_BY_PREFIX",
        description='{"F": "template/delivery_note_fala.xlsx", "L": "template/delivery_note_luda.xlsx"}',
    )

    # ---- 容器可用 CPU 核心数（2026-07-31 打印性能优化引入）----
    # 换服务器时只改 .env 里 APP_CPU_CORES；printing.py 等核心数相关参数
    # 通过 Settings 的 print_render_workers / print_download_concurrency 派生。
    # uvicorn 维持单 worker（4G 内存 + asyncio 模型，多 worker 徒增内存）；
    # 如要开多 worker 单独引入 UVICORN_WORKERS 配置。
    app_cpu_cores: int = Field(
        default=4, alias="APP_CPU_CORES", ge=1, le=128,
        description="容器可用 CPU 核心数；打印渲染/下载并发依此派生",
    )

    # ---- 打印正面页 L1 本地磁盘缓存（2026-07-31 引入）----
    # 不可写时（如 read-only rootfs / printcache 卷未挂载）自动降级为仅 L2 COS，
    # 不影响功能，只损失一次跨网下载延迟。
    print_cache_dir: str = Field(
        default="/app/.cache/print", alias="PRINT_CACHE_DIR",
        description="打印正面页 L1 本地磁盘缓存目录；不可写时自动降级为仅 L2 COS",
    )
    print_cache_max_bytes: int = Field(
        default=1024 * 1024 * 1024, alias="PRINT_CACHE_MAX_BYTES", ge=0,
        description="L1 本地磁盘缓存上限字节数（LRU 按 mtime 淘汰）",
    )

    @property
    def print_render_workers(self) -> int:
        """打印渲染线程并发上限（asyncio.to_thread 数量）。"""
        return max(1, self.app_cpu_cores)

    @property
    def print_download_concurrency(self) -> int:
        """打印路径 COS 下载并发上限。

        公式与原 `_MAX_CONCURRENT_DOWNLOADS=8` 注释保持等价：
        4 核 → 8、2 核 → 4（max 兜底）、8 核 → 16、16 核 → 32。
        """
        return max(4, 2 * self.app_cpu_cores)

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
