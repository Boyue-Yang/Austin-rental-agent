"""Collect only ApartmentRatings property lists and native IDs.

Reuses apartmentratings.py's browser, ZIP discovery and raw-page cache.
No property detail pages or review pages are requested.

Usage:
    python -m src.collectors.apartmentratings_listings --configured-zips
    python -m src.collectors.apartmentratings_listings --zip 78705 --zip 78751
"""
import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from src import config
from src.collectors import apartmentratings as ar
from src.db.schema import connect

FIELDS = ("complex_id", "name", "address", "city", "state", "postal_code", "lat", "lng", "url", "discovered_zipcodes")


def catalog_records(zipcode: str, urls: list[str]) -> list[dict]:
    """Read listing metadata; always take the exact ID from the URL string."""
    metadata = {}
    for path in sorted(ar.ZIP_DIR.glob(f"{zipcode}_p*.json")):
        result = ar.listing_data(json.loads(path.read_text(encoding="utf-8")))
        for row in result.get("complexesShortForm") or []:
            if not isinstance(row, list) or len(row) < 6:
                continue
            # IDs in URL mappings are strings; never convert these to floating point.
            item = {"name":row[1], "address":row[2], "city":row[3], "state":row[4], "postal_code":str(row[5]),
                    "lat":row[18] if len(row) > 18 else None,
                    "lng":row[19] if len(row) > 19 else None}
            metadata.setdefault(str(row[0]), item)
        for field in ("complexes", "leanComplexes", "trimComplexes"):
            for item in result.get(field) or []:
                if not isinstance(item, dict):
                    continue
                link = item.get("aptUrl") or item.get("url") or ""
                match = ar.COMPLEX_URL_RE.fullmatch(link)
                if match:
                    metadata.setdefault(match.group(1), {
                        "name":item.get("complexName") or item.get("name"),
                        "address":item.get("address"), "city":item.get("city"), "state":item.get("state"),
                        "postal_code":str(item.get("zip") or zipcode),
                        "lat":item.get("latitude"), "lng":item.get("longitude"),
                    })
    records = []
    for link in urls:
        match = ar.COMPLEX_URL_RE.fullmatch(link)
        if match is None:
            raise RuntimeError(f"Unexpected ApartmentRatings path: {link}")
        cid = match.group(1)
        item = metadata.get(cid)
        if not item or not item.get("name"):
            raise RuntimeError(f"Missing listing name/address metadata for {cid}, ZIP {zipcode}")
        if item["postal_code"] != zipcode:
            raise RuntimeError(f"Listing {cid} is outside requested ZIP {zipcode}")
        for coordinate, lower, upper in (("lat", -90, 90), ("lng", -180, 180)):
            value = item.get(coordinate)
            if value not in (None, ""):
                value = float(value)
                if not lower <= value <= upper:
                    raise RuntimeError(f"Invalid {coordinate} for {cid}: {value}")
                item[coordinate] = value
            else:
                item[coordinate] = None
        records.append({"complex_id":cid, **item, "url":ar.SITE + link, "discovered_zipcodes":[zipcode]})
    return records


def store_catalog(conn, records: list[dict]) -> None:
    """Fill listing information while retaining detail fields and verified matches."""
    for row in records:
        conn.execute(
            "INSERT INTO ar_properties(complex_id,url,name,address,postal_code,lat,lng) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(complex_id) DO UPDATE SET url=excluded.url, "
            "name=COALESCE(NULLIF(ar_properties.name,''),excluded.name), "
            "address=COALESCE(NULLIF(ar_properties.address,''),excluded.address), "
            "postal_code=COALESCE(NULLIF(ar_properties.postal_code,''),excluded.postal_code), "
            "lat=COALESCE(ar_properties.lat,excluded.lat), lng=COALESCE(ar_properties.lng,excluded.lng)",
            (row["complex_id"], row["url"], row["name"], row["address"], row["postal_code"], row["lat"], row["lng"]),
        )
    conn.commit()


def write_outputs(records: dict[str, dict], statuses: list[dict], zips: list[str], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rows = sorted(records.values(), key=lambda row:(row["postal_code"], row["name"], row["complex_id"]))
    output = directory / "apartmentratings_properties.jsonl"
    temporary = output.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(output)
    output = directory / "apartmentratings_properties.csv"
    temporary = output.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "discovered_zipcodes":";".join(row["discovered_zipcodes"])})
    temporary.replace(output)
    report = {
        "generated_at":datetime.now(timezone.utc).isoformat(), "requested_zipcodes":zips,
        "unique_complexes":len(rows), "processed_zipcodes":len(statuses), "zip_results":statuses,
        "complete":len(statuses)==len(zips) and all(item["status"] != "error" for item in statuses),
    }
    output = directory / "apartmentratings_discovery_report.json"
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)


def collect(zips: list[str]) -> int:
    zips = list(dict.fromkeys(zips))
    records, statuses = {}, []
    directory = config.DB_PATH.parent
    conn = connect()
    try:
        with sync_playwright() as p, ar.chrome_context(p) as context:
            page = context.pages[0] if context.pages else context.new_page()
            for index, zipcode in enumerate(zips, 1):
                print(f"[{index}/{len(zips)}] discovering ZIP {zipcode}", flush=True)
                try:
                    urls = ar.fetch_zip_pages(page, zipcode, None)
                    rows = catalog_records(zipcode, urls)
                    store_catalog(conn, rows)
                    (ar.ZIP_DIR / f"{zipcode}_complexes.json").write_text(json.dumps(urls, indent=1), encoding="utf-8")
                    for row in rows:
                        previous = records.get(row["complex_id"])
                        if previous:
                            previous["discovered_zipcodes"] = sorted(set(previous["discovered_zipcodes"]) | {zipcode})
                        else:
                            records[row["complex_id"]] = row
                    state = "not_found" if not urls and not (ar.ZIP_DIR / f"{zipcode}_p1.json").exists() else "complete"
                    statuses.append({"zip_code":zipcode, "status":state, "complexes":len(rows)})
                    print(f"[{zipcode}] {state}: {len(rows)} complexes; {len(records)} unique overall", flush=True)
                except ar.TargetClosedError:
                    raise
                except (PlaywrightError, RuntimeError, TimeoutError, ValueError, OSError) as exc:
                    conn.rollback()
                    statuses.append({"zip_code":zipcode, "status":"error", "complexes":0,
                                     "error":f"{type(exc).__name__}: {exc}"})
                    print(f"[{zipcode}] ERROR: {exc}", flush=True)
                write_outputs(records, statuses, zips, directory)
    finally:
        conn.close()
    failures = sum(item["status"] == "error" for item in statuses)
    print(f"FINISHED: {len(records)} unique complexes, {len(statuses)}/{len(zips)} ZIP codes processed, {failures} errors", flush=True)
    return failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--zip", action="append", help="ZIP to discover; repeatable")
    options.add_argument("--configured-zips", action="store_true", help="use config.AR_ZIP_CODES (also the default)")
    args = parser.parse_args()
    zips = args.zip or list(config.AR_ZIP_CODES)
    if any(not re.fullmatch(r"[0-9]{5}", zipcode) for zipcode in zips):
        parser.error("ZIP codes must have five digits")
    raise SystemExit(1 if collect(zips) else 0)
