"""Draw a rating-stratified random sample of English Google reviews (exploration / topic modeling).

Sample sizes per star rating are proportional to the population counts across the
chosen ZIP codes (largest-remainder rounding), so the sample keeps the real 1-5 star
mix. Fixed seed, so reruns produce the same rows. For the annotation (gold) sample
with oversampled 2-4 star reviews, use build_gold_sample instead.

Usage:
    python -m src.analysis.sample_reviews --zip 78705 --n 500
    python -m src.analysis.sample_reviews --zip 78701 --zip 78705 --zip 78722 --n 1000
"""
import argparse

import pandas as pd

from src import config
from src.analysis.text import load_google_reviews

OUT_DIR = config.DB_PATH.parent / "samples"


def stratified_counts(dist: dict[int, int], n: int) -> dict[int, int]:
    total = sum(dist.values())
    exact = {k: n * v / total for k, v in dist.items()}
    counts = {k: int(x) for k, x in exact.items()}
    for k in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[: n - sum(counts.values())]:
        counts[k] += 1
    return counts


def main(zips: list[str], n: int, seed: int) -> None:
    df = load_google_reviews(zips)
    if df.empty:
        raise SystemExit(f"No reviews for ZIPs {zips}")
    pop = df["rating"].value_counts().sort_index()
    counts = stratified_counts(pop.to_dict(), min(n, len(df)))
    sample = pd.concat(df[df["rating"] == r].sample(c, random_state=seed) for r, c in counts.items())
    sample = sample.sample(frac=1, random_state=seed).reset_index(drop=True)  # don't group by rating
    sample["weight"] = sample["rating"].map(lambda r: round(pop[r] / counts[r], 3))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"reviews_{'_'.join(sorted(zips))}_sample{len(sample)}_seed{seed}.csv"
    sample.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"population {len(df)} reviews, by stars {pop.to_dict()}")
    print(f"sample by stars {dict(sorted(counts.items()))}; by ZIP {sample['postal_code'].value_counts().to_dict()}")
    print(f"-> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="append", required=True, help="postal code (repeatable)")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    main(args.zip, args.n, args.seed)
