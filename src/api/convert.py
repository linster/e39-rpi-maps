"""Conversion between the gRPC messages (e39maps_api.v1) and Django models."""

from google.protobuf.timestamp_pb2 import Timestamp

from e39maps_api.v1 import common_pb2, pois_pb2
from pois.models import Poi


def _timestamp(dt):
    ts = Timestamp()
    if dt is not None:
        ts.FromDatetime(dt)
    return ts


def poi_to_pb(poi: Poi) -> pois_pb2.Poi:
    icon = pois_pb2.PoiIcon()
    if poi.icon_kind == Poi.IconKind.COLORED_CIRCLE and poi.icon_argb is not None:
        icon.colored_circle.argb = poi.icon_argb
    elif poi.icon_kind == Poi.IconKind.BUNDLED:
        icon.bundled.file_name = poi.icon_file
    else:
        icon.none.SetInParent()
    return pois_pb2.Poi(
        id=poi.id,
        name=poi.name,
        location=common_pb2.LatLng(latitude=poi.latitude, longitude=poi.longitude),
        icon=icon,
        visible=poi.visible,
        source=pois_pb2.POI_SOURCE_USER,
        created=_timestamp(poi.created),
        updated=_timestamp(poi.updated),
    )


# Fields of Poi a client may set (CreatePoi, UpdatePoi's update_mask).
EDITABLE = ("name", "location", "icon", "visible")


def apply_pb_to_poi(msg: pois_pb2.Poi, poi: Poi, fields=EDITABLE) -> None:
    """Copy the given editable fields from a message onto a model instance."""
    if "name" in fields:
        poi.name = msg.name.strip()
    if "location" in fields:
        poi.latitude = msg.location.latitude
        poi.longitude = msg.location.longitude
    if "icon" in fields:
        kind = msg.icon.WhichOneof("kind")
        poi.icon_argb, poi.icon_file = None, ""
        if kind == "colored_circle":
            poi.icon_kind, poi.icon_argb = Poi.IconKind.COLORED_CIRCLE, msg.icon.colored_circle.argb
        elif kind == "bundled":
            poi.icon_kind, poi.icon_file = Poi.IconKind.BUNDLED, msg.icon.bundled.file_name
        else:
            poi.icon_kind = Poi.IconKind.NONE
    if "visible" in fields:
        poi.visible = msg.visible
