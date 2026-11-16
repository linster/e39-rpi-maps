"""Geocoding: an address (free text, or a contact card's fields) to
positions, offline, from the map database's map_street, map_address,
map_place and map_postcode (written by the toolkit's addresses.extract).

Plain Python on an sqlite3 connection, no Django (the backend's gRPC
GeocodeService and /geocode use it).

How a query is answered, best candidates first:
1. Streets whose normal name contains the query's street words (FTS5 on
   map_street.norm, the last word as a prefix), scored by how well the name
   matches (type and direction included), the locality (same name, or within
   reach of a place of that name), and the distance to `near` / the postal
   code.
2. With a house number, on each such street: the address itself
   (PRECISION_ADDRESS), else a position between the nearest lower and higher
   numbers on the same side (PRECISION_INTERPOLATED), else the street
   (PRECISION_STREET).
3. Without a street, or none found: the postal code (PRECISION_POSTCODE)
   and places of that name (PRECISION_PLACE).
"""

import math
import sqlite3
from dataclasses import dataclass, field

from . import addresses as norm

PRECISION_ADDRESS = "address"
PRECISION_INTERPOLATED = "interpolated"
PRECISION_STREET = "street"
PRECISION_PLACE = "place"
PRECISION_POSTCODE = "postcode"

MAX_STREETS = 5000           # FTS candidates scored (names, places, distances: cheap)
ADDRESS_LOOKUPS = 25         # of those, the best looked up for the house number
# How far from a place (by its kind) a street still counts as in it, km.
PLACE_REACH_KM = {"city": 25, "town": 10, "village": 5, "hamlet": 3, "suburb": 6, "quarter": 3, "neighbourhood": 2}
# Interpolating from one known neighbour only: at most this many numbers away.
ONE_SIDED_SPAN = 10


class GeocodingUnavailable(Exception):
    """The map has no geocoding data (built before it, or without it)."""


