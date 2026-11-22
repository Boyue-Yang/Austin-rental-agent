"""Exploratory BERTopic over review *sentences*, to inform the label schema.

Steps: load English Google reviews -> split into sentences -> replace person names
(almost always staff) with [STAFF] using a NER model -> embed -> BERTopic ->
per-topic share of 1-star / 2-4-star / 5-star reviews that mention the topic.

Usage:
    python -m src.analysis.bertopic_explore --zip 78705
    python -m src.analysis.bertopic_explore --zip 78705 --min-topic-size 30
    python -m src.analysis.bertopic_explore --zip 78705 --refresh     # redo sentence split / NER / embeddings

Outputs (data/processed/topics/<zips>/):
    sentences.csv   one row per sentence (masked text, review_id, rating, topic)
    topics.csv      topic table: size, keywords, share of 1/2-4/5-star reviews, examples
    topics.md       the same, readable
    staff_names.csv the names that were masked (check the NER did something sensible)
    model/          saved BERTopic model
The sentence split, NER and embeddings are cached, so changing clustering
parameters only reruns UMAP/HDBSCAN.
"""
import argparse
import re
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from src import config
from src.analysis.text import load_google_reviews, rating_group

OUT_ROOT = config.DB_PATH.parent / "topics"
EMBED_MODEL = "sentence-transformers/all-mpnet-base-v2"
NER_MODEL = "dslim/bert-base-NER"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
MIN_WORDS = 3
NER_MAX_CHARS = 1500  # longer "sentences" (no punctuation) are skipped by NER to stay under 512 tokens


def split_sentences(text: str) -> list[str]:
    parts = (p.strip(" -*•\t\"'") for p in SENT_SPLIT.split(text))
    return [p for p in parts if len(p.split()) >= MIN_WORDS]


def mask_names(sentences: list[str], batch_size: int = 64) -> tuple[list[str], Counter]:
    from transformers import pipeline

    ner = pipeline("token-classification", model=NER_MODEL, aggregation_strategy="first",
                   device=0 if DEVICE == "cuda" else -1)
    idx = [i for i, s in enumerate(sentences) if len(s) <= NER_MAX_CHARS]
    results = ner([sentences[i] for i in idx], batch_size=batch_size)
    out, names = list(sentences), Counter()
    for i, ents in zip(idx, results):
        s = sentences[i]
        spans = []
        for e in ents:
            if e["entity_group"] != "PER" or e["score"] < 0.8:
                continue
            a, b = e["start"], e["end"]
            while a > 0 and s[a - 1].isalpha():  # extend partial-word hits to the whole word
                a -= 1
            while b < len(s) and s[b].isalpha():
                b += 1
            spans.append((a, b))
        for a, b in sorted(set(spans), reverse=True):
            names[s[a:b]] += 1
            s = s[:a] + "[STAFF]" + s[b:]
        out[i] = re.sub(r"\[STAFF\](\s*(and|&|,)?\s*\[STAFF\])+", "[STAFF]", s)
    return out, names


GENERIC_NAME_WORDS = {
    "the", "at", "on", "of", "by", "a", "an", "apartments", "apartment", "apts", "apt", "homes", "home",
    "student", "living", "austin", "tx", "atx", "luxury", "housing", "residences", "community", "leasing",
    "office", "center", "and", "&",
}


def property_patterns(name: str) -> list[re.Pattern]:
    """Regexes for a property's own name: the full name and its distinctive core
    ("The Castilian" -> "Castilian", "Villas on Rio Apartments" -> "Villas on Rio")."""
    name = re.split(r"\s+[-|–(]\s*|\s*\(", name or "")[0].strip()
    words = name.split()
    while words and words[0].lower() in GENERIC_NAME_WORDS:
        words.pop(0)
    while words and words[-1].lower() in GENERIC_NAME_WORDS:
        words.pop()
    variants = {name, " ".join(words)}
    return [re.compile(rf"\b(the\s+)?{re.escape(v)}\b", re.I)
            for v in sorted(variants, key=len, reverse=True) if len(v) >= 4]


def mask_property(text: str, patterns: list[re.Pattern]) -> str:
    for p in patterns:
        text = p.sub("[PROPERTY]", text)
    return text


def build_sentences(reviews: pd.DataFrame, out_dir) -> pd.DataFrame:
    rows = []
    for r in reviews.itertuples():
        text = mask_property(r.text, property_patterns(r.property_name))
        rows += [(r.review_id, r.place_id, r.rating, j, s) for j, s in enumerate(split_sentences(text))]
    sent = pd.DataFrame(rows, columns=["review_id", "place_id", "rating", "sent_idx", "raw"])
    print(f"{len(reviews)} reviews -> {len(sent)} sentences; masking names with {NER_MODEL} on {DEVICE}")
    sent["sentence"], names = mask_names(sent["raw"].tolist())
    pd.DataFrame(names.most_common(), columns=["name", "count"]).to_csv(
        out_dir / "staff_names.csv", index=False, encoding="utf-8-sig")
    print(f"masked {sum(names.values())} name mentions ({len(names)} distinct)")
    return sent


