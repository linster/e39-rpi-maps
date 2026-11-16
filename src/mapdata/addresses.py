"""Addresses: normalizing names and parsing what the user (or a contact
card) gives, shared by the map toolkit (which stores normalized names) and
the backend (which looks them up; mapdata.geocode).

Plain Python, no Django.

Normal form: lower case, accents folded, punctuation as spaces, and the
usual abbreviations reduced to one spelling, so "St. Laurent Blvd",
"Boul. Saint-Laurent" and "saint laurent boulevard" compare equal:
- street types (Street/St, Avenue/Ave/Av, Boulevard/Blvd/Boul, Chemin/Ch,
  Route/Rte, Crescent/Cres, ...) to a short form;
- directions (North/N/Nord, ...) to n, s, e, w, ne, ...;
- "St"/"Ste" before a name to saint/sainte; at the end, St is Street.
"""

import re
import unicodedata

# Street types -> one spelling. English and French (Ontario, Quebec).
STREET_TYPES = {}
for canonical, spellings in {
    "st": ("street", "st", "str"),
    "ave": ("avenue", "ave", "av"),
    "rd": ("road", "rd"),
    "dr": ("drive", "dr", "drv"),
    "blvd": ("boulevard", "blvd", "boul", "bd", "bvd"),
    "cres": ("crescent", "cres", "cr", "crois", "croissant"),
    "crt": ("court", "crt", "ct", "cour"),
    "pl": ("place", "pl"),
    "ln": ("lane", "ln"),
    "way": ("way",),
    "terr": ("terrace", "terr", "tce", "terrasse"),
    "pkwy": ("parkway", "pkwy", "pky"),
    "hwy": ("highway", "hwy"),
    "cir": ("circle", "cir", "circ"),
    "trl": ("trail", "trl"),
    "pvt": ("private", "pvt"),
    "sq": ("square", "sq"),
    "gate": ("gate", "gt"),
    "line": ("line",),
    "conc": ("concession", "conc"),
    "sdrd": ("sideroad", "sdrd", "side road"),
    "grove": ("grove", "grv"),
    "hts": ("heights", "hts"),
    "pt": ("point", "pt"),
    "rue": ("rue",),
    "ch": ("chemin", "ch"),
    "rte": ("route", "rte"),
    "mtee": ("montee", "mtee"),
    "rang": ("rang", "rg"),
    "prom": ("promenade", "prom"),
    "all": ("allee",),
    "imp": ("impasse", "imp"),
    "car": ("carre",),
    "cote": ("cote",),
    "ruelle": ("ruelle",),
}.items():
    for s in spellings:
        STREET_TYPES[s] = canonical

DIRECTIONS = {
    "north": "n", "n": "n", "nord": "n",
    "south": "s", "s": "s", "sud": "s",
    "east": "e", "e": "e", "est": "e",
    "west": "w", "w": "w", "ouest": "w", "o": "w",
    "northeast": "ne", "ne": "ne", "nordest": "ne",
    "northwest": "nw", "nw": "nw", "nordouest": "nw", "no": "nw",
    "southeast": "se", "se": "se", "sudest": "se",
    "southwest": "sw", "sw": "sw", "sudouest": "sw", "so": "sw",
}

# Words that say nothing about which street ("de la", "of").
STOP_WORDS = {"de", "du", "des", "la", "le", "les", "l", "d", "of", "the"}

# Provinces, states, countries in free text: not localities.
REGIONS = {
    "on", "ont", "ontario", "qc", "que", "quebec", "pq", "ab", "alberta", "bc", "sk", "mb", "manitoba",
    "nb", "ns", "pe", "nl", "ny", "new york", "vt", "vermont", "nh", "me", "mi", "michigan", "ma", "pa", "oh",
    "canada", "ca", "usa", "us", "united states", "etats unis",
}

CA_POSTCODE = re.compile(r"\b([abceghj-nprstvxy]\d[abceghj-nprstv-z])\s?(\d[abceghj-nprstv-z]\d)?\b", re.I)
US_ZIP = re.compile(r"\b(\d{5})(?:-\d{4})?\b")
HOUSE_NUMBER = re.compile(r"^(\d+)\s*([a-z]|1/2|½)?$", re.I)


