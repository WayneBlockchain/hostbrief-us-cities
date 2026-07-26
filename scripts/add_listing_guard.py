#!/usr/bin/env python3
"""
add_listing_guard.py -- fix the listing-URL field on every capture form: retitle
the placeholder and inject a client-side guard that rejects a non-listing URL
before it is submitted.

Stdlib only, to match generate_city.py / add_analytics.py. Do NOT add a
package.json to this repo.

Why this exists instead of regenerating the city pages:
same reason as add_analytics.py -- generate_city.py rebuilds a city page from an
Inside Airbnb ``listings.csv.gz`` snapshot, and those snapshots are NOT committed.
Regenerating every city to pick up a copy change would mean re-downloading every
snapshot and recomputing every published median. So the change goes into
scripts/template.html (so future cities inherit it) AND is injected into the
already-published pages here.

What prompted it:
a real lead submitted https://www.airbnb.com/hosting/listings/editor/... -- their
own hosting dashboard. That URL is not publicly reachable and not scrapeable, so
no benchmark can be built from it. The old placeholder ("Your Airbnb link") in
fact endorsed that answer: a host would reasonably call their dashboard tab
"your Airbnb link". Nothing client- or server-side checked the shape.

Two changes per page:
  1. placeholder -- names the artifact wanted (a public /rooms/ link)
  2. guard       -- blocks submit when the field is non-empty and not a listing URL

The guard is BRANCHED. A "/hosting/" URL gets the dashboard-specific message; any
other non-listing URL gets a neutral one. Telling someone they pasted their
dashboard when they pasted something else is a wrong error message on a lead
form, which costs a lead.

The field stays OPTIONAL: an empty value submits exactly as before. The regex is
the one the main app already trusts (api/_lib/validators/airbnb-url.ts).

Injected text is ASCII-only by design. City pages carry raw UTF-8 (a literal
U+2026) while the hand-built calculator pages use \\u2026 escapes; plain ASCII is
correct in both and cannot regress either.

The guard is wrapped in marker comments and this script is idempotent: a page
that already carries the markers is left alone rather than double-injected.

Redirect stubs (pages with a <meta http-equiv="refresh">) and pages with no
capture form (the hub index.html, home-service) are skipped.

Usage:
    python scripts/add_listing_guard.py           # apply
    python scripts/add_listing_guard.py --check   # verify only, non-zero on gap
"""
import argparse
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLACEHOLDER_OLD = 'placeholder="Your Airbnb link (optional)"'
PLACEHOLDER_NEW = 'placeholder="Your public listing link, airbnb.com/rooms/... (optional)"'

BEGIN = "/* listing-url guard */"
END = "/* end listing-url guard */"

# Injected immediately after e.preventDefault(), i.e. BEFORE the "Sending..."
# message is painted -- a rejected URL should never flash "Sending..." first.
# Indentation is applied per-file from the anchor line, since city pages indent
# the handler body 4 spaces and the hand-built pages 2.
GUARD_LINES = [
    BEGIN,
    "var lv=f.listing?f.listing.value.trim():'';",
    "if(lv){",
    " if(lv.toLowerCase().indexOf('/hosting/')!==-1){m.style.color='#ff8c66';"
    "m.textContent='That looks like your host dashboard. Open your listing the way "
    "a guest sees it and copy that link - it has /rooms/ in it.';return;}",
    r" if(!/^https?:\/\/(www\.)?airbnb\.([a-z]{2,3}\.[a-z]{2,3}|[a-z]{2,3})\/rooms\/\d+/i.test(lv))"
    "{m.style.color='#ff8c66';"
    "m.textContent='That link does not look like an Airbnb listing. It should look "
    "like airbnb.com/rooms/12345678 - or leave it blank and I will still send the "
    "city benchmark.';return;}",
    "}",
    END,
]

# e.preventDefault() appears exactly once in every form-bearing page (verified
# across all 38 + the template), so this anchor is unambiguous. Group 1 is the
# file's own handler-body indent; group 2 its newline flavour.
ANCHOR_RE = re.compile(r"^([ \t]*)e\.preventDefault\(\);[ \t]*(\r?\n)", re.M)

LISTING_INPUT = 'name="listing"'


def targets():
    """Every published page: the hub, each <slug>/index.html, and the template."""
    out = []
    for name in sorted(os.listdir(REPO_ROOT)):
        path = os.path.join(REPO_ROOT, name)
        if name == "index.html":
            out.append(path)
        elif os.path.isdir(path) and not name.startswith("."):
            idx = os.path.join(path, "index.html")
            if os.path.isfile(idx):
                out.append(idx)
    out.append(os.path.join(REPO_ROOT, "scripts", "template.html"))
    return out


def read(path):
    """Read text, preserving BOM presence and raw newlines."""
    raw = open(path, "rb").read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8") if bom else raw.decode("utf-8")
    return text, bom


def write(path, text, bom):
    data = text.encode("utf-8")
    open(path, "wb").write(b"\xef\xbb\xbf" + data if bom else data)


def is_stub(text):
    return 'http-equiv="refresh"' in text


def has_form(text):
    return LISTING_INPUT in text


def retitle(text):
    """Swap the placeholder. Returns (text, changed)."""
    if PLACEHOLDER_NEW in text:
        return text, False
    if PLACEHOLDER_OLD not in text:
        raise ValueError("listing input found but neither placeholder variant present")
    return text.replace(PLACEHOLDER_OLD, PLACEHOLDER_NEW, 1), True


def guard(text):
    """Insert the guard after e.preventDefault(). Returns (text, changed)."""
    if BEGIN in text:
        return text, False

    match = ANCHOR_RE.search(text)
    if match is None:
        raise ValueError("no e.preventDefault() anchor found")

    indent, eol = match.group(1), match.group(2)
    block = "".join(indent + line + eol for line in GUARD_LINES)
    return text[: match.end()] + block + text[match.end():], True


def main():
    ap = argparse.ArgumentParser(
        description="Add the listing-URL placeholder + guard to every capture form.")
    ap.add_argument("--check", action="store_true",
                    help="verify every capture form carries both changes; write nothing")
    args = ap.parse_args()

    changed, already, stubs, noform, missing = [], [], [], [], []

    for path in targets():
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        text, bom = read(path)

        if is_stub(text):
            stubs.append(rel)
            continue
        if not has_form(text):
            noform.append(rel)
            continue

        if args.check:
            gaps = []
            if PLACEHOLDER_NEW not in text:
                gaps.append("placeholder")
            if BEGIN not in text or END not in text:
                gaps.append("guard")
            (missing if gaps else already).append(
                "{} ({})".format(rel, ", ".join(gaps)) if gaps else rel)
            continue

        new, did_ph = retitle(text)
        new, did_gd = guard(new)

        actions = [n for n, d in (("placeholder", did_ph), ("guard", did_gd)) if d]
        if actions:
            write(path, new, bom)
            changed.append("{} ({})".format(rel, " + ".join(actions)))
        else:
            already.append(rel)

    if args.check:
        print("{} page(s) carry both changes".format(len(already)))
    else:
        print("changed {} page(s):".format(len(changed)))
        for rel in changed:
            print("  ", rel)
        print("already current, skipped {} page(s):".format(len(already)))
        for rel in already:
            print("  ", rel)

    print("skipped {} redirect stub(s)".format(len(stubs)))
    print("skipped {} page(s) with no capture form: {}".format(
        len(noform), ", ".join(noform) or "-"))

    if missing:
        print("INCOMPLETE on {} page(s):".format(len(missing)))
        for rel in missing:
            print("  ", rel)
        sys.exit(1)


if __name__ == "__main__":
    main()
