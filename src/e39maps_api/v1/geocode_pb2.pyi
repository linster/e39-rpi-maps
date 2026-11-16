from e39maps_api.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class GeocodePrecision(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    GEOCODE_PRECISION_UNSPECIFIED: _ClassVar[GeocodePrecision]
    GEOCODE_PRECISION_ADDRESS: _ClassVar[GeocodePrecision]
    GEOCODE_PRECISION_INTERPOLATED: _ClassVar[GeocodePrecision]
    GEOCODE_PRECISION_STREET: _ClassVar[GeocodePrecision]
    GEOCODE_PRECISION_PLACE: _ClassVar[GeocodePrecision]
    GEOCODE_PRECISION_POSTCODE: _ClassVar[GeocodePrecision]
GEOCODE_PRECISION_UNSPECIFIED: GeocodePrecision
GEOCODE_PRECISION_ADDRESS: GeocodePrecision
GEOCODE_PRECISION_INTERPOLATED: GeocodePrecision
GEOCODE_PRECISION_STREET: GeocodePrecision
GEOCODE_PRECISION_PLACE: GeocodePrecision
GEOCODE_PRECISION_POSTCODE: GeocodePrecision

class StructuredAddress(_message.Message):
    __slots__ = ("street", "house_number", "locality", "region", "postal_code", "country")
    STREET_FIELD_NUMBER: _ClassVar[int]
    HOUSE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    LOCALITY_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    POSTAL_CODE_FIELD_NUMBER: _ClassVar[int]
    COUNTRY_FIELD_NUMBER: _ClassVar[int]
    street: str
    house_number: str
    locality: str
    region: str
    postal_code: str
    country: str
    def __init__(self, street: _Optional[str] = ..., house_number: _Optional[str] = ..., locality: _Optional[str] = ..., region: _Optional[str] = ..., postal_code: _Optional[str] = ..., country: _Optional[str] = ...) -> None: ...

class GeocodeRequest(_message.Message):
    __slots__ = ("query", "address", "near", "limit")
    QUERY_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    NEAR_FIELD_NUMBER: _ClassVar[int]
    LIMIT_FIELD_NUMBER: _ClassVar[int]
    query: str
    address: StructuredAddress
    near: _common_pb2.LatLng
    limit: int
    def __init__(self, query: _Optional[str] = ..., address: _Optional[_Union[StructuredAddress, _Mapping]] = ..., near: _Optional[_Union[_common_pb2.LatLng, _Mapping]] = ..., limit: _Optional[int] = ...) -> None: ...

class GeocodeCandidate(_message.Message):
    __slots__ = ("label", "location", "precision", "score", "street", "house_number", "locality", "postal_code")
    LABEL_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    PRECISION_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    STREET_FIELD_NUMBER: _ClassVar[int]
    HOUSE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    LOCALITY_FIELD_NUMBER: _ClassVar[int]
    POSTAL_CODE_FIELD_NUMBER: _ClassVar[int]
    label: str
    location: _common_pb2.LatLng
    precision: GeocodePrecision
    score: float
    street: str
    house_number: str
    locality: str
    postal_code: str
    def __init__(self, label: _Optional[str] = ..., location: _Optional[_Union[_common_pb2.LatLng, _Mapping]] = ..., precision: _Optional[_Union[GeocodePrecision, str]] = ..., score: _Optional[float] = ..., street: _Optional[str] = ..., house_number: _Optional[str] = ..., locality: _Optional[str] = ..., postal_code: _Optional[str] = ...) -> None: ...

class GeocodeResponse(_message.Message):
    __slots__ = ("candidates",)
    CANDIDATES_FIELD_NUMBER: _ClassVar[int]
    candidates: _containers.RepeatedCompositeFieldContainer[GeocodeCandidate]
    def __init__(self, candidates: _Optional[_Iterable[_Union[GeocodeCandidate, _Mapping]]] = ...) -> None: ...