def fold(text):
    """Lower case, accents removed, anything but letters and digits as one space."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = text.replace("'", " ").replace("’", " ")
    return " ".join(re.sub(r"[^0-9a-z]+", " ", text).split())


def street_tokens(name):
    """The normal form of a street name, as a list of tokens."""
    words = fold(name).split()
    out = []
    for i, w in enumerate(words):
        if w in ("st", "ste") and i + 1 < len(words) and words[i + 1] not in STREET_TYPES and \
                words[i + 1] not in DIRECTIONS:
            out.append("saint" if w == "st" else "sainte")
        elif w in STREET_TYPES:
            out.append(STREET_TYPES[w])
        elif w in DIRECTIONS and (i > 0 or len(words) > 1):
            out.append(DIRECTIONS[w])
        elif w in STOP_WORDS and len(words) > 1:
            continue
        else:
            out.append(w)
    return out


def normalize_street(name):
    return " ".join(street_tokens(name))


def core_tokens(tokens):
    """The tokens that name the street, without its type and direction
    (the parts a user may leave out or get wrong)."""
    core = [t for t in tokens if t not in set(STREET_TYPES.values()) and t not in set(DIRECTIONS.values())]
    return core or list(tokens)


# Official prefixes of municipality names ("Township of Rideau Lakes").
PLACE_PREFIXES = re.compile(
    r"^(?:(?:the\s+)?(?:city|town|township|village|municipality|corporation of the \w+|united townships|"
    r"united counties)\s+of|ville|municipalite|canton|cantons unis|paroisse|village|cite)\s+(?:de\s+|d\s+|du\s+|des\s+)?",
    re.I)


def clean_place(name):
    """A locality's name for showing, without official prefixes:
    "Township of Rideau Lakes" -> "Rideau Lakes", "Ville de Gatineau" ->
    "Gatineau"."""
    name = (name or "").strip()
    folded = fold(name)
    m = PLACE_PREFIXES.match(folded)
    if not m or m.end() >= len(folded):
        return name
    # Drop as many words of the original as the prefix had.
    return " ".join(re.split(r"[\s']+", name)[len(folded[:m.end()].split()):]) or name


def normalize_place(name):
    """A locality's normal form ("Saint-Jérôme" -> "saint jerome",
    "Village of Merrickville-Wolford" -> "merrickville wolford")."""
    words = fold(clean_place(name)).split()
    return " ".join("saint" if w == "st" else "sainte" if w == "ste" else w for w in words)


def normalize_postcode(code):
    """Canadian postal codes as "K1P1J1" (or the area, "K1P"), US ZIP codes
    as 5 digits, else the folded text without spaces; None if empty."""
    text = (code or "").strip().upper().replace(" ", "").replace("-", "")
    if not text:
        return None
    m = re.fullmatch(r"([A-Z]\d[A-Z])(\d[A-Z]\d)?", text)
    if m:
        return m.group(0)
    m = re.fullmatch(r"(\d{5})(\d{4})?", text)
    if m:
        return m.group(1)
    return text


def house_number_key(number):
    """(leading integer, normal text) of a house number: "12A" -> (12, "12A");
    None if it doesn't start with digits, or with more than 9 (not a house
    number: OSM has phone numbers and IDs in addr:housenumber)."""
    text = (number or "").strip().upper().replace(" ", "")
    m = re.match(r"(\d{1,9})(?!\d)", text)
    return (int(m.group(1)), text) if m else None


def parse(query):
    """Free text ("123 Bank St, Ottawa, ON K1P 1J1") -> dict with street,
    number, locality, postcode (missing ones absent). Commas separate the
    street from the locality; without them, a known locality isn't
    guessed (the street search tries the whole text)."""
    text = (query or "").strip()
    result = {}
    m = CA_POSTCODE.search(text)
    if m and (m.group(2) or re.fullmatch(r"\s*[a-z]\d[a-z]\s*", text, re.I)):
        result["postcode"] = normalize_postcode(m.group(0))
        text = (text[:m.start()] + text[m.end():]).strip()
    else:
        m = US_ZIP.search(text)
        if m and m.start() > 0 and not re.match(r"^\s*\d+\s*$", text[:m.start()]):
            result["postcode"] = m.group(1)
            text = (text[:m.start()] + text[m.end():]).strip()
    parts = [p.strip() for p in text.split(",") if p.strip()]
    # "123, rue Principale": the number alone before the first comma.
    if len(parts) > 1 and HOUSE_NUMBER.match(parts[0]):
        parts = [f"{parts[0]} {parts[1]}"] + parts[2:]
    # A province or state is dropped, except as the first part after the
    # street: "Québec" there is the city.
    parts = [p for i, p in enumerate(parts) if fold(p) not in REGIONS or (i == 1 and len(fold(p)) > 3)]
    if not parts:
        return result
    first = parts[0]
    m = re.match(r"^\s*(\d+\s*(?:[a-z](?=\s)|1/2|½)?)[\s,]+(.+)$", first, re.I)
    if m:
        result["number"] = m.group(1).replace(" ", "")
        first = m.group(2)
    else:
        m = re.match(r"^(.+?)\s+(\d+[a-z]?)$", first, re.I)   # "Bank St 123"
        if m and fold(m.group(1)).split()[-1] in STREET_TYPES:
            first, result["number"] = m.group(1), m.group(2)
    if first:
        result["street"] = first
    for p in parts[1:]:
        words = fold(p).split()
        while len(words) > 1 and words[-1] in REGIONS:
            words.pop()
        if words:
            result["locality"] = normalize_place(" ".join(words))
            break
    return result
