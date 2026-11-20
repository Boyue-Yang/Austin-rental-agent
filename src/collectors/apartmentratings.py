"""Collect ApartmentRatings.com complexes, reviews and floor plans.

Plain HTTP and Playwright-launched Chrome can receive Cloudflare challenges.
Start installed Chrome as a separate process with a dedicated persistent profile,
then attach Playwright over a localhost Chrome DevTools Protocol (CDP) connection.
This loaded listing and review pages 1 and 2 without human interaction in a small
2026-11-19 test. It is not a guarantee against future challenges: if a page still
requires verification, fail clearly instead of waiting for a human click.

All page data is read from the Next.js `<script id="__NEXT_DATA__">` JSON.

Usage:
    python -m src.collectors.apartmentratings --configured-zips --all-reviews
    python -m src.collectors.apartmentratings --zip 78705 --zip 78751
    python -m src.collectors.apartmentratings --zip 78705 --all-reviews
    python -m src.collectors.apartmentratings --zip 78705 --review-pages 3
    python -m src.collectors.apartmentratings --zip 78705 --zip-pages 1 --max-complexes 2
    python -m src.collectors.apartmentratings --parse-only

Raw cache (existing files are skipped, so reruns resume):
    data/raw/apartmentratings/zips/<zip>_p<N>.json
    data/raw/apartmentratings/complexes/<complexId>_p<N>.json

Analysis output (one review per line, refreshed after parsing):
    data/processed/reviews.jsonl (both Google Maps and ApartmentRatings)
"""
import argparse
import json
import os
import random
import re
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from playwright._impl._errors import TargetClosedError
from playwright.sync_api import (
    Error as PlaywrightError,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from src import config
from src.db.schema import connect
from src.db.reviews import export_reviews, save_review

SITE = "https://www.apartmentratings.com"
ZIP_DIR = config.AR_RAW_DIR / "zips"
COMPLEX_DIR = config.AR_RAW_DIR / "complexes"
COMPLEX_URL_RE = re.compile(r"/tx/[a-z0-9-]+/[a-z0-9-]+_(\d+)/")
PAGE_DATA_TIMEOUT_MS = 30000


# --------------------------------------------------------------------------- browser


class CloudflareChallengeError(RuntimeError):
    """A page still requires verification after allowing browser scripts to run."""


@contextmanager
def chrome_context(playwright: Playwright):
    """Launch a dedicated ordinary Chrome process, then attach over local CDP."""
    candidates = [shutil.which("chrome"), shutil.which("google-chrome")]
    for variable, fallback in [("PROGRAMFILES", "C:/Program Files"),
                               ("PROGRAMFILES(X86)", "C:/Program Files (x86)"),
                               ("LOCALAPPDATA", "")]:
        directory = os.environ.get(variable, fallback)
        if directory:
            candidates.append(str(Path(directory) / "Google/Chrome/Application/chrome.exe"))
    executable = next((p for p in candidates if p and Path(p).is_file()), None)
    if executable is None:
        raise RuntimeError("Google Chrome was not found; install Chrome or add it to PATH.")

    config.AR_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [executable, f"--remote-debugging-port={port}",
         "--remote-debugging-address=127.0.0.1",
         f"--user-data-dir={config.AR_PROFILE_DIR.resolve()}",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    browser = None
    try:
        # Local readiness check only; do not send the debug connection through a proxy.
        opener = build_opener(ProxyHandler({}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "Crawler Chrome exited before startup. Close any other crawler "
                    f"using the profile {config.AR_PROFILE_DIR} and retry."
                )
            try:
                with opener.open(endpoint + "/json/version", timeout=0.5) as response:
                    json.load(response)
                break
            except (URLError, TimeoutError):
                time.sleep(0.2)
        else:
            raise TimeoutError("Chrome's local debug connection did not become ready.")
        browser = playwright.chromium.connect_over_cdp(endpoint, timeout=20000)
        yield browser.contexts[0]
    finally:
        if browser is not None:
            try:
                browser.new_browser_cdp_session().send("Browser.close")
            except PlaywrightError:
                pass  # The test window may already have been closed by the user.
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)


def load_next_data(page: Page, url: str) -> dict | None:
    """Load page JSON, allowing automatic checks but never waiting for human input."""
    resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
    challenged = resp is not None and resp.headers.get("cf-mitigated") == "challenge"
    if resp is not None and resp.status == 404 and not challenged:
        return None
    if resp is not None and resp.status >= 400 and not challenged:
        raise RuntimeError(f"HTTP {resp.status} loading {url}")
    node = page.locator("script#__NEXT_DATA__")
    try:
        node.wait_for(state="attached", timeout=PAGE_DATA_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        if challenged or "just a moment" in page.title().lower():
            raise CloudflareChallengeError(
                f"Cloudflare still requires verification for {url}. "
                "Stopping without a manual wait; completed pages remain cached."
            ) from exc
        raise TimeoutError(f"No __NEXT_DATA__ after {PAGE_DATA_TIMEOUT_MS // 1000}s: {url}") from exc
    return json.loads(node.text_content())


def pause() -> None:
    time.sleep(random.uniform(*config.AR_DELAY_SECONDS))


def queries(nd: dict) -> dict[str, dict]:
    """Map queryKey[0] -> query state data from the dehydrated React Query cache."""
    out = {}
    for q in nd.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", []):
        key = q.get("queryKey") or [None]
        out.setdefault(key[0], (q.get("state") or {}).get("data"))
    return out


# --------------------------------------------------------------------------- fetch


def listing_data(nd: dict) -> dict:
    """Use only the ZIP search result, excluding unrelated recommendations."""
    data = queries(nd).get("getComplexesWithinBoundsFiltered") or {}
    result = data.get("complexes")
    if not isinstance(result, dict) or result.get("success") is False:
        raise RuntimeError("Missing or unsuccessful ZIP search result.")
    if result.get("totalHits") == 0 and result.get("complexUrls") is None:
        return {**result, "complexUrls": {}}
    if not isinstance(result.get("complexUrls"), dict):
        raise RuntimeError("ZIP search result has no complex URL mapping.")
    return result


def fetch_zip_pages(page: Page, zipcode: str, max_pages: int | None) -> list[str]:
    """Discover the ZIP's complexes; some pages already embed the entire result."""
    ZIP_DIR.mkdir(parents=True, exist_ok=True)
    urls: dict[str, str] = {}
    seen: set[str] = set()
    n = 1
    while max_pages is None or n <= max_pages:
        path = ZIP_DIR / f"{zipcode}_p{n}.json"
        fetched = not path.exists()
        if not fetched:
            nd = json.loads(path.read_text(encoding="utf-8"))
        else:
            url = f"{SITE}/tx/{zipcode}/" + ("" if n == 1 else f"?page={n}")
            print(f"zip {zipcode} page {n}", flush=True)
            nd = load_next_data(page, url)
            if nd is None:
                if n == 1:
                    print(f"zip {zipcode}: listing not found (HTTP 404); no complexes discovered", flush=True)
                    return []
                raise RuntimeError(f"ZIP listing returned 404 before discovery completed: {url}")
        result = listing_data(nd)
        rows = result.get("complexesShortForm") or []
        zip_by_id = {str(row[0]): str(row[5]) for row in rows if isinstance(row, list) and len(row) > 5}
        found: dict[str, str] = {}
        for link in result["complexUrls"].values():
            match = COMPLEX_URL_RE.fullmatch(link) if isinstance(link, str) else None
            if match is None:
                raise RuntimeError(f"Unrecognized complex URL in ZIP {zipcode}: {link!r}")
            found[match.group(1)] = link
        if fetched:
            path.write_text(json.dumps(nd, ensure_ascii=False), encoding="utf-8")
            pause()
        new = set(found) - seen
        seen.update(found)
        for cid, link in found.items():
            if zip_by_id.get(cid, zipcode) == zipcode:
                urls.setdefault(cid, link)
        total = result.get("totalHits")
        if not isinstance(total, int) or total < 0:
            raise RuntimeError(f"Missing ZIP result count for {zipcode}")
        if len(seen) >= total:
            break
        if not new:
            raise RuntimeError(f"ZIP {zipcode} repeated a page before all results were found ({len(seen)}/{total}).")
        n += 1
    print(f"zip {zipcode}: {len(urls)} complexes", flush=True)
    return list(urls.values())


def review_records(data: dict):
    """Flatten review groups without dropping additional entries in a group."""
    for group in data.get("reviews") or []:
        for review in group if isinstance(group, list) else [group]:
            if isinstance(review, dict):
                yield review


def fetch_complex(page: Page, path_url: str, review_pages: int | None) -> None:
    """Collect details and review pages; None means all pages in the site's query."""
    COMPLEX_DIR.mkdir(parents=True, exist_ok=True)
    cid = COMPLEX_URL_RE.fullmatch(path_url).group(1)
    total_pages = None
    seen_ids: set[str] = set()
    n = 1
    while total_pages is None or n <= total_pages:
        path = COMPLEX_DIR / f"{cid}_p{n}.json"
        fetched = not path.exists()
        if not fetched:
            nd = json.loads(path.read_text(encoding="utf-8"))
        else:
            url = SITE + path_url + ("" if n == 1 else f"?page={n}")
            print(f"  {path_url} review page {n}", flush=True)
            nd = load_next_data(page, url)
            if nd is None:
                raise RuntimeError(f"Complex or review page returned 404: {url}")
        review_query = next((q for q in nd.get("props", {}).get("pageProps", {})
                             .get("dehydratedState", {}).get("queries", [])
                             if (q.get("queryKey") or [None])[0] == "searchReviewsV4"), None)
        if review_query is None:
            raise RuntimeError(f"Missing review query for {cid}, page {n}")
        params = review_query["queryKey"][1]
        if str(params.get("complexId")) != cid or params.get("page") != n - 1:
            raise RuntimeError(f"Unexpected review property/page in {path.name}")
        rq = (review_query.get("state") or {}).get("data") or {}
        hits, page_size = rq.get("totalHits"), params.get("pageSize")
        if rq.get("success") is False or not isinstance(hits, int) or hits < 0:
            raise RuntimeError(f"Invalid review result for {cid}, page {n}")
        if not isinstance(page_size, int) or page_size <= 0:
            raise RuntimeError(f"Invalid review page size for {cid}")
        available_pages = max(1, -(-hits // page_size))
        total_pages = available_pages if review_pages is None else min(review_pages, available_pages)
        ids = {rv.get("id") or (rv.get("strings") or {}).get("id") for rv in review_records(rq)} - {None, ""}
        if hits and (not ids or (n > 1 and not ids - seen_ids)):
            raise RuntimeError(f"Empty or repeated review page for {cid}, page {n}; collection is incomplete.")
        seen_ids.update(ids)
        if fetched:
            path.write_text(json.dumps(nd, ensure_ascii=False), encoding="utf-8")
            pause()
        n += 1
    if review_pages is None and len(seen_ids) < hits:
        raise RuntimeError(f"Incomplete reviews for {cid}: {len(seen_ids)} unique IDs, query reports {hits}.")
    print(f"  {cid}: {n - 1}/{available_pages} review pages, {len(seen_ids)} unique reviews "
          f"(query total {hits})", flush=True)


def fetch(zips: list[str], review_pages: int | None, max_complexes: int | None, zip_pages: int | None) -> None:
    with sync_playwright() as p, chrome_context(p) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        seen_complexes: set[str] = set()
        try:
            for z in dict.fromkeys(zips):
                urls = fetch_zip_pages(page, z, zip_pages)
                (ZIP_DIR / f"{z}_complexes.json").write_text(json.dumps(urls, indent=1), encoding="utf-8")
                if max_complexes:
                    urls = urls[:max_complexes]
                for i, u in enumerate(urls, 1):
                    cid = COMPLEX_URL_RE.fullmatch(u).group(1)
                    if cid in seen_complexes:
                        continue
                    print(f"[{z}] complex {i}/{len(urls)}", flush=True)
                    fetch_complex(page, u, review_pages)
                    seen_complexes.add(cid)
        except TargetClosedError:
            raise SystemExit("Browser window was closed; rerun to resume from cached pages.") from None
        except CloudflareChallengeError as exc:
            raise SystemExit(str(exc)) from None


# --------------------------------------------------------------------------- parse


def ms_to_iso(ms) -> str | None:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else None


def first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def parse_review(rv: dict, cid: str) -> tuple | None:
    s, ints = rv.get("strings") or {}, rv.get("integers") or {}
    floats, bools = rv.get("floats") or {}, rv.get("booleans") or {}
    rid = rv.get("id") or s.get("id")
    text = (s.get("review_text_s") or "").strip()
    if not rid or not text:
        return None
    times = sorted(v["time"] for v in (rv.get("dates") or {}).values() if isinstance(v, dict) and v.get("time"))
    aspects = {k[len("rating_"):-2]: v for k, v in ints.items() if k.startswith("rating_") and k.endswith("_i")}
    return (
        rid,
        cid,
        floats.get("rating_overall_f"),
        json.dumps(aspects),
        s.get("review_title_s"),
        text,
        s.get("response_text_s"),
        s.get("reviewer_description_s"),
        ints.get("begin_year_i"),
        ints.get("end_year_i"),
        int(bool(bools.get("is_incentivized_b"))),
        ms_to_iso(times[0]) if times else None,
        ms_to_iso(times[-1]) if times else None,
    )


def parse() -> None:
    conn = connect()
    zip_of: dict[str, str] = {}
    for f in ZIP_DIR.glob("*_complexes.json"):
        z = f.name.split("_")[0]
        for u in json.loads(f.read_text(encoding="utf-8")):
            zip_of.setdefault(COMPLEX_URL_RE.search(u).group(1), z)

    fields = ("review_id", "complex_id", "rating", "aspect_ratings", "title", "text",
              "owner_reply", "reviewer_type", "begin_year", "end_year", "incentivized",
              "posted_at", "updated_at")
    property_fields = ("complex_id", "url", "name", "address", "postal_code", "lat", "lng",
                       "management", "review_total", "avg_ratings", "fees", "info")
    n_props = n_reviews = n_plans = 0
    for path in sorted(COMPLEX_DIR.glob("*_p*.json")):
        cid, pnum = path.stem.rsplit("_p", 1)
        q = queries(json.loads(path.read_text(encoding="utf-8")))
        rq = q.get("searchReviewsV4") or {}
        source_path = (q.get("getComplexURL") or {}).get("url")
        source_url = SITE + source_path if source_path else None
        if pnum == "1":
            info = q.get("getComplexInfo") or {}
            addl = q.get("getAddlComplexInfo") or {}
            addr = info.get("address") if isinstance(info.get("address"), dict) else {}
            # Preserve any manually verified Google place_id when refreshing details.
            conn.execute(
                f"INSERT INTO ar_properties ({','.join(property_fields)}) VALUES ({','.join('?' for _ in property_fields)}) "
                "ON CONFLICT(complex_id) DO UPDATE SET " +
                ",".join(f"{field}=excluded.{field}" for field in property_fields if field != "complex_id"),
                (cid, source_url, first(info, "name", "complexName"),
                 first(addr, "streetAddress", "street", "address1") or first(info, "streetAddress", "address1", "address"),
                 first(addr, "zip", "postalCode") or info.get("zip") or zip_of.get(cid),
                 first(info, "latitude", "lat") or first(addr, "latitude", "lat"),
                 first(info, "longitude", "lng", "lon") or first(addr, "longitude", "lng", "lon"),
                 addl.get("managementName"), rq.get("totalHits"),
                 json.dumps(q.get("getAverageRatings"), ensure_ascii=False),
                 json.dumps(q.get("getComplexFees"), ensure_ascii=False),
                 json.dumps(info, ensure_ascii=False)),
            )
            n_props += 1
            fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).date().isoformat()
            plans = q.get("getFloorPlans") or []
            if isinstance(plans, dict):
                plans = plans.get("floorPlans") or plans.get("items") or []
            for fp in plans:
                conn.execute(
                    "INSERT OR REPLACE INTO ar_floor_plans VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, fp.get("name"), fp.get("bedrooms"), fp.get("bathrooms"),
                     fp.get("squareFeetMin"), fp.get("squareFeetMax"), fp.get("rentMin"),
                     fp.get("rentMax"), fp.get("deposit"), fp.get("unitsAvailable"), fetched_at),
                )
                n_plans += 1
        for rv in review_records(rq):
            row = parse_review(rv, cid)
            if row:
                review = dict(zip(fields, row))
                review.update(source="apartmentratings", collector="playwright", source_url=source_url)
                save_review(conn, review)
                n_reviews += 1
    conn.commit()
    export_reviews(conn)
    conn.close()
    print(f"stored {n_props} complexes, {n_reviews} reviews with text, {n_plans} floor plans")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    zip_options = ap.add_mutually_exclusive_group()
    zip_options.add_argument("--zip", action="append", help="zip code to crawl (repeatable)")
    zip_options.add_argument("--configured-zips", action="store_true",
                             help="crawl the ZIP codes in config.AR_ZIP_CODES")
    review_options = ap.add_mutually_exclusive_group()
    review_options.add_argument("--review-pages", type=int, default=1,
                                help="maximum review pages per complex (default: 1)")
    review_options.add_argument("--all-reviews", action="store_true",
                                help="collect every review page returned by the site's default review query")
    ap.add_argument("--zip-pages", type=int, help="max listing pages per zip")
    ap.add_argument("--max-complexes", type=int, help="per zip, for testing")
    ap.add_argument("--parse-only", action="store_true")
    args = ap.parse_args()
    for option in ("review_pages", "zip_pages", "max_complexes"):
        value = getattr(args, option)
        if value is not None and value < 1:
            ap.error(f"--{option.replace('_', '-')} must be at least 1")
    if args.zip and any(not re.fullmatch(r"[0-9]{5}", z) for z in args.zip):
        ap.error("--zip must be a five-digit ZIP code")
    if not args.parse_only:
        zips = list(config.AR_ZIP_CODES) if args.configured_zips else args.zip
        if not zips:
            raise SystemExit("give at least one --zip or use --configured-zips")
        fetch(zips, None if args.all_reviews else args.review_pages, args.max_complexes, args.zip_pages)
    parse()
