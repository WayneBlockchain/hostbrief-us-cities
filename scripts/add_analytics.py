#!/usr/bin/env python3
"""
add_analytics.py -- inject (or re-token) the PostHog snippet in every published
page of this static site.

Stdlib only, to match generate_city.py. Do NOT add a package.json to this repo.

Why this exists instead of regenerating the city pages:
generate_city.py rebuilds a city page from an Inside Airbnb ``listings.csv.gz``
snapshot, and those snapshots are NOT committed (there is no data/ directory).
Regenerating all 33 cities to pick up a template change would mean re-downloading
every snapshot and recomputing every published median -- a lot of risk and network
for a change that adds an analytics tag and touches no data. So the snippet goes
into scripts/template.html (so future cities inherit it) AND is injected into the
already-published pages here.

The snippet is inserted immediately before </head>, wrapped in marker comments.
This script is idempotent: run it again to re-inject or to swap the token.

Redirect stubs (pages with a <meta http-equiv="refresh">) are skipped -- nobody
lingers on them and they would only add bounce noise.

Usage:
    python scripts/add_analytics.py                     # uses POSTHOG_TOKEN placeholder
    python scripts/add_analytics.py --token phc_xxxxx   # drop the real token in
    python scripts/add_analytics.py --check             # verify only, non-zero on gap
"""
import argparse
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PLACEHOLDER = "POSTHOG_TOKEN"
BEGIN = "<!-- PostHog analytics -->"
END = "<!-- end PostHog analytics -->"

# Snippet verbatim from posthog.com/docs/web-analytics/installation/html-snippet
# (fetched 2026-07-16). @@TOKEN@@ is substituted below.
SNIPPET = """<!-- PostHog analytics -->
<script>
  !function(t,e){var o,n,p,r;e.__SV||(window.posthog && window.posthog.__loaded)||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture register register_once register_for_session unregister unregister_for_session getFeatureFlag getFeatureFlagResult isFeatureEnabled reloadFeatureFlags updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures on onFeatureFlags onSessionId getSurveys getActiveMatchingSurveys renderSurvey canRenderSurvey getNextSurveyStep identify setPersonProperties group resetGroups setPersonPropertiesForFlags resetPersonPropertiesForFlags setGroupPropertiesForFlags resetGroupPropertiesForFlags reset get_distinct_id getGroups get_session_id get_session_replay_url alias set_config startSessionRecording stopSessionRecording sessionRecordingStarted captureException loadToolbar get_property getSessionProperty createPersonProfile opt_in_capturing opt_out_capturing has_opted_in_capturing has_opted_out_capturing clear_opt_in_out_capturing debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
  posthog.init('@@TOKEN@@', {
    api_host: 'https://us.i.posthog.com',
    defaults: '2026-05-30',
  })
</script>
<!-- end PostHog analytics -->
"""

BLOCK_RE = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\r?\n?", re.S)


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


def inject(text, token):
    """Insert or refresh the snippet just before </head>. Returns (text, action)."""
    eol = "\r\n" if "\r\n" in text else "\n"
    block = SNIPPET.replace("@@TOKEN@@", token).replace("\n", eol)

    if BLOCK_RE.search(text):
        return BLOCK_RE.sub(lambda _: block, text, count=1), "updated"

    if text.count("</head>") != 1:
        raise ValueError("expected exactly one </head>, found {}".format(
            text.count("</head>")))
    return text.replace("</head>", block + "</head>", 1), "added"


def main():
    ap = argparse.ArgumentParser(description="Inject the PostHog snippet into every page.")
    ap.add_argument("--token", default=PLACEHOLDER,
                    help="PostHog project token (default: the POSTHOG_TOKEN placeholder)")
    ap.add_argument("--check", action="store_true",
                    help="verify every published page carries the snippet; write nothing")
    args = ap.parse_args()

    done, skipped, missing = [], [], []
    for path in targets():
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        text, bom = read(path)

        if is_stub(text):
            skipped.append(rel)
            continue

        if args.check:
            (done if BLOCK_RE.search(text) else missing).append(rel)
            continue

        new, action = inject(text, args.token)
        if new != text:
            write(path, new, bom)
        done.append("{} ({})".format(rel, action))

    verb = "carry" if args.check else "processed"
    print("{} {} page(s):".format(len(done), verb))
    for rel in done:
        print("  ", rel)
    print("skipped {} redirect stub(s):".format(len(skipped)))
    for rel in skipped:
        print("  ", rel)
    if missing:
        print("MISSING the snippet on {} page(s):".format(len(missing)))
        for rel in missing:
            print("  ", rel)
        sys.exit(1)


if __name__ == "__main__":
    main()
