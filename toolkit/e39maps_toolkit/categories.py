"""Which OpenStreetMap objects become which map POI category.

Keys are mapdata.schema.CATEGORIES. An object matching several rules gets
the first category in this order.
"""

RULES = {
    "fuel": [("amenity", "fuel")],
    "restaurant": [("amenity", "restaurant"), ("amenity", "fast_food")],
    "ski_area": [("landuse", "winter_sports")],
    "museum": [("tourism", "museum")],
    "airport": [("aeroway", "aerodrome")],
    "park": [("leisure", "park"), ("leisure", "nature_reserve"), ("boundary", "national_park")],
    "trailhead": [("highway", "trailhead")],
    "car_parts": [("shop", "car_parts")],
    "junkyard": [("industrial", "scrap_yard"), ("shop", "scrap_yard"), ("amenity", "scrapyard")],
}

# Categories whose POIs are useful even without a name: they're shown with
# their brand/operator, or else the category's label.
UNNAMED_OK = {"fuel", "car_parts"}

# OSM tags kept in map_poi.details (besides "kind", the matching tag).
DETAIL_KEYS = ("brand", "operator", "cuisine", "phone", "website", "opening_hours",
               "iata", "icao", "ele", "addr:housenumber", "addr:street", "addr:city",
               "addr:postcode")


def tag_pairs(categories):
    return [pair for c in categories for pair in RULES[c]]


def classify(tags, categories):
    """(category, "key=value") of the first rule the tags match, or None."""
    for category in categories:
        for key, value in RULES[category]:
            if tags.get(key) == value:
                return category, f"{key}={value}"
    return None


def poi_name(tags, category, labels):
    name = tags.get("name") or tags.get("name:en") or tags.get("name:fr")
    if name:
        return name
    if category in UNNAMED_OK:
        return tags.get("brand") or tags.get("operator") or labels[category]
    return None
