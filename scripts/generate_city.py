#!/usr/bin/env python3
"""
generate_city.py -- build a HostBrief city benchmark page from a per-city JSON
config and an Inside Airbnb ``listings.csv.gz`` snapshot.

Stdlib only (csv + statistics + gzip + json). No third-party deps; do NOT add a
package.json to this static repo.

Occupancy and revenue are NOT modelled here. We read Inside Airbnb's OWN published
per-listing estimates straight from the snapshot:

    occupancy = estimated_occupancy_l365d      (nights booked, trailing 12 months)
    revenue   = estimated_revenue_l365d        (price * occupancy, trailing 12 months)

There are deliberately no review-rate / length-of-stay / occupancy-cap constants in
this file -- those belong to Inside Airbnb's estimator, not ours. What we compute is
only the nightly price, the listing counts, and the area / bedroom groupings, plus the
medians and percentiles over Inside Airbnb's numbers. The page copy attributes each
figure accordingly.

Geography = ``neighbourhood_cleansed``. An "active" listing is one with
``number_of_reviews_ltm > 0``. ``total_active`` counts ALL room types; the area and
bedroom tables are built from ENTIRE-HOME listings only. Every displayed median is a
median of PER-LISTING values (never a product of medians).

Usage:
    python scripts/generate_city.py --config configs/austin.json \
        --csv data/austin-2026-06-22.listings.csv.gz --out austin/index.html
"""
import argparse
import csv
import gzip
import json
import os
import re
import statistics
import sys

# ---------------------------------------------------------------- thresholds
AREA_MIN = 25                          # min entire-home listings to publish an area
BED_MIN = 10                           # min listings to publish a bedroom cohort
BED_BUCKETS = 5                        # 0,1,2,3,4+  (4 == "4 or more")

