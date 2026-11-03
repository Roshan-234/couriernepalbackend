"""Nepal administrative geography and courier delivery zones.

Source of truth for the 7 provinces and 77 districts used by rate cards,
branch coverage, address validation and district landing pages. Serving this
from the backend keeps the frontend dropdowns, the pricing engine and the
sitemap working from one list.

Delivery zones follow how Nepali courier networks actually price a parcel
rather than the administrative hierarchy:

    valley    Kathmandu Valley, same-day/next-day capable
    cities    Highway-served metros and sub-metros
    hills     District headquarters reached by road on a routine schedule
    remote    Mountain districts served by air or seasonal road only
"""

ZONE_VALLEY = "valley"
ZONE_CITIES = "cities"
ZONE_HILLS = "hills"
ZONE_REMOTE = "remote"

ZONES = (
    (ZONE_VALLEY, "Kathmandu Valley", "Same-day and next-day delivery"),
    (ZONE_CITIES, "Major cities", "Next-day delivery on highway routes"),
    (ZONE_HILLS, "District headquarters", "2-4 working days"),
    (ZONE_REMOTE, "Remote districts", "4-10 working days, air or seasonal road"),
)

# (name, headquarters, zone)
PROVINCES = {
    "Koshi": {
        "code": "P1",
        "capital": "Biratnagar",
        "districts": [
            ("Bhojpur", "Bhojpur", ZONE_HILLS),
            ("Dhankuta", "Dhankuta", ZONE_HILLS),
            ("Ilam", "Ilam", ZONE_HILLS),
            ("Jhapa", "Bhadrapur", ZONE_CITIES),
            ("Khotang", "Diktel", ZONE_HILLS),
            ("Morang", "Biratnagar", ZONE_CITIES),
            ("Okhaldhunga", "Okhaldhunga", ZONE_HILLS),
            ("Panchthar", "Phidim", ZONE_HILLS),
            ("Sankhuwasabha", "Khandbari", ZONE_HILLS),
            ("Solukhumbu", "Salleri", ZONE_REMOTE),
            ("Sunsari", "Inaruwa", ZONE_CITIES),
            ("Taplejung", "Taplejung", ZONE_REMOTE),
            ("Terhathum", "Myanglung", ZONE_HILLS),
            ("Udayapur", "Gaighat", ZONE_HILLS),
        ],
    },
    "Madhesh": {
        "code": "P2",
        "capital": "Janakpur",
        "districts": [
            ("Bara", "Kalaiya", ZONE_CITIES),
            ("Dhanusha", "Janakpur", ZONE_CITIES),
            ("Mahottari", "Jaleshwar", ZONE_HILLS),
            ("Parsa", "Birgunj", ZONE_CITIES),
            ("Rautahat", "Gaur", ZONE_HILLS),
            ("Saptari", "Rajbiraj", ZONE_HILLS),
            ("Sarlahi", "Malangwa", ZONE_HILLS),
            ("Siraha", "Siraha", ZONE_HILLS),
        ],
    },
    "Bagmati": {
        "code": "P3",
        "capital": "Hetauda",
        "districts": [
            ("Bhaktapur", "Bhaktapur", ZONE_VALLEY),
            ("Chitwan", "Bharatpur", ZONE_CITIES),
            ("Dhading", "Dhading Besi", ZONE_HILLS),
            ("Dolakha", "Charikot", ZONE_HILLS),
            ("Kathmandu", "Kathmandu", ZONE_VALLEY),
            ("Kavrepalanchok", "Dhulikhel", ZONE_HILLS),
            ("Lalitpur", "Lalitpur", ZONE_VALLEY),
            ("Makwanpur", "Hetauda", ZONE_CITIES),
            ("Nuwakot", "Bidur", ZONE_HILLS),
            ("Ramechhap", "Manthali", ZONE_HILLS),
            ("Rasuwa", "Dhunche", ZONE_REMOTE),
            ("Sindhuli", "Kamalamai", ZONE_HILLS),
            ("Sindhupalchok", "Chautara", ZONE_HILLS),
        ],
    },
    "Gandaki": {
        "code": "P4",
        "capital": "Pokhara",
        "districts": [
            ("Baglung", "Baglung", ZONE_HILLS),
            ("Gorkha", "Gorkha", ZONE_HILLS),
            ("Kaski", "Pokhara", ZONE_CITIES),
            ("Lamjung", "Besisahar", ZONE_HILLS),
            ("Manang", "Chame", ZONE_REMOTE),
            ("Mustang", "Jomsom", ZONE_REMOTE),
            ("Myagdi", "Beni", ZONE_HILLS),
            ("Nawalpur", "Kawasoti", ZONE_HILLS),
            ("Parbat", "Kusma", ZONE_HILLS),
            ("Syangja", "Putalibazar", ZONE_HILLS),
            ("Tanahun", "Damauli", ZONE_HILLS),
        ],
    },
    "Lumbini": {
        "code": "P5",
        "capital": "Deukhuri",
        "districts": [
            ("Arghakhanchi", "Sandhikharka", ZONE_HILLS),
            ("Banke", "Nepalgunj", ZONE_CITIES),
            ("Bardiya", "Gulariya", ZONE_HILLS),
            ("Dang", "Ghorahi", ZONE_CITIES),
            ("Eastern Rukum", "Rukumkot", ZONE_REMOTE),
            ("Gulmi", "Tamghas", ZONE_HILLS),
            ("Kapilvastu", "Taulihawa", ZONE_HILLS),
            ("Palpa", "Tansen", ZONE_HILLS),
            ("Parasi", "Ramgram", ZONE_HILLS),
            ("Pyuthan", "Pyuthan", ZONE_HILLS),
            ("Rolpa", "Liwang", ZONE_HILLS),
            ("Rupandehi", "Butwal", ZONE_CITIES),
        ],
    },
    "Karnali": {
        "code": "P6",
        "capital": "Birendranagar",
        "districts": [
            ("Dailekh", "Narayan", ZONE_HILLS),
            ("Dolpa", "Dunai", ZONE_REMOTE),
            ("Humla", "Simikot", ZONE_REMOTE),
            ("Jajarkot", "Khalanga", ZONE_HILLS),
            ("Jumla", "Chandannath", ZONE_REMOTE),
            ("Kalikot", "Manma", ZONE_REMOTE),
            ("Mugu", "Gamgadhi", ZONE_REMOTE),
            ("Salyan", "Salyan", ZONE_HILLS),
            ("Surkhet", "Birendranagar", ZONE_CITIES),
            ("Western Rukum", "Musikot", ZONE_HILLS),
        ],
    },
    "Sudurpashchim": {
        "code": "P7",
        "capital": "Godawari",
        "districts": [
            ("Achham", "Mangalsen", ZONE_HILLS),
            ("Baitadi", "Dasharathchand", ZONE_HILLS),
            ("Bajhang", "Jayaprithvi", ZONE_REMOTE),
            ("Bajura", "Martadi", ZONE_REMOTE),
            ("Dadeldhura", "Amargadhi", ZONE_HILLS),
            ("Darchula", "Khalanga", ZONE_REMOTE),
            ("Doti", "Dipayal Silgadhi", ZONE_HILLS),
            ("Kailali", "Dhangadhi", ZONE_CITIES),
            ("Kanchanpur", "Bhimdatta", ZONE_CITIES),
        ],
    },
}


def _slug(value):
    return value.lower().replace(" ", "-")


DISTRICTS = tuple(
    {
        "name": name,
        "slug": _slug(name),
        "headquarters": hq,
        "province": province,
        "province_code": meta["code"],
        "zone": zone,
    }
    for province, meta in PROVINCES.items()
    for name, hq, zone in meta["districts"]
)

DISTRICTS_BY_SLUG = {district["slug"]: district for district in DISTRICTS}
DISTRICT_NAMES = tuple(district["name"] for district in DISTRICTS)
ZONE_CODES = tuple(code for code, _label, _eta in ZONES)


def district(slug_or_name):
    """Return a district record for a slug or a name, or None."""
    if not isinstance(slug_or_name, str):
        return None
    return DISTRICTS_BY_SLUG.get(_slug(slug_or_name.strip()))


def zone_for(slug_or_name, default=ZONE_HILLS):
    """Delivery zone for a district, falling back to the standard zone."""
    found = district(slug_or_name)
    return found["zone"] if found else default
