# Austin Housing Agent

An LLM agent that helps UT Austin students choose an apartment. It answers questions such as
*"Which West Campus apartments have the fewest maintenance complaints?"* or *"Is this place noisy at night?"*
using structured property data and tens of thousands of real resident reviews.

> **Status: work in progress.** Data collection is done; review analysis and the agent itself are under development.

## Progress

- [x] Property discovery: 31 Google Maps search queries → 694 rental properties across 16 Austin ZIP codes
- [x] Google Maps reviews: ~54k reviews with text (since 2022-01-01)
- [x] ApartmentRatings.com listings: 881 complexes (floor plans, prices, sub-ratings for maintenance, noise, safety, etc.)
- [ ] Cross-site property matching (Google Maps ↔ ApartmentRatings)
- [ ] Topic discovery with BERTopic
- [ ] Review labeling: review type + aspect sentiment (LLM labels, human gold set)
- [ ] Fine-tuned DeBERTa multi-label classifier
- [ ] Agent tools: parameterized property search, restricted text-to-SQL, review RAG

## Planned architecture

The agent routes each question to one of three tools:

| Tool | Used for | How |
|---|---|---|
| `search_properties` | "2-bed under $1,500 near campus" | The LLM fills in parameters; the SQL is fixed in code |
| `run_sql` | Open-ended comparisons and statistics | Read-only text-to-SQL over curated views, with a forced `LIMIT` |
| `search_reviews` | "What do residents say about noise?" | Filter by property, aspect and date, then retrieve sentences by vector similarity |

Aspect-level labels from the review classifier (maintenance, staff, noise, pests, safety, parking, fees,
deposit, …) are aggregated per property, so these tools can query them.

## Data pipeline

```
Outscraper (Google Maps search) ──► filter & dedupe ──► properties
Apify (Google Maps reviews)     ──► reviews (source = google_maps)
ApartmentRatings.com (Playwright + Chrome) ──► ar_properties, ar_floor_plans, reviews (source = apartmentratings)
                                                   │
                                                   ▼
                                      SQLite: data/processed/austin_housing.db
                                      JSONL:  data/processed/reviews.jsonl
```

- Raw API and page responses are cached under `data/raw/`. Reruns skip anything already fetched, so they are not billed twice.
- Reviewer identity (names, user IDs) is never stored.
- `data/` is not part of this repository. Run the collectors to rebuild it.

## Setup

```bash
conda create -n aus_housing_agent python=3.12
conda activate aus_housing_agent
pip install torch --index-url https://download.pytorch.org/whl/cu126   # CUDA build; adjust to your system
pip install -r requirements.txt
python -m playwright install
```

Create a `.env` file in the project root:

```
OUTSCRAPER_API_KEY=...
APIFY_API_TOKEN=...
```

## Usage

Run all commands from the project root. Search queries, ZIP codes and filters are set in `src/config.py`.

```bash
# 1. Discover properties (Google Maps via Outscraper)
python -m src.collectors.outscraper_places
python -m src.collectors.outscraper_places --parse-only        # re-apply filters to the cached data

# 2. Fetch Google Maps reviews (Apify); test small first and set a cost cap
python -m src.collectors.apify_reviews --max-places 3 --reviews-limit 50 --max-cost 0.5
python -m src.collectors.apify_reviews --zip 78705 --max-cost 8

# 3. ApartmentRatings.com listings and reviews (uses a locally installed Chrome)
python -m src.collectors.apartmentratings_listings --configured-zips
python -m src.collectors.apartmentratings --zip 78705 --all-reviews

# 4. Draw a stratified review sample for annotation
python -m src.analysis.sample_reviews --zip 78705 --n 500

# Export all reviews to JSONL
python -m src.db.reviews
```

## Project layout

```
src/
  config.py          search queries, ZIP codes, filters, paths
  collectors/        Outscraper, Apify and ApartmentRatings collectors
  db/                SQLite schema (with migrations) and review storage/export
  analysis/          sampling and, later, topic modeling and labeling
tests/
```
