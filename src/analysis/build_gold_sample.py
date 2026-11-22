"""Draw the human-annotation (gold) sample of Google Maps reviews.

Design (see README / labeling guideline):
- Population: English Google reviews with text, all ZIP codes, locator agencies excluded.
- Stratified by star rating with deliberate oversampling of the rare 2-4 star reviews
  (they are ~9% of the population but carry most of the mixed / specific complaints).
  Each row keeps a sampling weight = stratum population / stratum sample size, so
  metrics can also be reported for the real rating distribution.
- At most --per-property reviews from one property, so big complexes don't dominate.
- Exact-duplicate texts ("Great place!") are sampled at most once.
- Fixed split, stratified within each rating (single annotator):
    dev  (100) G0001-G0100, for tuning the LLM-labeling prompt. G0001-G0050 are the
               checkpoint batch: stop after them, review, revise the guideline, fix them.
    test (400) G0101-G0500, never used for training or prompt tuning.
  Dev comes first so the annotator's early (least practiced) labels land in dev, which
  the checkpoint review corrects.

Usage:
    python -m src.analysis.build_gold_sample
    python -m src.analysis.build_gold_sample --seed 7 --out-name gold_v2

Output: data/processed/gold/<name>.csv with full metadata (rating, property, zip, split,
weight). This file is for training / evaluation, not for annotators;
build the annotation workbooks from it with `python -m src.labeling.make_sheets`.
"""
import argparse
import random

import pandas as pd

from src import config
from src.analysis.text import load_google_reviews

OUT_DIR = config.DB_PATH.parent / "gold"
ALLOCATION = {1: 150, 2: 50, 3: 50, 4: 50, 5: 200}
# Per rating stratum: checkpoint batch and the rest of dev, as fractions of the stratum sample.
CHECKPOINT_FRAC, DEV_FRAC = 0.10, 0.10


def draw(df: pd.DataFrame, allocation: dict[int, int], per_property: int, seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    used_props: dict[str, int] = {}
    seen_text: set[str] = set()
    picked = []
    # Draw the smallest strata first so the property cap doesn't starve them.
    for rating in sorted(allocation, key=allocation.get):
        pool = df[df["rating"] == rating].index.tolist()
        rng.shuffle(pool)
        n_pop, chosen = len(pool), []
        for i in pool:
            if len(chosen) == allocation[rating]:
                break
            row = df.loc[i]
            key = " ".join(row["text"].lower().split())
            if key in seen_text or used_props.get(row["place_id"], 0) >= per_property:
                continue
            seen_text.add(key)
            used_props[row["place_id"]] = used_props.get(row["place_id"], 0) + 1
            chosen.append(i)
        if len(chosen) < allocation[rating]:
            print(f"warning: only {len(chosen)} reviews available for {rating} stars")
        part = df.loc[chosen].copy()
        part["weight"] = round(n_pop / len(chosen), 3)
        # Split within the stratum (order is already random).
        n = len(part)
        n_ckpt, n_dev = round(n * CHECKPOINT_FRAC), round(n * DEV_FRAC)
        part["_batch"] = [0] * n_ckpt + [1] * n_dev + [2] * (n - n_ckpt - n_dev)
        picked.append(part)
    sample = pd.concat(picked)
    sample["split"] = sample["_batch"].map({0: "dev", 1: "dev", 2: "test"})
    # Annotation order: checkpoint batch, rest of dev, test; shuffled within each so ratings are not grouped.
    sample["_r"] = [rng.random() for _ in range(len(sample))]
    sample = sample.sort_values(["_batch", "_r"]).drop(columns=["_batch", "_r"]).reset_index(drop=True)
    sample.insert(0, "sample_id", [f"G{i + 1:04d}" for i in range(len(sample))])
    return sample


def main(per_property: int, seed: int, out_name: str) -> None:
    df = load_google_reviews()
    sample = draw(df, ALLOCATION, per_property, seed)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["sample_id", "review_id", "split", "rating", "weight",
            "place_id", "property_name", "postal_code", "posted_at", "text"]
    sample[cols].to_csv(OUT_DIR / f"{out_name}.csv", index=False, encoding="utf-8-sig")

    pop = df["rating"].value_counts().sort_index()
    print(f"population: {len(df)} reviews, {df['place_id'].nunique()} properties, by stars {pop.to_dict()}")
    print(pd.crosstab(sample["rating"], sample["split"], margins=True))
    print(f"properties: {sample['place_id'].nunique()}; ZIPs: {sample['postal_code'].nunique()}")
    print(f"median text length {sample['text'].str.len().median():.0f} chars "
          f"(population {df['text'].str.len().median():.0f})")
    print(f"-> {OUT_DIR / out_name}.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-property", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-name", default="gold_v1")
    args = ap.parse_args()
    main(args.per_property, args.seed, args.out_name)
