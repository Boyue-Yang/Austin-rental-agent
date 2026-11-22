# Review Labeling and Classifier Training: Task Handoff

This document describes the full task: human gold labels → LLM labels → DeBERTa classifier → labels for every review.
The label definitions are in [annotation_guideline.md](annotation_guideline.md); the schema in code is
`src/labeling/schema.py` (always import labels from there, never retype them).

## Overview

```
48,090 English Google reviews (16 Austin ZIP codes)
 │
 ├─ 500 gold reviews ── human labels (Step 1) ──┬─ dev 100: tune the LLM prompt (Step 2)
 │                                              └─ test 400: final evaluation only (Step 5)
 │
 ├─ ~15,000 training reviews ── LLM labels (Step 3) ── train DeBERTa (Step 4)
 │
 └─ all 48,090 reviews ── DeBERTa predictions (Step 6) ── database tables for the agent
```

Why both: 500 human labels are too few to train 13 topics (some have only ~20 positive examples),
but they are reliable enough to measure quality. The LLM produces many slightly noisier labels to train on.
DeBERTa learns from those and is then cheap and fast enough to label all current and future reviews.

**The 400 test reviews are never used for training or for tuning prompts.** Anything that looks at their labels
before the final evaluation makes the reported numbers meaningless.

## Step 0: Setup

1. Follow the setup in the README (conda env `aus_housing_agent`, Python 3.12, CUDA torch, `pip install -r requirements.txt`).
2. Get a copy of `data/raw/` from the project owner and rebuild the database (no API calls):
   ```bash
   python -m src.collectors.outscraper_places --parse-only
   python -m src.collectors.apify_reviews --parse-only
   ```
3. The gold files are already created in `data/processed/gold/`:
   - `gold_v1.csv`: metadata (review_id, split, rating, weight, property, ZIP). Not for the annotator.
   - `gold_v1_annotation.xlsx`: the annotation workbook.

   They are reproducible with `python -m src.analysis.build_gold_sample` and `python -m src.labeling.make_sheets`
   (the latter refuses to overwrite a workbook that may already contain labels).

## Step 1: Human gold labels (500 reviews)

How the sample was drawn (`src/analysis/build_gold_sample.py`, seed 42):
- Stratified by star rating, with 2-4 star reviews oversampled because they are rare (9% of reviews) but carry
  most mixed and specific opinions: 1★ 150, 2★ 50, 3★ 50, 4★ 50, 5★ 200.
- At most 5 reviews per property, identical texts once; 285 properties across all 16 ZIP codes.
- `weight` = reviews in that star stratum / sampled reviews in it. Use it to report metrics for the real
  rating mix (Step 5).
- `split`: G0001-G0100 are `dev`, G0101-G0500 are `test`.

Procedure: follow the guideline. Stop after G0050 for the checkpoint review; finish; run
`python -m src.labeling.load_gold` until it reports no problems. Output: `gold_v1_labels.csv`.

Deliverable: `gold_v1_labels.csv`, the guideline with any revisions (bump the version and add a changelog line),
and a short note on the hardest cases.

## Step 2: LLM labeling prompt (dev set only)

1. Write `src/labeling/llm_label.py`:
   - The prompt contains the guideline (sections 3-6) and asks for JSON:
     `{"review_type": "...", "topics": {"maintenance_repairs": "pos" | "neg" | null, ...}}`.
     Use the provider's structured-output / JSON-schema mode so the output always parses.
   - Send the star rating, the property name and the review text, the same context the human annotator sees.
     DeBERTa gets the same input as one string, e.g. `"5 stars | <property> | <text>"`, at training and prediction time
     (both are available for every review).
   - Validate every response with `consistency_errors()` from `src/labeling/schema.py`; retry once on violation, then log it.
   - Cache raw responses to `data/raw/llm_labels/<review_id>.json` so reruns are free, like the other collectors.
   - Options `--split dev`, `--input <csv>`, `--max-cost`.
2. Run on the 100 dev reviews and compare with the human labels:
   - review type: accuracy and confusion matrix;
   - each topic: precision / recall / F1 for `pos` and for `neg`.
3. Read the disagreements, improve the prompt (clearer rules, more examples), rerun. Stop when results stop improving.
   Typical targets: review type accuracy ≥ 0.85, macro F1 over topics ≥ 0.75. If a topic stays poor, record it.
