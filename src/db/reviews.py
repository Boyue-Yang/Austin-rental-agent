"""Shared review writes and the combined analysis export.

Run python -m src.db.reviews to export the current database without fetching.
"""
import json
from pathlib import Path

from src import config
from src.db.schema import connect

REVIEW_FIELDS = (
    "review_id", "source", "collector", "place_id", "complex_id", "rating", "text",
    "posted_at", "updated_at", "owner_reply", "likes", "language", "title",
    "aspect_ratings", "reviewer_type", "begin_year", "end_year", "incentivized", "source_url",
)


def save_review(conn, review: dict) -> None:
    """Upsert by website and original ID, keeping cross-site IDs independent."""
    unknown = set(review) - set(REVIEW_FIELDS)
    if unknown:
        raise ValueError(f"Unrecognized review fields: {sorted(unknown)}")
    if not review.get("review_id") or not review.get("source"):
        raise ValueError("A review must have a source and its original review ID.")
    columns = [field for field in REVIEW_FIELDS if field in review]
    updates = [f"{field}=excluded.{field}" for field in columns if field not in ("source", "review_id")]
    conflict = "DO UPDATE SET " + ",".join(updates) if updates else "DO NOTHING"
    conn.execute(
        f"INSERT INTO reviews ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
        f"ON CONFLICT(source,review_id) {conflict}",
        [review[field] for field in columns],
    )


def export_reviews(conn, output: Path | None = None) -> int:
    """Stream both websites into one JSONL, keeping unavailable fields null."""
    output = output or config.DB_PATH.parent / "reviews.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    cursor = conn.execute("SELECT * FROM review_dataset ORDER BY source, review_id")
    fields = [column[0] for column in cursor.description]
    count = 0
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in cursor:
            review = dict(zip(fields, row))
            if review["aspect_ratings"] is not None:
                review["aspect_ratings"] = json.loads(review["aspect_ratings"])
            if review["incentivized"] is not None:
                review["incentivized"] = bool(review["incentivized"])
            line = json.dumps(review, ensure_ascii=False)
            # Keep Unicode line separators inside a single physical record.
            for separator in ("\u0085", "\u2028", "\u2029"):
                line = line.replace(separator, f"\\u{ord(separator):04x}")
            stream.write(line + "\n")
            count += 1
    temporary.replace(output)
    print(f"exported {count} reviews to {output}", flush=True)
    return count


if __name__ == "__main__":
    conn = connect()
    try:
        export_reviews(conn)
    finally:
        conn.close()
