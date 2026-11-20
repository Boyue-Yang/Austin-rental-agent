"""Seed the properties table from Outscraper's Google Maps search API.

Usage:
    python -m src.collectors.outscraper_places            # run all queries
    python -m src.collectors.outscraper_places --limit 1  # only first N queries (cheap test)
    python -m src.collectors.outscraper_places --query "apartment complex 78705"  # one ad-hoc query
    python -m src.collectors.outscraper_places --parse-only  # re-parse saved raw files, no API calls

Raw responses are saved to data/raw/outscraper/places/<query-slug>.json; queries
that already have a raw file are skipped, so reruns don't cost anything.
"""
import argparse
import csv
import json
import re

from outscraper import ApiClient

from src import config
from src.db.schema import connect

PLACES_DIR = config.RAW_DIR / "places"
DROPPED_CSV = config.DB_PATH.parent / "dropped_places.csv"
PROPERTIES_CSV = config.DB_PATH.parent / "properties.csv"  # human-readable copy of the properties table


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def fetch(queries: list[str]) -> None:
    PLACES_DIR.mkdir(parents=True, exist_ok=True)
    client = ApiClient(api_key=config.OUTSCRAPER_API_KEY)
    for q in queries:
        path = PLACES_DIR / f"{slug(q)}.json"
        if path.exists():
            print(f"skip (cached): {q}")
            continue
        print(f"fetching: {q}")
        # Returns a list (one entry per query) of lists of place dicts.
        results = client.google_maps_search(
            q, limit=config.SEARCH_LIMIT_PER_QUERY, language="en", region="US"
        )
        path.write_text(json.dumps({"query": q, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")


def is_rental(place: dict) -> bool:
    haystack = " ".join(
        str(place.get(k) or "") for k in ("type", "category", "subtypes")
    ).lower()
    return any(kw in haystack for kw in config.KEEP_CATEGORY_KEYWORDS)


def drop_reason(p: dict) -> str | None:
    """Return why a place is excluded, or None if it should be kept."""
    if not is_rental(p):
        return "category"
    if (p.get("state_code"), p.get("city")) != (config.STATE, config.CITY):
        return "wrong_city"
    if (p.get("reviews") or 0) < config.MIN_REVIEWS:
        return "few_reviews"
    if config.ONLY_WITH_WEBSITE and not p.get("website"):
        return "no_website"
    return None


def parse() -> None:
    """Rebuild the properties table from all raw files (filters apply retroactively)."""
    conn = connect()
    conn.execute("DELETE FROM properties")
    done: set[str] = set()
    counts: dict[str, int] = {}
    dropped_rows = []
    for path in sorted(PLACES_DIR.glob("*.json")):
        blob = json.loads(path.read_text(encoding="utf-8"))
        for group in blob["results"]:
            for p in group:
                pid = p.get("place_id")
                if not pid or pid in done:
                    continue
                done.add(pid)
                reason = drop_reason(p)
                if reason:
                    counts[reason] = counts.get(reason, 0) + 1
                    dropped_rows.append(
                        [reason, pid, p.get("name"), p.get("type"), p.get("city"), p.get("state_code"),
                         p.get("postal_code"), p.get("reviews"), p.get("rating"), p.get("website")]
                    )
                    continue
                conn.execute(
                    """INSERT INTO properties
                       (place_id, name, address, postal_code, lat, lng, category, subtypes,
                        rating, review_count, reviews_per_score, website, phone, status, source_query)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pid,
                        p.get("name"),
                        p.get("full_address") or p.get("address"),
                        p.get("postal_code"),
                        p.get("latitude"),
                        p.get("longitude"),
                        p.get("type") or p.get("category"),
                        p.get("subtypes"),
                        p.get("rating"),
                        p.get("reviews"),
                        json.dumps(p.get("reviews_per_score")),
                        p.get("website"),
                        p.get("phone"),
                        p.get("business_status"),
                        blob["query"],
                    ),
                )
    conn.commit()
    DROPPED_CSV.parent.mkdir(parents=True, exist_ok=True)
    with DROPPED_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["reason", "place_id", "name", "type", "city", "state", "zip", "reviews", "rating", "website"])
        w.writerows(sorted(dropped_rows))
    cur = conn.execute("SELECT * FROM properties ORDER BY postal_code, review_count DESC")
    with PROPERTIES_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([d[0] for d in cur.description])
        w.writerows(cur)
    total = conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0]
    print(f"{len(done)} unique places: kept {total}, dropped {counts}; dropped list -> {DROPPED_CSV}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only run the first N queries")
    ap.add_argument("--parse-only", action="store_true")
    ap.add_argument("--query", action="append", help="run only this query (repeatable), e.g. 'apartment complex 78705'")
    args = ap.parse_args()
    if not args.parse_only:
        if not config.OUTSCRAPER_API_KEY:
            raise SystemExit("Set OUTSCRAPER_API_KEY in .env")
        fetch(args.query or config.SEARCH_QUERIES[: args.limit])
    parse()
