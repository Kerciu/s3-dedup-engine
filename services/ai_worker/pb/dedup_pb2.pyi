from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class ImageChunk(_message.Message):
    __slots__ = ("image_key", "total_size", "data")
    IMAGE_KEY_FIELD_NUMBER: _ClassVar[int]
    TOTAL_SIZE_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    image_key: str
    total_size: int
    data: bytes
    def __init__(
        self,
        image_key: _Optional[str] = ...,
        total_size: _Optional[int] = ...,
        data: _Optional[bytes] = ...,
    ) -> None: ...

class ProcessImageResponse(_message.Message):
    __slots__ = (
        "status",
        "image_key",
        "quality_score",
        "distance",
        "existing_image_key",
    )
    STATUS_FIELD_NUMBER: _ClassVar[int]
    IMAGE_KEY_FIELD_NUMBER: _ClassVar[int]
    QUALITY_SCORE_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_FIELD_NUMBER: _ClassVar[int]
    EXISTING_IMAGE_KEY_FIELD_NUMBER: _ClassVar[int]
    status: str
    image_key: str
    quality_score: float
    distance: float
    existing_image_key: str
    def __init__(
        self,
        status: _Optional[str] = ...,
        image_key: _Optional[str] = ...,
        quality_score: _Optional[float] = ...,
        distance: _Optional[float] = ...,
        existing_image_key: _Optional[str] = ...,
    ) -> None: ...
