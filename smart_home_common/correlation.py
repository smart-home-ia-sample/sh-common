import uuid

CORRELATION_HEADER = "X-Correlation-Id"
REQUEST_ID_HEADER = "X-Request-Id"


def new_id() -> str:
    return str(uuid.uuid4())


def get_or_create_correlation_id(existing: str | None) -> str:
    return existing if existing else new_id()
