import uuid


def new_id() -> int:
    return uuid.uuid4().int & 0x7FFFFFFFFFFFFFFF