def main(zips: list[str] | None, sample: str | None, min_topic_size: int, seed: int, refresh: bool) -> None:
    from bertopic import BERTopic
    from bertopic.representation import KeyBERTInspired
    from hdbscan import HDBSCAN
    from sentence_transformers import SentenceTransformer
    from sklearn.feature_extraction.text import CountVectorizer
    from umap import UMAP

    if sample:
        sample_path = Path(sample)
        label, out_dir = sample_path.stem, OUT_ROOT / sample_path.stem
    else:
        label, out_dir = ", ".join(zips), OUT_ROOT / "_".join(sorted(zips))
    out_dir.mkdir(parents=True, exist_ok=True)
    sent_path, emb_path = out_dir / "sentences.csv", out_dir / "embeddings.npy"

    if sent_path.exists() and not refresh:
        sent = pd.read_csv(sent_path, encoding="utf-8-sig", dtype={"review_id": str})
    else:
        if sample:
            reviews = pd.read_csv(sample_path, encoding="utf-8-sig", dtype={"review_id": str, "place_id": str})
        else:
            reviews = load_google_reviews(zips)
        sent = build_sentences(reviews, out_dir)
    docs = sent["sentence"].tolist()

    embedder = SentenceTransformer(EMBED_MODEL, device=DEVICE)
    if emb_path.exists() and not refresh and len(np.load(emb_path, mmap_mode="r")) == len(docs):
        emb = np.load(emb_path)
    else:
        emb = embedder.encode(docs, batch_size=128, show_progress_bar=True, normalize_embeddings=True)
        np.save(emb_path, emb)

    model = BERTopic(
        embedding_model=embedder,
        umap_model=UMAP(n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", random_state=seed),
        hdbscan_model=HDBSCAN(min_cluster_size=min_topic_size, min_samples=min(10, min_topic_size), metric="euclidean",
                              cluster_selection_method="eom", prediction_data=True),
        vectorizer_model=CountVectorizer(stop_words="english", ngram_range=(1, 2), min_df=5),
        representation_model={"KeyBERT": KeyBERTInspired()},
        verbose=True,
    )
    topics, _ = model.fit_transform(docs, emb)
    sent["topic"] = topics
    sent.to_csv(sent_path, index=False, encoding="utf-8-sig")

    # Share of reviews in each rating group that contain at least one sentence of the topic.
    sent["group"] = sent["rating"].map(rating_group)
    n_reviews = sent.drop_duplicates("review_id")["group"].value_counts()
    per_topic = (sent.drop_duplicates(["review_id", "topic"])
                 .groupby(["topic", "group"]).size().unstack(fill_value=0)
                 .reindex(columns=["1", "2-4", "5"], fill_value=0))
    share = per_topic / n_reviews.reindex(per_topic.columns)

    info = model.get_topic_info().set_index("Topic")
    table = pd.DataFrame({
        "n_sentences": info["Count"],
        "n_reviews": per_topic.sum(axis=1),
        "share_1star": share["1"].round(4),
        "share_2_4star": share["2-4"].round(4),
        "share_5star": share["5"].round(4),
        "neg_lift": (share["1"] / share["5"].clip(lower=1e-4)).round(2),
        "keywords": info["Representation"].map(lambda w: ", ".join(w[:8])),
        "keybert": info["KeyBERT"].map(lambda w: ", ".join(w[:8])),
        "examples": info["Representative_Docs"].map(lambda d: " || ".join(d[:3]) if isinstance(d, list) else ""),
    })
    table.index.name = "topic"
    table.to_csv(out_dir / "topics.csv", encoding="utf-8-sig")

    outliers = (sent["topic"] == -1).mean()
    lines = [f"# BERTopic: {label}", "",
             f"{sent['review_id'].nunique()} reviews, {len(sent)} sentences, "
             f"{len(table) - (-1 in table.index)} topics, {outliers:.0%} outlier sentences (topic -1).",
             f"Reviews per group: {n_reviews.to_dict()}. Shares = fraction of reviews in that star group "
             "with at least one sentence in the topic; neg_lift = share_1star / share_5star.", ""]
    for t, r in table.drop(index=-1, errors="ignore").iterrows():
        lines += [f"## {t}: {r.keywords}",
                  f"{r.n_sentences} sentences / {r.n_reviews} reviews | 1★ {r.share_1star:.1%} · "
                  f"2-4★ {r.share_2_4star:.1%} · 5★ {r.share_5star:.1%} | lift {r.neg_lift}",
                  f"KeyBERT: {r.keybert}", ""]
        lines += [f"- {e}" for e in r.examples.split(" || ") if e] + [""]
    (out_dir / "topics.md").write_text("\n".join(lines), encoding="utf-8")

    model.save(out_dir / "model", serialization="safetensors", save_ctfidf=True, save_embedding_model=EMBED_MODEL)
    print(f"{len(table) - 1} topics, {outliers:.0%} outliers -> {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    src_group = ap.add_mutually_exclusive_group()
    src_group.add_argument("--zip", action="append", help="all reviews in this postal code (repeatable); default 78705")
    src_group.add_argument("--sample", help="CSV from sample_reviews / build_gold_sample instead of whole ZIPs")
    ap.add_argument("--min-topic-size", type=int, default=40, help="use ~15 for a 1,000-review sample")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--refresh", action="store_true", help="redo sentence split, NER and embeddings")
    args = ap.parse_args()
    main(None if args.sample else (args.zip or ["78705"]), args.sample, args.min_topic_size, args.seed, args.refresh)
