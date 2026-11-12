from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class LatLng(_message.Message):
    __slots__ = ("latitude", "longitude")
    LATITUDE_FIELD_NUMBER: _ClassVar[int]
    LONGITUDE_FIELD_NUMBER: _ClassVar[int]
    latitude: float
    longitude: float
    def __init__(self, latitude: _Optional[float] = ..., longitude: _Optional[float] = ...) -> None: ...

class BoundingBox(_message.Message):
    __slots__ = ("south_west", "north_east")
    SOUTH_WEST_FIELD_NUMBER: _ClassVar[int]
    NORTH_EAST_FIELD_NUMBER: _ClassVar[int]
    south_west: LatLng
    north_east: LatLng
    def __init__(self, south_west: _Optional[_Union[LatLng, _Mapping]] = ..., north_east: _Optional[_Union[LatLng, _Mapping]] = ...) -> None: ...
