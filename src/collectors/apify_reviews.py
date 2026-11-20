"""Fetch Google Maps reviews for properties via the Apify Actor
`compass/google-maps-reviews-scraper` (cheaper per review than Outscraper).

Usage:
    python -m src.collectors.apify_reviews --max-places 3 --reviews-limit 50 --max-cost 0.5   # cheap test
    python -m src.collectors.apify_reviews --zip 78703 --max-cost 2                            # one zip code
    python -m src.collectors.apify_reviews                                                    # all properties
    python -m src.collectors.apify_reviews --parse-only

Raw items go to data/raw/apify/reviews/<place_id>.json (one file per place, a JSON list;
an empty list means "fetched, no reviews"). Places with an existing file are skipped,
so reruns and crashes don't re-bill. Reviewer identity is never requested
(personalData=False) and is dropped at parse time. The Actor cannot skip reviews
without text, so those are fetched (and billed) but filtered out at parse time.
"""
import argparse
import json
import os
from decimal import Decimal

from apify_client import ApifyClient

from src import config
from src.db.schema import connect
from src.db.reviews import export_reviews, save_review

ACTOR = "compass/google-maps-reviews-scraper"
REVIEWS_DIR = config.APIFY_RAW_DIR / "reviews"
BATCH = 20  # places per Actor run


def fetch(max_places: int | None, reviews_limit: int, max_cost: float | None, zips: list[str] | None,
          token: str = config.APIFY_API_TOKEN) -> None:
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    sql = "SELECT place_id FROM properties"
    params: list[str] = []
    if zips:
        sql += f" WHERE postal_code IN ({','.join('?' * len(zips))})"
        params = zips
    rows = conn.execute(sql + " ORDER BY review_count DESC", params).fetchall()
    todo = [r[0] for r in rows if not (REVIEWS_DIR / f"{r[0]}.json").exists()]
    if max_places:
        todo = todo[:max_places]
    print(f"{len(todo)} places to fetch")

    client = ApifyClient(token)
    spent = 0.0
    for i in range(0, len(todo), BATCH):
        batch = todo[i : i + BATCH]
        budget = None
        if max_cost is not None:
            remaining = max_cost - spent
            if remaining <= 0:
                print(f"stopping: --max-cost {max_cost} reached")
                break
            budget = Decimal(str(round(remaining, 4)))
        print(f"fetching {i + 1}-{i + len(batch)} / {len(todo)}")
        run = client.actor(ACTOR).call(
            run_input={
                "placeIds": batch,
                "maxReviews": reviews_limit,
                "reviewsSort": "newest",  # required by reviewsStartDate
                "reviewsStartDate": config.REVIEWS_START_DATE,
                "language": "en",
                "reviewsOrigin": "google",
                "personalData": False,
            },
            max_total_charge_usd=budget,
        )
        if run is None:
            print("run returned nothing, skipping batch")
            continue
        spent += float(run.usage_total_usd or 0)
        items = list(client.dataset(run.default_dataset_id).iterate_items())
        by_place: dict[str, list] = {pid: [] for pid in batch}
        for it in items:
            pid = it.get("placeId")
            if pid in by_place:
                by_place[pid].append(it)
        unmatched = len(items) - sum(len(v) for v in by_place.values())
        if unmatched:
            print(f"  warning: {unmatched} items had no matching placeId (not saved)")
        if run.status != "SUCCEEDED":
            # e.g. budget exhausted mid-run: any place may be truncated, so save nothing;
            # the whole batch is refetched on the next run.
            print(f"  run status {run.status}; batch not saved, rerun to retry")
            continue
        for pid, its in by_place.items():
            (REVIEWS_DIR / f"{pid}.json").write_text(json.dumps(its, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {len(items)} items, run cost ${float(run.usage_total_usd or 0):.4f}, total ${spent:.4f}")


def parse() -> None:
    conn = connect()
    stored = empty = 0
    for path in sorted(REVIEWS_DIR.glob("*.json")):
        pid = path.stem
        for r in json.loads(path.read_text(encoding="utf-8")):
            rid = r.get("reviewId")
            text = (r.get("text") or "").strip()
            if not rid:
                continue
            if not text:
                empty += 1
                continue
            save_review(conn, {
                "review_id": rid, "place_id": pid, "source": "google_maps", "collector": "apify",
                "rating": r.get("stars"), "text": text, "posted_at": r.get("publishedAtDate"),
                "owner_reply": r.get("responseFromOwnerText"), "likes": r.get("likesCount"),
                "language": r.get("originalLanguage"), "source_url": r.get("reviewUrl"),
            })
            stored += 1
    conn.commit()
    export_reviews(conn)
    conn.close()
    print(f"stored {stored} reviews with text, skipped {empty} without text")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-places", type=int)
    ap.add_argument("--reviews-limit", type=int, default=config.REVIEWS_LIMIT_PER_PLACE)
    ap.add_argument("--max-cost", type=float, help="USD cap across this invocation (Apify max_total_charge_usd)")
    ap.add_argument("--zip", action="append", help="only properties in this postal code (repeatable)")
    ap.add_argument("--token-env", default="APIFY_API_TOKEN",
                    help="name of the .env variable holding the Apify token (e.g. APIFY_API_TOKEN_2 for a second account)")
    ap.add_argument("--parse-only", action="store_true")
    args = ap.parse_args()
    if not args.parse_only:
        token = os.getenv(args.token_env, "")
        if not token:
            raise SystemExit(f"Set {args.token_env} in .env")
        fetch(args.max_places, args.reviews_limit, args.max_cost, args.zip, token)
    parse()
