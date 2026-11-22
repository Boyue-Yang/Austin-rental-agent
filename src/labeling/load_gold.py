"""Validate the filled annotation workbook and export gold labels.

Usage:
    python -m src.labeling.load_gold                 # data/processed/gold/gold_v1_annotation.xlsx
    python -m src.labeling.load_gold --partial       # allow unlabeled rows (e.g. after the checkpoint batch)

Checks every labeled row against the schema rules (valid values, generic -> no topics,
staff_shoutout -> only staff_management) and prints the problems with their sample_id.
Writes data/processed/gold/<gold>_labels.csv: sample_id, review_id, split, rating, weight,
review_type, one column per topic with none / pos / neg, notes.
"""
import argparse

import pandas as pd

from src import config
from src.labeling.schema import TOPICS, consistency_errors

GOLD_DIR = config.DB_PATH.parent / "gold"


def norm(v):
    if pd.isna(v):
        return None
    v = str(v).strip().lower()
    return v or None


def main(gold: str, partial: bool) -> None:
    sheet = pd.read_excel(GOLD_DIR / f"{gold}_annotation.xlsx", sheet_name="reviews", dtype=str)
    meta = pd.read_csv(GOLD_DIR / f"{gold}.csv", encoding="utf-8-sig", dtype={"review_id": str})
    for col in ["review_type", *TOPICS]:
        sheet[col] = sheet[col].map(norm)

    labeled = sheet[sheet["review_type"].notna()]
    unlabeled = sheet[sheet["review_type"].isna()]
    problems = []

    def topics_of(r) -> dict[str, str | None]:  # pandas stores empty cells as NaN
        return {t: v if isinstance(v := getattr(r, t), str) else None for t in TOPICS}

    for r in labeled.itertuples():
        errs = consistency_errors(r.review_type, topics_of(r))
        problems += [f"{r.sample_id}: {e}" for e in errs]
    # Rows with topic labels but no review_type are almost certainly an oversight.
    for r in unlabeled.itertuples():
        if any(topics_of(r).values()):
            problems.append(f"{r.sample_id}: topics filled but review_type empty")

    print(f"{len(labeled)} labeled, {len(unlabeled)} unlabeled")
    if problems:
        print(f"{len(problems)} problems:")
        print("\n".join(f"  {p}" for p in problems))
    if not partial and len(unlabeled):
        raise SystemExit("unlabeled rows remain; fix them or pass --partial")
    if problems:
        raise SystemExit("fix the problems above in the workbook, then rerun")

    out = labeled[["sample_id", "review_type", *TOPICS, "notes"]].copy()
    out[list(TOPICS)] = out[list(TOPICS)].fillna("none")
    out = meta[["sample_id", "review_id", "split", "rating", "weight"]].merge(out, on="sample_id")
    path = GOLD_DIR / f"{gold}_labels.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")

    print("\nreview_type:", out["review_type"].value_counts().to_dict())
    counts = pd.DataFrame({t: out[t].value_counts() for t in TOPICS}).T.reindex(columns=["pos", "neg"]).fillna(0).astype(int)
    print("\ntopic counts:\n" + counts.to_string())
    print(f"\n-> {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="gold_v1")
    ap.add_argument("--partial", action="store_true")
    args = ap.parse_args()
    main(args.gold, args.partial)