@dataclass
class Candidate:
    label: str
    latitude: float
    longitude: float
    precision: str
    score: float
    street: str = ""
    number: str = ""
    place: str = ""
    postcode: str = ""
    extra: dict = field(default_factory=dict)


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def available(conn):
    try:
        return conn.execute("SELECT 1 FROM map_street LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def format_postcode(code):
    return f"{code[:3]} {code[3:]}" if len(code) == 6 and code[0].isalpha() else code


def _places(conn, place_norm):
    return conn.execute("SELECT name, kind, latitude, longitude, population FROM map_place WHERE norm = ?",
                        (place_norm,)).fetchall()


def _postcode_point(conn, code):
    if not code:
        return None
    row = conn.execute("SELECT code, latitude, longitude FROM map_postcode WHERE code = ?", (code,)).fetchone()
    if row is None and len(code) == 6 and code[0].isalpha():
        row = conn.execute("SELECT code, latitude, longitude FROM map_postcode WHERE code = ?", (code[:3],)).fetchone()
    return row


def _fts_query(tokens, prefix=False, place=None):
    quoted = [f'"{t}"' for t in tokens]
    if prefix:
        quoted[-1] += "*"
    query = "norm : (" + " ".join(quoted) + ")"
    if place:
        query += " AND place_norm : (" + " ".join(f'"{t}"' for t in place.split()) + ")"
    return query


def _find_streets(conn, street, locality_norm=None):
    """(the query's tokens, map_street rows whose names have its words):
    those in the locality, then all; the last word as a prefix only if the
    whole words find nothing (a name typed partly)."""
    tokens = norm.street_tokens(street)
    if not tokens:
        return tokens, []
    core = norm.core_tokens(tokens)
    sql = ("SELECT s.id, s.name, s.norm, s.place, s.place_norm, s.latitude, s.longitude, s.addresses "
           "FROM map_street_fts f JOIN map_street s ON s.id = f.rowid WHERE map_street_fts MATCH ? LIMIT ?")
    rows = {}
    for prefix in (False, True):
        queries = ([_fts_query(core, prefix, locality_norm)] if locality_norm else []) + [_fts_query(core, prefix)]
        for q in queries:
            for row in conn.execute(sql, (q, MAX_STREETS)):
                rows.setdefault(row[0], row)
        if rows:
            break
    return tokens, list(rows.values())


def _name_score(query_tokens, row_norm):
    row_tokens = row_norm.split()
    if row_tokens == query_tokens:
        return 50
    q_core, r_core = norm.core_tokens(query_tokens), norm.core_tokens(row_tokens)
    score = 30 if q_core == r_core else 30 - 6 * max(0, len(r_core) - len(q_core)) - (8 if r_core[:len(q_core)] != q_core else 0)
    q_extra = [t for t in query_tokens if t not in q_core]
    r_extra = [t for t in row_tokens if t not in r_core]
    for t in q_extra:       # a type or direction the user gave: must agree
        score += 5 if t in r_extra else -12
    return score


def _locality_score(row, locality_norm, places):
    if not locality_norm:
        return 0
    if row[4] == locality_norm:
        return 40
    if row[4].startswith(locality_norm) or locality_norm.startswith(row[4]):
        return 30
    best = None
    for _name, kind, lat, lon, _pop in places:
        d = km(lat, lon, row[5], row[6])
        if d <= PLACE_REACH_KM.get(kind, 5):
            return 35
        best = d if best is None else min(best, d)
    return -20 - min(40, (best or 200) * 0.2)


def _address(conn, street_id, number):
    """(precision, lat, lon, number shown, postcode) of number on the
    street, or None."""
    key = norm.house_number_key(number)
    if key is None:
        return None
    n, text = key
    rows = conn.execute("SELECT number, lat_e6, lon_e6, postcode FROM map_address WHERE street_id = ? AND number_int = ?",
                        (street_id, n)).fetchall()
    if rows:
        row = next((r for r in rows if r[0] == text), rows[0])
        return PRECISION_ADDRESS, row[1] / 1e6, row[2] / 1e6, row[0] if row[0] == text else text, row[3]

    def neighbour(op, order, same_side=True):
        side = "AND number_int % 2 = ?" if same_side else ""
        params = (street_id, n, n % 2) if same_side else (street_id, n)
        return conn.execute(f"SELECT number_int, lat_e6, lon_e6, postcode FROM map_address "
                            f"WHERE street_id = ? AND number_int {op} ? {side} ORDER BY number_int {order} LIMIT 1",
                            params).fetchone()

    lower, higher = neighbour("<", "DESC"), neighbour(">", "ASC")
    if lower is None and higher is None:
        lower, higher = neighbour("<", "DESC", False), neighbour(">", "ASC", False)
    if lower and higher:
        f = (n - lower[0]) / (higher[0] - lower[0])
        lat = (lower[1] + (higher[1] - lower[1]) * f) / 1e6
        lon = (lower[2] + (higher[2] - lower[2]) * f) / 1e6
        return PRECISION_INTERPOLATED, lat, lon, text, lower[3] if lower[3] == higher[3] else None
    one = lower or higher
    if one and abs(one[0] - n) <= ONE_SIDED_SPAN:
        return PRECISION_INTERPOLATED, one[1] / 1e6, one[2] / 1e6, text, one[3]
    return None


def _split_locality(conn, street):
    """"Main St Smiths Falls" -> ("Main St", "smiths falls") when the last
    words name a place; else (street, None)."""
    words = street.split()
    for k in (3, 2, 1):
        if len(words) > k:
            place = norm.normalize_place(" ".join(words[-k:]))
            if place and _places(conn, place):
                return " ".join(words[:-k]), place
    return street, None


def geocode(conn, query=None, street=None, number=None, locality=None, region=None, postcode=None,
            near=None, limit=5):
    """Candidates (best first, at most limit) for an address: free text
    (query) and/or its parts. near: (lat, lon) to prefer nearby matches
    (e.g. the car's position). Raises GeocodingUnavailable without data."""
    if not available(conn):
        raise GeocodingUnavailable("this map has no geocoding data (built before it was added)")
    parsed = norm.parse(query) if query else {}
    street = street or parsed.get("street")
    number = number or parsed.get("number")
    locality_norm = norm.normalize_place(locality) if locality else parsed.get("locality")
    postcode = norm.normalize_postcode(postcode) if postcode else parsed.get("postcode")
    if street and not locality_norm and not (query and "," in query):
        street, locality_norm = _split_locality(conn, street)

    places = _places(conn, locality_norm) if locality_norm else []
    pc = _postcode_point(conn, postcode)
    anchor = near or (pc[1:] if pc else None) or (max(places, key=lambda p: p[4])[2:4] if places else None)
    candidates = []

    if street:
        tokens, rows = _find_streets(conn, street, locality_norm)
        scored = []
        for row in rows:
            score = 100 + _name_score(tokens, row[2]) + _locality_score(row, locality_norm, places)
            score += min(10, math.log10(row[7] + 1) * 4)
            if anchor:
                score -= min(60, km(anchor[0], anchor[1], row[5], row[6]) * 0.5)
            scored.append((score, row))
        scored.sort(key=lambda sr: -sr[0])
        for score, row in scored[:ADDRESS_LOOKUPS]:
            found = _address(conn, row[0], number) if number else None
            if found:
                precision, lat, lon, shown, pcode = found
                score += 25 if precision == PRECISION_ADDRESS else 15
                if postcode and pcode and pcode.startswith(postcode[:3]):
                    score += 10 if pcode == postcode else 4
                candidates.append(Candidate(f"{shown} {row[1]}, {row[3]}", lat, lon, precision, score,
                                            row[1], shown, row[3], format_postcode(pcode or "")))
            else:
                candidates.append(Candidate(f"{row[1]}, {row[3]}", row[5], row[6], PRECISION_STREET, score,
                                            row[1], "", row[3]))

    if pc:
        candidates.append(Candidate(format_postcode(pc[0]), pc[1], pc[2], PRECISION_POSTCODE,
                                    (90 if len(pc[0]) > 3 else 70) - (30 if street and candidates else 0),
                                    postcode=format_postcode(pc[0])))
    place_names = [locality_norm] if locality_norm and (not street or not candidates) else []
    if street and not number and (not query or "," not in query):
        place_names.append(norm.normalize_place(street))     # "Ottawa": maybe a place
    for place_norm in place_names:
        for name, kind, lat, lon, pop in _places(conn, place_norm):
            score = 140 + {"city": 8, "town": 6, "village": 4}.get(kind, 0) + min(6, math.log10(pop + 1))
            if anchor and not (pc and place_norm == locality_norm):
                score -= min(60, km(anchor[0], anchor[1], lat, lon) * 0.2)
            candidates.append(Candidate(name, lat, lon, PRECISION_PLACE, score, place=name, extra={"kind": kind}))

    candidates.sort(key=lambda c: -c.score)
    result = []
    for c in candidates:
        if any(r.label == c.label and r.precision == c.precision and
               km(r.latitude, r.longitude, c.latitude, c.longitude) < 1.0 for r in result):
            continue
        result.append(c)
        if len(result) >= limit:
            break
    return result
