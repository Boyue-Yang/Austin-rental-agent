"""Shared helpers for loading and cleaning review text for analysis."""
import html
import re

import pandas as pd

from src.db.schema import connect

# Apartment-locator agencies were picked up by the property search; their reviews are not about a property.
NON_PROPERTY = re.compile(r"locator|locating", re.I)


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def load_google_reviews(zips: list[str] | None = None) -> pd.DataFrame:
    """English Google Maps reviews with text, excluding locator agencies."""
    sql = """SELECT review_id, canonical_place_id AS place_id, property_name, postal_code,
                    rating, posted_at, text
             FROM review_dataset
             WHERE source = 'google_maps' AND language LIKE 'en%' AND text IS NOT NULL"""
    params: list[str] = []
    if zips:
        sql += f" AND postal_code IN ({','.join('?' * len(zips))})"
        params = list(zips)
    conn = connect()
    df = pd.read_sql_query(sql + " ORDER BY review_id", conn, params=params)
    conn.close()
    df = df[~df["property_name"].fillna("").str.contains(NON_PROPERTY)].copy()
    df["text"] = df["text"].map(clean_text)
    df = df[df["text"].str.len() > 0]
    df["rating"] = df["rating"].astype(int)
    return df.reset_index(drop=True)


def rating_group(r: int) -> str:
    return "1" if r == 1 else "5" if r == 5 else "2-4"
