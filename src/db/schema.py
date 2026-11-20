"""SQLite schema and lossless migration to a shared, multi-source reviews table."""
import sqlite3
from datetime import datetime, timezone

from src.config import DB_PATH

BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    place_id     TEXT PRIMARY KEY,
    name         TEXT,
    address      TEXT,
    postal_code  TEXT,
    lat          REAL,
    lng          REAL,
    category     TEXT,
    subtypes     TEXT,
    rating       REAL,
    review_count INTEGER,
    reviews_per_score TEXT,  -- JSON {"1":n,...,"5":n}: full rating histogram, incl. text-less reviews
    website      TEXT,
    phone        TEXT,
    status       TEXT,
    source_query TEXT
);

CREATE TABLE IF NOT EXISTS ar_properties (
    complex_id   TEXT PRIMARY KEY,
    url          TEXT,
    name         TEXT,
    address      TEXT,
    postal_code  TEXT,       -- ZIP from the property address, falling back to discovery ZIP
    lat          REAL,
    lng          REAL,
    management   TEXT,
    review_total INTEGER,    -- totalHits of searchReviewsV4
    avg_ratings  TEXT,       -- JSON from getAverageRatings
    fees         TEXT,       -- JSON from getComplexFees
    info         TEXT,       -- raw getComplexInfo JSON (field names not fully mapped yet)
    place_id     TEXT        -- matched Google properties.place_id (filled later)
);

CREATE TABLE IF NOT EXISTS ar_floor_plans (
    complex_id   TEXT NOT NULL REFERENCES ar_properties(complex_id),
    name         TEXT,
    bedrooms     REAL,
    bathrooms    REAL,
    sqft_min     INTEGER,
    sqft_max     INTEGER,
    rent_min     REAL,
    rent_max     REAL,
    deposit      REAL,
    units_available INTEGER,
    fetched_at   TEXT,       -- mtime of the cached raw page (prices are snapshots)
    PRIMARY KEY (complex_id, name, fetched_at)
);
"""

REVIEW_TABLE = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id      TEXT NOT NULL,
    source         TEXT NOT NULL CHECK (length(source) > 0),
    collector      TEXT,
    place_id       TEXT REFERENCES properties(place_id),
    complex_id     TEXT REFERENCES ar_properties(complex_id),
    rating         REAL,
    text           TEXT,
    posted_at      TEXT,
    updated_at     TEXT,
    owner_reply    TEXT,
    likes          INTEGER,
    language       TEXT,
    title          TEXT,
    aspect_ratings TEXT,
    reviewer_type  TEXT,
    begin_year     INTEGER,
    end_year       INTEGER,
    incentivized   INTEGER,
    source_url     TEXT,
    PRIMARY KEY (source, review_id),
    CHECK (source <> 'google_maps' OR place_id IS NOT NULL),
    CHECK (source <> 'apartmentratings' OR complex_id IS NOT NULL)
)
"""

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_reviews_place ON reviews(place_id)",
    "CREATE INDEX IF NOT EXISTS idx_reviews_complex ON reviews(complex_id)",
)

# Both sources remain visible even when an ApartmentRatings property has not yet
# been matched to a Google place. Never infer that match from the property name.
DATASET_VIEW = """
CREATE VIEW IF NOT EXISTS review_dataset AS
SELECT r.*,
       COALESCE(a.name, p.name) AS property_name,
       COALESCE(a.address, p.address) AS property_address,
       COALESCE(a.postal_code, p.postal_code) AS postal_code,
       COALESCE(r.place_id, a.place_id) AS canonical_place_id
FROM reviews r
LEFT JOIN ar_properties a ON a.complex_id = r.complex_id
LEFT JOIN properties p ON p.place_id = COALESCE(r.place_id, a.place_id)
"""

# Read compatibility only: ApartmentRatings comments are stored in reviews.
AR_REVIEW_VIEW = """
CREATE VIEW IF NOT EXISTS ar_reviews AS
SELECT review_id, complex_id, rating, aspect_ratings, title, text, owner_reply,
       reviewer_type, begin_year, end_year, incentivized, posted_at, updated_at
FROM reviews WHERE source = 'apartmentratings'
"""

SCHEMA = BASE_SCHEMA + REVIEW_TABLE + ";\n" + ";\n".join((*INDEXES, DATASET_VIEW, AR_REVIEW_VIEW)) + ";\n"


def _object_type(conn, name):
    row = conn.execute("SELECT type FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def _needs_migration(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reviews)")}
    return bool(columns and "collector" not in columns) or _object_type(conn, "ar_reviews") == "table"


def _backup(conn):
    directory = DB_PATH.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{DB_PATH.stem}_before_unified_reviews_{timestamp}.db"
    backup = sqlite3.connect(path)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    print(f"database backup: {path}", flush=True)


def _migrate_reviews(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reviews)")}
    if columns and "collector" not in columns:
        expected = {"review_id", "place_id", "rating", "text", "posted_at",
                    "owner_reply", "likes", "language", "source"}
        if columns != expected:
            raise RuntimeError("Unrecognized legacy reviews schema; migration was not applied.")
        sources = {row[0] for row in conn.execute("SELECT DISTINCT source FROM reviews")}
        if not sources <= {None, "apify", "outscraper", "google_maps"}:
            raise RuntimeError("Unrecognized legacy review sources; migration was not applied.")
        conn.execute("ALTER TABLE reviews RENAME TO reviews_legacy_google")
        conn.execute(REVIEW_TABLE)
        conn.execute("""
            INSERT INTO reviews (review_id, source, collector, place_id, rating,
                                 text, posted_at, owner_reply, likes, language)
            SELECT review_id, 'google_maps',
                   CASE WHEN source = 'google_maps' THEN NULL ELSE source END,
                   place_id, rating, text, posted_at, owner_reply, likes, language
            FROM reviews_legacy_google
        """)
        conn.execute("DROP TABLE reviews_legacy_google")
    else:
        conn.execute(REVIEW_TABLE)
    if _object_type(conn, "ar_reviews") == "table":
        conn.execute("""
            INSERT INTO reviews
                (review_id, source, collector, complex_id, place_id, rating,
                 aspect_ratings, title, text, owner_reply, reviewer_type,
                 begin_year, end_year, incentivized, posted_at, updated_at, source_url)
            SELECT r.review_id, 'apartmentratings', 'playwright', r.complex_id,
                   p.place_id, r.rating, r.aspect_ratings, r.title, r.text,
                   r.owner_reply, r.reviewer_type, r.begin_year, r.end_year,
                   r.incentivized, r.posted_at, r.updated_at, p.url
            FROM ar_reviews r LEFT JOIN ar_properties p USING(complex_id)
            WHERE 1
            ON CONFLICT(source, review_id) DO NOTHING
        """)
        conn.execute("DROP TABLE ar_reviews")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        if _needs_migration(conn):
            _backup(conn)
        conn.executescript("BEGIN IMMEDIATE;\n" + BASE_SCHEMA)
        _migrate_reviews(conn)
        for statement in INDEXES:
            conn.execute(statement)
        conn.execute("DROP VIEW IF EXISTS review_dataset")
        conn.execute("DROP VIEW IF EXISTS ar_reviews")
        conn.execute(DATASET_VIEW)
        conn.execute(AR_REVIEW_VIEW)
        conn.commit()
        return conn
    except BaseException:
        conn.rollback()
        conn.close()
        raise