GEO = {
    "ZIP":  {"noun": "ZIP code",     "header": "ZIP code area",
             "default_label": lambda z: "ZIP {}".format(z)},
    "Area": {"noun": "neighbourhood", "header": "Neighbourhood",
             "default_label": lambda z: z},
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- parsing helpers
def parse_price(raw):
    if raw is None:
        return None
    s = raw.replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def parse_float(raw):
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_int(raw):
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def imedian(values):
    """Median of a list, rounded to a plain int (matches the committed display)."""
    return int(round(statistics.median(values)))


def percentile(values, pct):
    """Type-7 (linear interpolation) percentile -- numpy's default."""
    s = sorted(values)
    n = len(s)
    if n == 1:
        return float(s[0])
    pos = (pct / 100.0) * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 < n:
        return s[lo] + frac * (s[lo + 1] - s[lo])
    return float(s[lo])


# ---------------------------------------------------------------- core model
def build_data(csv_path, geography_label, area_labels, include_areas=None):
    """Return the DATA dict that gets embedded in the page.

    Occupancy and revenue come straight from Inside Airbnb's own per-listing
    estimates (estimated_occupancy_l365d / estimated_revenue_l365d). We only
    compute price, counts, groupings, and the medians/percentiles over them.

    ``include_areas`` (optional) restricts the whole build to a set of raw
    ``neighbourhood_cleansed`` values -- used to carve a city (e.g. Perth) out of
    a wider Inside Airbnb region file (Western Australia).
    """
    label_for = GEO[geography_label]["default_label"]
    include = set(include_areas) if include_areas else None

    def display_label(raw):
        if raw in area_labels:
            return area_labels[raw]
        # tidy ALL-CAPS region labels (e.g. Perth LGAs) for neighbourhood pages
        if geography_label == "Area" and raw.isupper():
            return raw.title()
        return label_for(raw)

    total_active = 0
    entire_active = 0
    # scored entire-home listings: (area_label, bed_bucket_or_None, price, occ, rev)
    scored = []

    opener = gzip.open if csv_path.endswith(".gz") else open
    with opener(csv_path, "rt", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if include is not None and row.get("neighbourhood_cleansed") not in include:
                continue                      # outside the carved city footprint
            if (parse_int(row.get("number_of_reviews_ltm")) or 0) <= 0:
                continue                      # not an active listing
            total_active += 1
            if row.get("room_type") != "Entire home/apt":
                continue
            entire_active += 1
            price = parse_price(row.get("price"))
            occ = parse_int(row.get("estimated_occupancy_l365d"))
            rev = parse_float(row.get("estimated_revenue_l365d"))
            if price is None or occ is None or rev is None:
                continue    # need price + Inside Airbnb's own occ/rev estimates
            bed = parse_int(row.get("bedrooms"))
            bucket = min(bed, BED_BUCKETS - 1) if bed is not None else None
            scored.append((display_label(row["neighbourhood_cleansed"]),
                           bucket, price, occ, rev))

    if not scored:
        sys.exit("No scorable entire-home listings found in {}".format(csv_path))

    entire_share = int(round(100.0 * entire_active / total_active))

    all_price = [s[2] for s in scored]
    all_occ = [s[3] for s in scored]
    all_rev = [s[4] for s in scored]
    overall = {
        "total_active": total_active,
        "median_price": imedian(all_price),
        "median_occ": imedian(all_occ),
        "median_rev": imedian(all_rev),
        "entire_share": entire_share,
    }

    # --- areas ---
    by_area = {}
    for label, _bucket, price, occ, rev in scored:
        by_area.setdefault(label, []).append((price, occ, rev))
    subs = []
    for label, items in by_area.items():
        if len(items) < AREA_MIN:
            continue
        prices = [i[0] for i in items]
        occs = [i[1] for i in items]
        revs = [i[2] for i in items]
        subs.append({
            "suburb": label,
            "n": len(items),
            "median_price": imedian(prices),
            "median_occ": imedian(occs),
            "median_rev": imedian(revs),
            "rev_top25": int(round(percentile(revs, 75))),
        })
    subs.sort(key=lambda s: (-s["median_rev"], s["suburb"]))

    # --- bedroom cohorts (independent of the area minimum) ---
    by_cohort = {}
    for label, bucket, price, occ, rev in scored:
        if bucket is None:
            continue
        by_cohort.setdefault(label, {}).setdefault(bucket, []).append((price, occ, rev))
    cohort = {}
    for label in sorted(by_cohort):
        beds = {}
        for bucket in sorted(by_cohort[label]):
            items = by_cohort[label][bucket]
            if len(items) < BED_MIN:
                continue
            prices = [i[0] for i in items]
            occs = [i[1] for i in items]
            revs = [i[2] for i in items]
            beds[str(bucket)] = {
                "n": len(items),
                "price": imedian(prices),
                "occ": imedian(occs),
                "rev": imedian(revs),
                "rev_top25": int(round(percentile(revs, 75))),
            }
        if beds:
            cohort[label] = beds

    return {"overall": overall, "subs": subs, "cohort": cohort}


# ---------------------------------------------------------------- rendering
def render_page(template, config, data):
    geo = GEO[config["geography_label"]]
    year = re.search(r"(\d{4})", config["snapshot_month"]).group(1)
    region = config.get("region_code")
    tag_location = config.get("tag_location") or (
        "{}, {}".format(config["display_name"], region) if region
        else config["display_name"])
    ov = data["overall"]
    data_json = json.dumps(data, separators=(",", ":"), ensure_ascii=True)

    tokens = {
        "@@DATA@@": data_json,
        "@@CCY@@": config["currency_symbol"],
        "@@CITY@@": config["display_name"],
        "@@TAG_LOCATION@@": tag_location,
        "@@TITLE_YEAR@@": year,
        "@@LEDE_COUNT@@": "{:,}".format(ov["total_active"]),
        "@@STAT_PRICE@@": "{:,}".format(ov["median_price"]),
        "@@STAT_OCC@@": str(ov["median_occ"]),
        "@@STAT_REV@@": "{:,}".format(ov["median_rev"]),
        "@@STAT_SHARE@@": str(ov["entire_share"]),
        "@@GEO_NOUN@@": geo["noun"],
        "@@GEO_HEADER@@": geo["header"],
        "@@SNAPSHOT@@": config["snapshot_month"],
        "@@GROUPED_BY@@": config["grouped_by_copy"],
    }
    out = template
    for key, val in tokens.items():
        out = out.replace(key, val)
    leftover = re.findall(r"@@[A-Z_]+@@", out)
    if leftover:
        sys.exit("Unfilled template tokens: {}".format(sorted(set(leftover))))
    return out


# ---------------------------------------------------------------- hub + sitemap
def update_sitemap(path, slug, base="https://benchmarks.hostbrief.app"):
    """Insert the city's <url> in alphabetical order if it is missing."""
    text = open(path, "r", encoding="utf-8", newline="").read()
    loc = "{}/{}/".format(base, slug)
    if loc in text:
        return False
    line = ('  <url><loc>{}</loc><changefreq>weekly</changefreq>'
            '<priority>0.8</priority></url>\n').format(loc)
    lines = text.splitlines(keepends=True)
    urls = [i for i, ln in enumerate(lines)
            if "/loc>" in ln and "benchmarks.hostbrief.app/" in ln
            and ">https://benchmarks.hostbrief.app/<" not in ln]
    insert_at = None
    for i in urls:
        m = re.search(r"hostbrief\.app/([^/]+)/", lines[i])
        if m and slug < m.group(1):
            insert_at = i
            break
    if insert_at is None:
        insert_at = (urls[-1] + 1) if urls else len(lines) - 1
    lines.insert(insert_at, line)
    open(path, "w", encoding="utf-8", newline="").write("".join(lines))
    return True


def update_hub_counts(path, city_count, country_count):
    """Rewrite the city-count and country-count strings on the hub."""
    text = open(path, "r", encoding="utf-8", newline="").read()
    subs = [
        ("for 42 cities across 9 countries", "for {} cities across {} countries"),
        ("Benchmarks, 42 Cities Worldwide", "Benchmarks, {} Cities Worldwide"),
        ("42 cities and counting", "{} cities and counting"),
        ("Benchmarks · 42 Cities Worldwide", "Benchmarks · {} Cities Worldwide"),
        ('>42 <small style="font-size:.45em;font-weight:600;color:var(--gold)">cities<',
         '>{} <small style="font-size:.45em;font-weight:600;color:var(--gold)">cities<'),
        ('>9 <small style="font-size:.45em;font-weight:600;color:var(--gold)">countries<',
         '>{} <small style="font-size:.45em;font-weight:600;color:var(--gold)">countries<'),
        ("Browse all 42 pricing benchmarks", "Browse all {} pricing benchmarks"),
    ]
    changed = 0
    for old, tmpl in subs:
        if old not in text:
            continue
        new = tmpl.format(city_count, country_count) if "countr" in tmpl else \
            tmpl.format(city_count)
        text = text.replace(old, new)
        changed += 1
    open(path, "w", encoding="utf-8", newline="").write(text)
    return changed


# ---------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description="Generate a HostBrief city benchmark page.")
    ap.add_argument("--config", required=True, help="per-city JSON config")
    ap.add_argument("--csv", help="listings.csv.gz (defaults to config.source_csv)")
    ap.add_argument("--out", help="output HTML path (defaults to <slug>/index.html)")
    ap.add_argument("--template", default=os.path.join(REPO_ROOT, "scripts", "template.html"))
    ap.add_argument("--update-hub", action="store_true", help="also rewrite hub counts")
    ap.add_argument("--update-sitemap", action="store_true", help="also add sitemap entry")
    ap.add_argument("--city-count", type=int, help="new city count for --update-hub")
    ap.add_argument("--country-count", type=int, help="new country count for --update-hub")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    csv_path = args.csv or os.path.join(REPO_ROOT, config["source_csv"])
    template = open(args.template, "r", encoding="utf-8", newline="").read()

    data = build_data(csv_path, config["geography_label"], config.get("area_labels", {}),
                      include_areas=config.get("include_areas"))
    html = render_page(template, config, data)

    out_path = args.out or os.path.join(REPO_ROOT, config["slug"], "index.html")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "w", encoding="utf-8", newline="").write(html)

    ov = data["overall"]
    print("Wrote {}".format(out_path))
    print("  total_active={:,}  entire_share={}%  median_price={}{:,}  "
          "median_occ={}  median_rev={}{:,}".format(
              ov["total_active"], ov["entire_share"], config["currency_symbol"],
              ov["median_price"], ov["median_occ"], config["currency_symbol"],
              ov["median_rev"]))
    print("  areas={}  cohort_areas={}".format(len(data["subs"]), len(data["cohort"])))

    if args.update_sitemap:
        sm = os.path.join(REPO_ROOT, "sitemap.xml")
        print("  sitemap:", "added" if update_sitemap(sm, config["slug"]) else "already present")
    if args.update_hub:
        if args.city_count is None or args.country_count is None:
            sys.exit("--update-hub requires --city-count and --country-count")
        hub = os.path.join(REPO_ROOT, "index.html")
        print("  hub: rewrote {} count strings".format(
            update_hub_counts(hub, args.city_count, args.country_count)))


if __name__ == "__main__":
    main()
