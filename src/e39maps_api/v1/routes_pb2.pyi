from e39maps_api.v1 import common_pb2 as _common_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Costing(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    COSTING_UNSPECIFIED: _ClassVar[Costing]
    COSTING_AUTO: _ClassVar[Costing]

class Units(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    UNITS_UNSPECIFIED: _ClassVar[Units]
    UNITS_KILOMETERS: _ClassVar[Units]
    UNITS_MILES: _ClassVar[Units]

class ManeuverType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MANEUVER_TYPE_UNSPECIFIED: _ClassVar[ManeuverType]
    MANEUVER_TYPE_NONE: _ClassVar[ManeuverType]
    MANEUVER_TYPE_START: _ClassVar[ManeuverType]
    MANEUVER_TYPE_START_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_START_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_DESTINATION: _ClassVar[ManeuverType]
    MANEUVER_TYPE_DESTINATION_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_DESTINATION_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_BECOMES: _ClassVar[ManeuverType]
    MANEUVER_TYPE_CONTINUE: _ClassVar[ManeuverType]
    MANEUVER_TYPE_SLIGHT_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_SHARP_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_UTURN_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_UTURN_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_SHARP_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_SLIGHT_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_RAMP_STRAIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_RAMP_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_RAMP_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_EXIT_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_EXIT_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_STAY_STRAIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_STAY_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_STAY_LEFT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_MERGE: _ClassVar[ManeuverType]
    MANEUVER_TYPE_ROUNDABOUT_ENTER: _ClassVar[ManeuverType]
    MANEUVER_TYPE_ROUNDABOUT_EXIT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_FERRY_ENTER: _ClassVar[ManeuverType]
    MANEUVER_TYPE_FERRY_EXIT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_MERGE_RIGHT: _ClassVar[ManeuverType]
    MANEUVER_TYPE_MERGE_LEFT: _ClassVar[ManeuverType]
COSTING_UNSPECIFIED: Costing
COSTING_AUTO: Costing
UNITS_UNSPECIFIED: Units
UNITS_KILOMETERS: Units
UNITS_MILES: Units
MANEUVER_TYPE_UNSPECIFIED: ManeuverType
MANEUVER_TYPE_NONE: ManeuverType
MANEUVER_TYPE_START: ManeuverType
MANEUVER_TYPE_START_RIGHT: ManeuverType
MANEUVER_TYPE_START_LEFT: ManeuverType
MANEUVER_TYPE_DESTINATION: ManeuverType
MANEUVER_TYPE_DESTINATION_RIGHT: ManeuverType
MANEUVER_TYPE_DESTINATION_LEFT: ManeuverType
MANEUVER_TYPE_BECOMES: ManeuverType
MANEUVER_TYPE_CONTINUE: ManeuverType
MANEUVER_TYPE_SLIGHT_RIGHT: ManeuverType
MANEUVER_TYPE_RIGHT: ManeuverType
MANEUVER_TYPE_SHARP_RIGHT: ManeuverType
MANEUVER_TYPE_UTURN_RIGHT: ManeuverType
MANEUVER_TYPE_UTURN_LEFT: ManeuverType
MANEUVER_TYPE_SHARP_LEFT: ManeuverType
MANEUVER_TYPE_LEFT: ManeuverType
MANEUVER_TYPE_SLIGHT_LEFT: ManeuverType
MANEUVER_TYPE_RAMP_STRAIGHT: ManeuverType
MANEUVER_TYPE_RAMP_RIGHT: ManeuverType
MANEUVER_TYPE_RAMP_LEFT: ManeuverType
MANEUVER_TYPE_EXIT_RIGHT: ManeuverType
MANEUVER_TYPE_EXIT_LEFT: ManeuverType
MANEUVER_TYPE_STAY_STRAIGHT: ManeuverType
MANEUVER_TYPE_STAY_RIGHT: ManeuverType
MANEUVER_TYPE_STAY_LEFT: ManeuverType
MANEUVER_TYPE_MERGE: ManeuverType
MANEUVER_TYPE_ROUNDABOUT_ENTER: ManeuverType
MANEUVER_TYPE_ROUNDABOUT_EXIT: ManeuverType
MANEUVER_TYPE_FERRY_ENTER: ManeuverType
MANEUVER_TYPE_FERRY_EXIT: ManeuverType
MANEUVER_TYPE_MERGE_RIGHT: ManeuverType
MANEUVER_TYPE_MERGE_LEFT: ManeuverType

class ComputeRouteRequest(_message.Message):
    __slots__ = ("origin", "destination", "via", "costing", "units", "language")
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    DESTINATION_FIELD_NUMBER: _ClassVar[int]
    VIA_FIELD_NUMBER: _ClassVar[int]
    COSTING_FIELD_NUMBER: _ClassVar[int]
    UNITS_FIELD_NUMBER: _ClassVar[int]
    LANGUAGE_FIELD_NUMBER: _ClassVar[int]
    origin: _common_pb2.LatLng
    destination: _common_pb2.LatLng
    via: _containers.RepeatedCompositeFieldContainer[_common_pb2.LatLng]
    costing: Costing
    units: Units
    language: str
    def __init__(self, origin: _Optional[_Union[_common_pb2.LatLng, _Mapping]] = ..., destination: _Optional[_Union[_common_pb2.LatLng, _Mapping]] = ..., via: _Optional[_Iterable[_Union[_common_pb2.LatLng, _Mapping]]] = ..., costing: _Optional[_Union[Costing, str]] = ..., units: _Optional[_Union[Units, str]] = ..., language: _Optional[str] = ...) -> None: ...

class Route(_message.Message):
    __slots__ = ("shape", "length_meters", "duration_seconds", "maneuvers")
    SHAPE_FIELD_NUMBER: _ClassVar[int]
    LENGTH_METERS_FIELD_NUMBER: _ClassVar[int]
    DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MANEUVERS_FIELD_NUMBER: _ClassVar[int]
    shape: _containers.RepeatedCompositeFieldContainer[_common_pb2.LatLng]
    length_meters: float
    duration_seconds: float
    maneuvers: _containers.RepeatedCompositeFieldContainer[Maneuver]
    def __init__(self, shape: _Optional[_Iterable[_Union[_common_pb2.LatLng, _Mapping]]] = ..., length_meters: _Optional[float] = ..., duration_seconds: _Optional[float] = ..., maneuvers: _Optional[_Iterable[_Union[Maneuver, _Mapping]]] = ...) -> None: ...

class Maneuver(_message.Message):
    __slots__ = ("type", "instruction", "verbal_pre_transition", "street_names", "length_meters", "duration_seconds", "begin_shape_index", "end_shape_index", "roundabout_exit_count", "location", "trigger_radius_meters")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    INSTRUCTION_FIELD_NUMBER: _ClassVar[int]
    VERBAL_PRE_TRANSITION_FIELD_NUMBER: _ClassVar[int]
    STREET_NAMES_FIELD_NUMBER: _ClassVar[int]
    LENGTH_METERS_FIELD_NUMBER: _ClassVar[int]
    DURATION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    BEGIN_SHAPE_INDEX_FIELD_NUMBER: _ClassVar[int]
    END_SHAPE_INDEX_FIELD_NUMBER: _ClassVar[int]
    ROUNDABOUT_EXIT_COUNT_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    TRIGGER_RADIUS_METERS_FIELD_NUMBER: _ClassVar[int]
    type: ManeuverType
    instruction: str
    verbal_pre_transition: str
    street_names: _containers.RepeatedScalarFieldContainer[str]
    length_meters: float
    duration_seconds: float
    begin_shape_index: int
    end_shape_index: int
    roundabout_exit_count: int
    location: _common_pb2.LatLng
    trigger_radius_meters: float
    def __init__(self, type: _Optional[_Union[ManeuverType, str]] = ..., instruction: _Optional[str] = ..., verbal_pre_transition: _Optional[str] = ..., street_names: _Optional[_Iterable[str]] = ..., length_meters: _Optional[float] = ..., duration_seconds: _Optional[float] = ..., begin_shape_index: _Optional[int] = ..., end_shape_index: _Optional[int] = ..., roundabout_exit_count: _Optional[int] = ..., location: _Optional[_Union[_common_pb2.LatLng, _Mapping]] = ..., trigger_radius_meters: _Optional[float] = ...) -> None: ...
