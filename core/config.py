from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(alias="DATABASE_URL")
    test_database_url: str | None = Field(default=None, alias="TEST_DATABASE_URL")

    snowflake_instance: int = Field(
        default=0, alias="SNOWFLAKE_INSTANCE", ge=0, le=1023
    )
    snowflake_seq: int = Field(
        default=0, alias="SNOWFLAKE_SEQ", ge=0, le=4095
    )
    snowflake_epoch: int = Field(
        default=1735689600000, alias="SNOWFLAKE_EPOCH"
    )

    jwt_secret: str = Field(default="dev-secret-change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=8 * 60, alias="JWT_EXPIRE_MINUTES")
    superadmin_username: str = Field(default="superadmin", alias="SUPERADMIN_USERNAME")


settings = Settings()
