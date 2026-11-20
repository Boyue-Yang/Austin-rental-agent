"""Fetch Google Maps reviews for properties via Outscraper.

Usage:
    python -m src.collectors.outscraper_reviews --max-places 3   # cheap test
    python -m src.collectors.outscraper_reviews                  # all properties
    python -m src.collectors.outscraper_reviews --parse-only

Only reviews with text are fetched (ignore_empty=True); the full 1-5 star histogram incl.
text-less reviews is stored in properties.reviews_per_score.
One raw file per place: data/raw/outscraper/reviews/<place_id>.json. Places with
an existing raw file are skipped. Reviewer identity is dropped at parse time.
"""
import argparse
import json

from outscraper import ApiClient

from src import config
from src.db.schema import connect
from src.db.reviews import export_reviews, save_review

REVIEWS_DIR = config.RAW_DIR / "reviews"
BATCH = 10


def fetch(max_places: int | None, reviews_limit: int) -> None:
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    rows = conn.execute(
        "SELECT place_id FROM properties ORDER BY review_count DESC"
    ).fetchall()
    todo = [r[0] for r in rows if not (REVIEWS_DIR / f"{r[0]}.json").exists()]
    if max_places:
        todo = todo[:max_places]
    print(f"{len(todo)} places to fetch")

    client = ApiClient(api_key=config.OUTSCRAPER_API_KEY)
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        print(f"fetching {i + 1}-{i + len(batch)} / {len(todo)}")
        results = client.google_maps_reviews(
            batch, reviews_limit=reviews_limit, sort="newest", ignore_empty=True, language="en"
        )
        # Results come back in the same order as the input queries.
        for pid, result in zip(batch, results):
            (REVIEWS_DIR / f"{pid}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
            )


def parse() -> None:
    conn = connect()
    n = 0
    for path in sorted(REVIEWS_DIR.glob("*.json")):
        place = json.loads(path.read_text(encoding="utf-8"))
        # Outscraper returns either the place dict or a one-element list of it.
        if isinstance(place, list):
            place = place[0] if place else {}
        pid = place.get("place_id") or path.stem
        for r in place.get("reviews_data") or []:
            rid = r.get("review_id")
            if not rid:
                continue
            text = (r.get("review_text") or "").strip()
            if not text:
                continue
            save_review(conn, {
                "review_id": rid, "place_id": pid, "source": "google_maps", "collector": "outscraper",
                "rating": r.get("review_rating"), "text": text, "posted_at": r.get("review_datetime_utc"),
                "owner_reply": r.get("owner_answer"), "likes": r.get("review_likes"),
                "language": r.get("review_language"),
            })
            n += 1
    conn.commit()
    export_reviews(conn)
    conn.close()
    print(f"stored {n} reviews")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-places", type=int)
    ap.add_argument("--reviews-limit", type=int, default=500)
    ap.add_argument("--parse-only", action="store_true")
    args = ap.parse_args()
    if not args.parse_only:
        if not config.OUTSCRAPER_API_KEY:
            raise SystemExit("Set OUTSCRAPER_API_KEY in .env")
        fetch(args.max_places, args.reviews_limit)
    parse()
