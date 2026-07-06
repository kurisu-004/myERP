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
    # 允许的扩展名（逗号分隔）。service 层做白名单校验。
    # 含图纸类（pdf/step/stp/dwg/dxf）与 CNC G 代码类（nc/tap/cnc/mpf/ngc）。
    cos_allowed_types: str = Field(
        default="pdf,step,stp,dwg,dxf,nc,tap,cnc,mpf,ngc", alias="COS_ALLOWED_TYPES"
    )


settings = Settings()
print(settings)
