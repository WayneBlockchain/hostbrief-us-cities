#!/usr/bin/env python3
"""
add_sitemap_lastmod.py -- give every sitemap entry a <lastmod> sourced from the
page's own last commit date, and refresh those dates on later runs.

Stdlib only, to match generate_city.py / add_analytics.py / add_listing_guard.py.
Do NOT add a package.json to this repo.

Why this exists:
the sitemap was submitted 2026-07-13 and read once, on 2026-07-13. Every entry
carried <changefreq> and <priority> and nothing else. Google largely ignores
both of those; <lastmod> is the field it actually uses to decide whether a
re-crawl is worth the request. So when PR #15 shipped the listing-URL guard
across 39 pages, the sitemap said nothing had moved, because it had no way to
say otherwise.

Where the dates come from:
``git log -1 --format=%cs -- <file>``, per page. NOT a single build timestamp,
and NOT filesystem mtime -- mtime in a git checkout records when the file was
last written to disk (a clone, a branch switch), not when its content changed.
The hub root is the proof: its mtime matches every other page, but its last
real change was ten days earlier.

On the dates being nearly identical:
35 of 36 entries currently resolve to 2026-07-26 and the hub root to
2026-07-16. That is correct and must not be "spread out" to look more organic.
This repo's history is sweep commits -- add_analytics.py, add_listing_guard.py,
the Inside Airbnb rebaseline -- each of which genuinely does edit every page on
one day. Google discounts <lastmod> site-wide once it finds the values
inconsistent with real page changes, so a manufactured spread would forfeit the
exact signal this script exists to send.

Idempotent in the useful sense: an entry that already carries a <lastmod> has
its value refreshed from git rather than being skipped or double-inserted, so
re-running after a content change is the intended workflow. A run that finds
every date already correct writes nothing at all.

Redirect stubs (pages with a <meta http-equiv="refresh">) are skipped. None are
currently listed -- all 16 retired cities were removed from the sitemap -- but
the guard stays, because a stub is exactly the kind of entry that should never
carry a freshness signal.

Byte handling: the file is read and written as bytes. sitemap.xml carries a
UTF-8 BOM and CRLF endings, and both survive verbatim, so the diff is one line
per changed entry rather than a whole-file rewrite.

Usage:
    python scripts/add_sitemap_lastmod.py           # apply
    python scripts/add_sitemap_lastmod.py --check   # verify only, non-zero on drift
"""
import argparse
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITEMAP = os.path.join(REPO_ROOT, "sitemap.xml")
BASE = "https://benchmarks.hostbrief.app"

# Group 1 is everything up to and including </loc>, group 2 the loc value, and
# group 3 an existing <lastmod> element if the entry already has one. Anchoring
# on </loc> is what keeps the loc value itself untouchable: the script only ever
# rewrites the span immediately after it.
ENTRY_RE = re.compile(
    r"(<url>\s*<loc>(.*?)</loc>)(\s*<lastmod>.*?</lastmod>)?")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def read_bytes(path):
    """Read text, preserving BOM presence and raw newlines."""
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    return text, bom


def write_bytes(path, text, bom):
    data = text.encode("utf-8")
    open(path, "wb").write(b"\xef\xbb\xbf" + data if bom else data)


def page_for(loc):
    """Map a <loc> to the index.html that serves it. Returns a repo-relative path."""
    if not loc.startswith(BASE):
        raise ValueError("loc is not on {}: {}".format(BASE, loc))
    slug = loc[len(BASE):].strip("/")
    return "index.html" if not slug else "{}/index.html".format(slug)


def is_stub(path):
    """A retired page: redirects away and asks not to be indexed."""
    text, _ = read_bytes(os.path.join(REPO_ROOT, path))
    return 'http-equiv="refresh"' in text


def git_date(path):
    """Last commit date for one file, YYYY-MM-DD. Empty string if uncommitted.

    Merge commits are deliberately left in. This site deploys from main, so the
    date a change landed on main is the date the page actually changed for a
    crawler -- which is what <lastmod> is claiming.
    """
    out = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", path],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if out.returncode != 0:
        raise RuntimeError("git log failed for {}: {}".format(
            path, out.stderr.decode("utf-8", "replace").strip()))
    return out.stdout.decode("utf-8").strip()


def main():
    ap = argparse.ArgumentParser(
        description="Add or refresh <lastmod> on every sitemap entry, from git.")
    ap.add_argument("--check", action="store_true",
                    help="verify every entry carries its correct date; write nothing")
    args = ap.parse_args()

    text, bom = read_bytes(SITEMAP)

    added, refreshed, current, stubs, problems = [], [], [], [], []

    def replace(match):
        head, loc, existing = match.group(1), match.group(2), match.group(3)

        try:
            path = page_for(loc)
        except ValueError as exc:
            problems.append(str(exc))
            return match.group(0)

        if not os.path.isfile(os.path.join(REPO_ROOT, path)):
            problems.append("{} -> {} does not exist".format(loc, path))
            return match.group(0)

        if is_stub(path):
            stubs.append(loc)
            return match.group(0)

        date = git_date(path)
        if not DATE_RE.match(date):
            problems.append("{} -> no commit date for {}".format(loc, path))
            return match.group(0)

        was = None
        if existing:
            was = existing.strip()[len("<lastmod>"):-len("</lastmod>")]

        if was == date:
            current.append(loc)
        elif was is None:
            added.append("{} {}".format(date, loc))
        else:
            refreshed.append("{} {} -> {}".format(loc, was, date))

        return "{}<lastmod>{}</lastmod>".format(head, date)

    new = ENTRY_RE.sub(replace, text)
    changed = added + refreshed

    if args.check:
        print("{} entry/entries already carry the correct date".format(len(current)))
        if changed:
            print("DRIFT on {} entry/entries:".format(len(changed)))
            for line in changed:
                print("  ", line)
    else:
        if new != text:
            write_bytes(SITEMAP, new, bom)
        print("added <lastmod> to {} entry/entries:".format(len(added)))
        for line in added:
            print("  ", line)
        print("refreshed {} entry/entries:".format(len(refreshed)))
        for line in refreshed:
            print("  ", line)
        print("already current, left alone: {} entry/entries".format(len(current)))

    print("skipped {} redirect stub(s): {}".format(len(stubs), ", ".join(stubs) or "-"))

    if problems:
        print("PROBLEM on {} entry/entries:".format(len(problems)))
        for line in problems:
            print("  ", line)
        sys.exit(1)

    if args.check and changed:
        sys.exit(1)


if __name__ == "__main__":
    main()