4. **Cost estimate before any large run**: from the token counts of the 100-review run, compute the cost for 15,000
   reviews and get approval from the project owner. Always set `--max-cost`.

## Step 3: LLM-labeled training set (~15,000 reviews)

1. Write `src/labeling/build_train_pool.py`: draw from the 48,090 reviews **excluding all 500 gold reviews**
   (use `src.analysis.text.load_google_reviews()`), fixed seed:
   - all 2-4★ reviews (~4,500), 5,000 1★, 5,500 5★.
   - Assign ~10% of **properties** (not reviews) to a validation split, so that reviews of the same property
     are never in both train and validation.
2. Label them with the final prompt. Drop responses that still violate the rules after the retry, and report how many.
3. Output `data/processed/labels/llm_train_v1.jsonl`: `review_id, place_id, split (train / val), text, review_type, topics`.

## Step 4: Train DeBERTa

Write `src/models/train_classifier.py`.

**Model**: one `microsoft/deberta-v3-base` encoder shared by all heads (multi-task), on the [CLS] vector:
- review type head: Linear(768 → 4), softmax;
- 13 topic heads: Linear(768 → 3) each, classes `none / pos / neg` (`TOPIC_CLASSES` in the schema), softmax.

**Loss**: cross-entropy of the type head + sum of the 13 topic cross-entropies. Most topics are `none` for most reviews,
so use class weights per topic head (e.g. inverse square root of class frequency in the training set).

**Settings to start from** (RTX 4060 8 GB):
- max length 256 tokens (median review is ~220 characters), bf16, batch size 16 (or 8 + gradient accumulation 2);
- AdamW, learning rate 2e-5, weight decay 0.01, linear warmup 6%, 3-5 epochs;
- evaluate on the validation split every epoch; keep the checkpoint with the best mean topic macro-F1; seed fixed.

**After training**: tune a decision rule per topic on the validation split if `pos`/`neg` recall is too low
(e.g. predict `neg` when p(neg) > threshold instead of argmax). Save the model, the thresholds, the label schema
version and the training config to `models/review_classifier_v1/` (not committed; share it like the data).

Baseline to train on the same data: TF-IDF + logistic regression, one classifier per head (`src/models/baseline.py`).

## Step 5: Evaluation on the 400 test reviews

Run once, at the end, for every system:

| System | What it shows |
|---|---|
| TF-IDF + logistic regression | Simple baseline |
| LLM labels (final prompt) | Quality of the training labels; roughly the ceiling for DeBERTa |
| DeBERTa | The model we deploy |

Report:
- review type: accuracy, macro F1, confusion matrix;
- each topic: F1 of `pos` and of `neg`, and the number of gold examples behind each (small counts → wide uncertainty);
- mean topic macro F1;
- all of the above unweighted and weighted with `weight` (the weighted numbers estimate performance on the real rating mix);
- rule-violation rate of DeBERTa predictions (e.g. `generic` with topics);
- error analysis: 20-30 wrong predictions grouped by cause.

## Step 6: Label all reviews and load them for the agent

Write `src/models/predict.py`: run the model on all English Google reviews, apply the type constraints
(`generic` → no topics; `staff_shoutout` → only `staff_management`), and write two tables (schema in `src/db/schema.py`):

```sql
review_labels (source, review_id, review_type, p_lived_experience, p_prospective, p_staff_shoutout, p_generic,
               model_version, PRIMARY KEY (source, review_id))
review_topics (source, review_id, topic, label, p_none, p_pos, p_neg, model_version,
               PRIMARY KEY (source, review_id, topic))
```

Then a view for the agent, e.g. per property and topic: number of `lived_experience` reviews, share with `pos`,
share with `neg`. Only `lived_experience` reviews count toward these shares.

## Deliverables checklist

- [ ] `gold_v1_labels.csv` passes `load_gold` with no problems; guideline changelog updated
- [ ] `llm_label.py` with cached responses; dev-set comparison report; cost estimate approved
- [ ] `llm_train_v1.jsonl` (~15k) with property-level train/val split
- [ ] `train_classifier.py`, `baseline.py`; trained model + config + thresholds
- [ ] Test-set report (Step 5) as a markdown file in `docs/`
- [ ] `predict.py`; `review_labels` / `review_topics` filled for all reviews
