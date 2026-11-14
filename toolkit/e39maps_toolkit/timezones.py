"""The time zone of a map: the IANA zone at the centre of its circle
("America/Toronto" for Ottawa + 530 km), stored in the map's facts. The
device follows it in automatic mode (e39-rpi-yocto: e39rpi-timezone's auto.d
hook from meta-e39rpi-maps).

One zone per map: a circle can span several (Edmonton + 1000 km: Pacific,
Mountain, Saskatchewan's CST); following the car's position across them is
for later (the map carrying the zone boundaries, GPS).

Looked up offline with timezonefinder (OpenStreetMap-based boundaries, from
timezone-boundary-builder).
"""


def zone_at(lat, lon):
    """The IANA time zone name at (lat, lon), or None if unknown."""
    from timezonefinder import TimezoneFinder

    return TimezoneFinder().timezone_at(lat=lat, lng=lon)
