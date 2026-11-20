"""Regression checks for shared review storage and safe legacy migration."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src import config
from src.db import schema
from src.db.reviews import export_reviews, save_review
from src.collectors import apartmentratings as ar

LEGACY_REVIEWS = """
CREATE TABLE reviews (
    review_id TEXT PRIMARY KEY, place_id TEXT NOT NULL REFERENCES properties(place_id),
    rating INTEGER, text TEXT, posted_at TEXT, owner_reply TEXT,
    likes INTEGER, language TEXT, source TEXT
);
CREATE INDEX idx_reviews_place ON reviews(place_id);
CREATE TABLE ar_reviews (
    review_id TEXT PRIMARY KEY, complex_id TEXT NOT NULL REFERENCES ar_properties(complex_id),
    rating REAL, aspect_ratings TEXT, title TEXT, text TEXT, owner_reply TEXT,
    reviewer_type TEXT, begin_year INTEGER, end_year INTEGER, incentivized INTEGER,
    posted_at TEXT, updated_at TEXT
);
"""


class ReviewsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review_tests_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "test.db"
        for module in (schema, config):
            context = patch.object(module, "DB_PATH", self.path)
            context.start()
            self.addCleanup(context.stop)

    def legacy(self, invalid_id=False):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript(schema.BASE_SCHEMA + LEGACY_REVIEWS)
            conn.execute("INSERT INTO properties(place_id,name,postal_code) VALUES ('g1','Google property','78705')")
            conn.execute("INSERT INTO ar_properties(complex_id,name,postal_code,url) VALUES ('a1','AR property','78705','https://example.com/a1')")
            if invalid_id:
                conn.execute("INSERT INTO reviews(review_id,place_id,text,source) VALUES (NULL,'g1','Invalid ID','apify')")
            else:
                for rid, collector in (("same-id", "apify"), ("other", "outscraper"), ("unknown", None)):
                    conn.execute("INSERT INTO reviews(review_id,place_id,rating,text,source) VALUES (?,'g1',5,'Google original',?)", (rid, collector))
                conn.execute("INSERT INTO ar_reviews(review_id,complex_id,rating,text,incentivized) VALUES ('same-id','a1',2.5,?,1)", ("中文\u0085line\u2028line\u2029end",))
            conn.commit()

    def test_migration_preserves_sources_and_ids(self):
        self.legacy()
        with closing(schema.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 4)
            self.assertEqual(dict(conn.execute("SELECT review_id,collector FROM reviews WHERE source='google_maps'")), {"same-id":"apify", "other":"outscraper", "unknown":None})
            self.assertEqual(conn.execute("SELECT type FROM sqlite_master WHERE name='ar_reviews'").fetchone()[0], "view")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ar_reviews").fetchone()[0], 1)
        with closing(schema.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 4)
        self.assertEqual(len(list((self.root / "backups").glob("*.db"))), 1)

    def test_upsert_does_not_overwrite_other_website(self):
        self.legacy()
        with closing(schema.connect()) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            save_review(conn, {"source":"google_maps", "review_id":"same-id", "place_id":"g1", "collector":"outscraper", "text":"Updated Google"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT text FROM reviews WHERE source='apartmentratings'").fetchone()[0], "中文\u0085line\u2028line\u2029end")
            self.assertEqual(conn.execute("SELECT collector FROM reviews WHERE source='google_maps' AND review_id='same-id'").fetchone()[0], "outscraper")

    def test_unmatched_property_and_later_matching(self):
        self.legacy()
        with closing(schema.connect()) as conn:
            self.assertEqual(conn.execute("SELECT property_name,place_id,canonical_place_id FROM review_dataset WHERE source='apartmentratings'").fetchone(), ("AR property",None,None))
            conn.execute("UPDATE ar_properties SET place_id='g1' WHERE complex_id='a1'")
            self.assertEqual(conn.execute("SELECT canonical_place_id FROM review_dataset WHERE source='apartmentratings'").fetchone()[0], "g1")

    def test_jsonl_preserves_unicode_and_unknown_values(self):
        self.legacy()
        with closing(schema.connect()) as conn:
            output = self.root / "reviews.jsonl"
            self.assertEqual(export_reviews(conn, output), 4)
        records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 4)
        google = next(record for record in records if record["source"] == "google_maps")
        apartment = next(record for record in records if record["source"] == "apartmentratings")
        self.assertIsNone(google["incentivized"])
        self.assertIsNone(google["aspect_ratings"])
        self.assertIs(apartment["incentivized"], True)
        self.assertEqual(apartment["text"], "中文\u0085line\u2028line\u2029end")

    def test_migration_failure_rolls_back_all_changes(self):
        self.legacy(invalid_id=True)
        with self.assertRaises(sqlite3.IntegrityError):
            schema.connect()
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertNotIn("collector", {row[1] for row in conn.execute("PRAGMA table_info(reviews)")})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 1)
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='reviews_legacy_google'").fetchone())
        backup_path = next((self.root / "backups").glob("*.db"))
        with closing(sqlite3.connect(backup_path)) as backup:
            self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_invalid_apartment_review_aborts_migration(self):
        self.legacy()
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("INSERT INTO ar_reviews(review_id,complex_id,text) VALUES (NULL,'a1','Invalid AR ID')")
            conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            schema.connect()
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute("SELECT type FROM sqlite_master WHERE name='ar_reviews'").fetchone()[0], "table")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ar_reviews").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT source FROM reviews WHERE review_id='same-id'").fetchone()[0], "apify")

    def test_fresh_database(self):
        with closing(schema.connect()) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0], 0)
            with self.assertRaises(ValueError):
                save_review(conn, {"source":"google_maps", "review_id":"id", "unexpected":"value"})

    def test_property_refresh_preserves_verified_mapping(self):
        with closing(schema.connect()) as conn:
            conn.execute("INSERT INTO properties(place_id,name) VALUES ('g1','Google property')")
            conn.execute("INSERT INTO ar_properties(complex_id,name,place_id) VALUES ('123456','Old name','g1')")
            conn.commit()
        raw = self.root / "raw"
        raw.mkdir()
        def query(name, data):
            return {"queryKey":[name], "state":{"data":data}}
        data = {"props":{"pageProps":{"dehydratedState":{"queries":[
            query("getComplexInfo", {"complexName":"New name", "address1":"1 Main St", "zip":"78705"}),
            query("getComplexURL", {"url":"/tx/austin/test_123456/"}),
            query("searchReviewsV4", {"totalHits":0,"reviews":[]}),
        ]}}}}
        (raw / "123456_p1.json").write_text(json.dumps(data), encoding="utf-8")
        with patch.object(ar, "COMPLEX_DIR", raw), patch.object(ar, "ZIP_DIR", self.root / "zips"):
            ar.parse()
        with closing(schema.connect()) as conn:
            self.assertEqual(conn.execute("SELECT name,place_id FROM ar_properties WHERE complex_id='123456'").fetchone(), ("New name","g1"))


if __name__ == "__main__":
    unittest.main()
