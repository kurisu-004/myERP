from snowflake import SnowflakeGenerator

from core.config import settings

_generator = SnowflakeGenerator(
    instance=settings.snowflake_instance,
    seq=settings.snowflake_seq,
    epoch=settings.snowflake_epoch,
)


def new_id() -> int:
    return next(_generator)