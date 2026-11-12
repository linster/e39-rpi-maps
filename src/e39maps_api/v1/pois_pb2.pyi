from e39maps_api.v1 import common_pb2 as _common_pb2
from google.protobuf import empty_pb2 as _empty_pb2
from google.protobuf import field_mask_pb2 as _field_mask_pb2
from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PoiSource(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    POI_SOURCE_UNSPECIFIED: _ClassVar[PoiSource]
    POI_SOURCE_USER: _ClassVar[PoiSource]
    POI_SOURCE_MAP: _ClassVar[PoiSource]

class PoiCategory(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    POI_CATEGORY_UNSPECIFIED: _ClassVar[PoiCategory]
    POI_CATEGORY_FUEL: _ClassVar[PoiCategory]
    POI_CATEGORY_RESTAURANT: _ClassVar[PoiCategory]
    POI_CATEGORY_SKI_AREA: _ClassVar[PoiCategory]
    POI_CATEGORY_MUSEUM: _ClassVar[PoiCategory]
    POI_CATEGORY_AIRPORT: _ClassVar[PoiCategory]
    POI_CATEGORY_PARK: _ClassVar[PoiCategory]
    POI_CATEGORY_TRAILHEAD: _ClassVar[PoiCategory]
    POI_CATEGORY_CAR_PARTS: _ClassVar[PoiCategory]
    POI_CATEGORY_JUNKYARD: _ClassVar[PoiCategory]
POI_SOURCE_UNSPECIFIED: PoiSource
POI_SOURCE_USER: PoiSource
POI_SOURCE_MAP: PoiSource
POI_CATEGORY_UNSPECIFIED: PoiCategory
POI_CATEGORY_FUEL: PoiCategory
POI_CATEGORY_RESTAURANT: PoiCategory
POI_CATEGORY_SKI_AREA: PoiCategory
POI_CATEGORY_MUSEUM: PoiCategory
POI_CATEGORY_AIRPORT: PoiCategory
POI_CATEGORY_PARK: PoiCategory
POI_CATEGORY_TRAILHEAD: PoiCategory
POI_CATEGORY_CAR_PARTS: PoiCategory
POI_CATEGORY_JUNKYARD: PoiCategory

class PoiIcon(_message.Message):
    __slots__ = ("none", "colored_circle", "bundled")
    class NoIcon(_message.Message):
        __slots__ = ()
        def __init__(self) -> None: ...
    class ColoredCircle(_message.Message):
        __slots__ = ("argb",)
        ARGB_FIELD_NUMBER: _ClassVar[int]
        argb: int
        def __init__(self, argb: _Optional[int] = ...) -> None: ...
    class BundledIcon(_message.Message):
        __slots__ = ("file_name", "tint_argb")
        FILE_NAME_FIELD_NUMBER: _ClassVar[int]
        TINT_ARGB_FIELD_NUMBER: _ClassVar[int]
        file_name: str
        tint_argb: int
        def __init__(self, file_name: _Optional[str] = ..., tint_argb: _Optional[int] = ...) -> None: ...
    NONE_FIELD_NUMBER: _ClassVar[int]
    COLORED_CIRCLE_FIELD_NUMBER: _ClassVar[int]
    BUNDLED_FIELD_NUMBER: _ClassVar[int]
    none: PoiIcon.NoIcon
    colored_circle: PoiIcon.ColoredCircle
    bundled: PoiIcon.BundledIcon
    def __init__(self, none: _Optional[_Union[PoiIcon.NoIcon, _Mapping]] = ..., colored_circle: _Optional[_Union[PoiIcon.ColoredCircle, _Mapping]] = ..., bundled: _Optional[_Union[PoiIcon.BundledIcon, _Mapping]] = ...) -> None: ...

class Poi(_message.Message):
    __slots__ = ("id", "name", "location", "icon", "visible", "source", "created", "updated", "category", "details")
    class DetailsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    NAME_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    ICON_FIELD_NUMBER: _ClassVar[int]
    VISIBLE_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CREATED_FIELD_NUMBER: _ClassVar[int]
    UPDATED_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    DETAILS_FIELD_NUMBER: _ClassVar[int]
    id: int
    name: str
    location: _common_pb2.LatLng
    icon: PoiIcon
    visible: bool
    source: PoiSource
    created: _timestamp_pb2.Timestamp
    updated: _timestamp_pb2.Timestamp
    category: PoiCategory
    details: _containers.ScalarMap[str, str]
    def __init__(self, id: _Optional[int] = ..., name: _Optional[str] = ..., location: _Optional[_Union[_common_pb2.LatLng, _Mapping]] = ..., icon: _Optional[_Union[PoiIcon, _Mapping]] = ..., visible: bool = ..., source: _Optional[_Union[PoiSource, str]] = ..., created: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., updated: _Optional[_Union[_timestamp_pb2.Timestamp, _Mapping]] = ..., category: _Optional[_Union[PoiCategory, str]] = ..., details: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ListPoisRequest(_message.Message):
    __slots__ = ("visible_only",)
    VISIBLE_ONLY_FIELD_NUMBER: _ClassVar[int]
    visible_only: bool
    def __init__(self, visible_only: bool = ...) -> None: ...

class ListPoisResponse(_message.Message):
    __slots__ = ("pois",)
    POIS_FIELD_NUMBER: _ClassVar[int]
    pois: _containers.RepeatedCompositeFieldContainer[Poi]
    def __init__(self, pois: _Optional[_Iterable[_Union[Poi, _Mapping]]] = ...) -> None: ...

class GetPoiRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: int
    def __init__(self, id: _Optional[int] = ...) -> None: ...

class CreatePoiRequest(_message.Message):
    __slots__ = ("poi",)
    POI_FIELD_NUMBER: _ClassVar[int]
    poi: Poi
    def __init__(self, poi: _Optional[_Union[Poi, _Mapping]] = ...) -> None: ...

class UpdatePoiRequest(_message.Message):
    __slots__ = ("poi", "update_mask")
    POI_FIELD_NUMBER: _ClassVar[int]
    UPDATE_MASK_FIELD_NUMBER: _ClassVar[int]
    poi: Poi
    update_mask: _field_mask_pb2.FieldMask
    def __init__(self, poi: _Optional[_Union[Poi, _Mapping]] = ..., update_mask: _Optional[_Union[_field_mask_pb2.FieldMask, _Mapping]] = ...) -> None: ...

class DeletePoiRequest(_message.Message):
    __slots__ = ("id",)
    ID_FIELD_NUMBER: _ClassVar[int]
    id: int
    def __init__(self, id: _Optional[int] = ...) -> None: ...

class SearchPoisRequest(_message.Message):
    __slots__ = ("near", "bounding_box", "name_contains", "visible_only", "limit", "sources", "categories")
    class Near(_message.Message):
        __slots__ = ("center", "radius_meters")
        CENTER_FIELD_NUMBER: _ClassVar[int]
        RADIUS_METERS_FIELD_NUMBER: _ClassVar[int]
        center: _common_pb2.LatLng
        radius_meters: float
        def __init__(self, center: _Optional[_Union[_common_pb2.LatLng, _Mapping]] = ..., radius_meters: _Optional[float] = ...) -> None: ...
    NEAR_FIELD_NUMBER: _ClassVar[int]
    BOUNDING_BOX_FIELD_NUMBER: _ClassVar[int]
    NAME_CONTAINS_FIELD_NUMBER: _ClassVar[int]
    VISIBLE_ONLY_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    SOURCES_FIELD_NUMBER: _ClassVar[int]
    CATEGORIES_FIELD_NUMBER: _ClassVar[int]
    near: SearchPoisRequest.Near
    bounding_box: _common_pb2.BoundingBox
    name_contains: str
    visible_only: bool
    limit: int
    sources: _containers.RepeatedScalarFieldContainer[PoiSource]
    categories: _containers.RepeatedScalarFieldContainer[PoiCategory]
    def __init__(self, near: _Optional[_Union[SearchPoisRequest.Near, _Mapping]] = ..., bounding_box: _Optional[_Union[_common_pb2.BoundingBox, _Mapping]] = ..., name_contains: _Optional[str] = ..., visible_only: bool = ..., limit: _Optional[int] = ..., sources: _Optional[_Iterable[_Union[PoiSource, str]]] = ..., categories: _Optional[_Iterable[_Union[PoiCategory, str]]] = ...) -> None: ...

class SearchPoisResponse(_message.Message):
    __slots__ = ("results",)
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[PoiResult]
    def __init__(self, results: _Optional[_Iterable[_Union[PoiResult, _Mapping]]] = ...) -> None: ...

class PoiResult(_message.Message):
    __slots__ = ("poi", "distance_meters")
    POI_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_METERS_FIELD_NUMBER: _ClassVar[int]
    poi: Poi
    distance_meters: float
    def __init__(self, poi: _Optional[_Union[Poi, _Mapping]] = ..., distance_meters: _Optional[float] = ...) -> None: ...
