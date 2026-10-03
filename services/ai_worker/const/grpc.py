"""gRPC server and image-stream spooling constants."""

from typing import Final

GRPC_PORT: Final[int] = 50051
GRPC_MAX_WORKERS: Final[int] = 4
GRPC_MAX_MESSAGE_BYTES: Final[int] = 8 * 1024 * 1024
GRPC_SHUTDOWN_GRACE_SECONDS: Final[float] = 5.0
SPOOL_MAX_BYTES: Final[int] = 20 * 1024 * 1024
