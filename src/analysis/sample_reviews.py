"""Draw a rating-stratified random sample of reviews for manual reading / gold labeling.

The sample keeps the zip code's 1-5 star distribution (largest-remainder rounding) and
uses a fixed seed, so reruns produce the same rows.

Usage:
    python -m src.analysis.sample_reviews --zip 78705 --n 500
"""
import argparse
import csv
import random

from src import config
from src.db.schema import connect

OUT_DIR = config.DB_PATH.parent / "samples"


def stratified_counts(dist: dict[int, int], n: int) -> dict[int, int]:
    total = sum(dist.values())
    exact = {k: n * v / total for k, v in dist.items()}
    counts = {k: int(x) for k, x in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[: n - sum(counts.values())]:
        counts[k] += 1
    return counts


def main(zip_code: str, n: int, seed: int, source: str = "google_maps") -> None:
    conn = connect()
    sql = """SELECT review_id, place_id, property_name, rating, posted_at, text,
                    owner_reply IS NOT NULL AS has_owner_reply, likes, source, complex_id
             FROM review_dataset WHERE postal_code = ?"""
    params = [zip_code]
    if source != "all":
        sql += " AND source = ?"
        params.append(source)
    rows = conn.execute(sql + " ORDER BY source, review_id", params).fetchall()
    conn.close()
    if not rows:
        raise SystemExit(f"No reviews for ZIP {zip_code} and source {source}")
    n = min(n, len(rows))
    by_rating: dict[int, list] = {}
    for r in rows:
        by_rating.setdefault(r[3], []).append(r)
    counts = stratified_counts({k: len(v) for k, v in by_rating.items()}, n)

    rng = random.Random(seed)
    sample = []
    for rating in sorted(counts):
        sample += rng.sample(by_rating[rating], counts[rating])
    rng.shuffle(sample)  # don't present grouped by rating (avoids anchoring when labeling)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "" if source == "google_maps" else f"_{source}"
    out = OUT_DIR / f"reviews_{zip_code}{suffix}_sample{n}_seed{seed}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "place_id", "property", "rating", "posted_at", "text", "has_owner_reply", "likes", "source", "complex_id"])
        w.writerows(sample)
    print(f"{len(rows)} reviews in {zip_code}; population {dict(sorted((k, len(v)) for k, v in by_rating.items()))}")
    print(f"sample counts {dict(sorted(counts.items()))} -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default="78705")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--source", choices=("google_maps", "apartmentratings", "all"), default="google_maps")
    args = ap.parse_args()
    main(args.zip, args.n, args.seed, args.source)
