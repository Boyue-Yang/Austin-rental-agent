import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RAW_DIR = ROOT / "data" / "raw" / "outscraper"
DB_PATH = ROOT / "data" / "processed" / "austin_housing.db"

OUTSCRAPER_API_KEY = os.getenv("OUTSCRAPER_API_KEY", "")
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")
APIFY_RAW_DIR = ROOT / "data" / "raw" / "apify"

# ApartmentRatings.com: raw __NEXT_DATA__ cache and persistent Chrome profile (keeps cookies between runs).
AR_RAW_DIR = ROOT / "data" / "raw" / "apartmentratings"
AR_PROFILE_DIR = ROOT / "data" / ".ar_chrome_profile"
AR_DELAY_SECONDS = (4, 9)  # random pause between page loads
# User-selected ZIP codes for the full ApartmentRatings collection.
AR_ZIP_CODES = (
    "78701", "78702", "78703", "78704", "78705", "78712",
    "78721", "78722", "78723", "78731", "78741", "78751", "78752",
    "78756", "78757", "78758", "78759",
)

# Search queries for seeding the property list. Outscraper's Google Maps search
# caps results per query, so many neighborhood-specific queries are used and
# results are deduplicated by place_id.
SEARCH_QUERIES = [
    "apartments near University of Texas at Austin, Austin, TX",
    "apartments West Campus, Austin, TX",
    "student housing West Campus, Austin, TX",
    "apartments North Campus, Austin, TX",
    "apartments Hyde Park, Austin, TX",
    "apartments Highland, Austin, TX",
    "apartments Crestview, Austin, TX",
    "apartments Mueller, Austin, TX",
    "apartments Hancock, Austin, TX",
    "apartments Windsor Park, Austin, TX",
    "apartments Brentwood, Austin, TX",
    "apartments Zilker, Austin, TX",
    "apartments East Austin, Austin, TX",
    "apartments Cherrywood, Austin, TX",
    "apartment complex 78701",
    "apartment complex 78702",
    "apartment complex 78703",
    "apartment complex 78704",
    "apartment complex 78705",
    "apartment complex 78712",
    "apartment complex 78721",
    "apartment complex 78722",
    "apartment complex 78723",
    "apartment complex 78731",
    "apartment complex 78741",
    "apartment complex 78751",
    "apartment complex 78752",
    "apartment complex 78756",
    "apartment complex 78757",
    "apartment complex 78758",
    "apartment complex 78759"
]
SEARCH_LIMIT_PER_QUERY = 500

# Post-filters applied at parse time (raw JSON is cached, so changing these is free).
STATE, CITY = "TX", "Austin"  # drop results outside this city (zip-only queries can leak other states)
MIN_REVIEWS = 10
ONLY_WITH_WEBSITE = True

# Apify review scraping: only reviews newer than this (UTC, requires 'newest' sort), capped per place.
REVIEWS_START_DATE = "2022-01-01"
REVIEWS_LIMIT_PER_PLACE = 500

# Keep only places whose Google category looks like a rental property.
KEEP_CATEGORY_KEYWORDS = ("apartment", "student housing", "housing complex", "residential", "condominium")
