#!/usr/bin/env python3
"""
drive_folder.py - list a link-viewable Google Drive folder without any auth.

Drive has no public JSON API that works without a key, but the folder embed
view does, and it is plain HTML:

    https://drive.google.com/embeddedfolderview?id=<FOLDER_ID>#list

It returns one <div class="flip-entry" id="entry-<FILE_ID>"> per item with the
file's link, title and type icon. That is everything the site needs to turn a
Drive folder into a set of pages, and it keeps working for as long as the
folder is shared "Anyone with the link".

Two entry points, on purpose:

    entries(folder_id, name)    fetch, then cache to data/drive-<name>.json
    cached(name)                read the cache only, never touches the network

The sync scripts call entries(). build_portal.py calls cached(), so building
the portal chrome never depends on Drive being reachable and never makes an
HTTP request at import time. A folder item added since the last build shows up
on its own hub page immediately and in the portal nav on the next build.

Requires nothing outside the stdlib: the GitHub Action has no pip step.
"""

import html
import json
import logging
import os
import re
import urllib.request
from datetime import datetime

# ============================ CONFIG ==============================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

VIEW_URL   = "https://drive.google.com/embeddedfolderview?id=%s#list"
FOLDER_URL = "https://drive.google.com/drive/folders/%s"

FETCH_TIMEOUT = 45
UA = {"User-Agent": "duke-bme406-site/1.0"}

# href pattern -> kind. Read from the entry's own link, which is more reliable
# than the icon URL. Anything unmatched is "file".
KINDS = [
    (r"docs\.google\.com/document/",       "doc"),
    (r"docs\.google\.com/spreadsheets/",   "sheet"),
    (r"docs\.google\.com/presentation/",   "slides"),
    (r"docs\.google\.com/forms/",          "form"),
    (r"drive\.google\.com/drive/folders/", "folder"),
]

log = logging.getLogger("drive")


# ============================ PARSING =============================

def parse(page):
    """The embed view's HTML -> [{id, title, url, kind, mime, modified}]."""
    out = []
    for chunk in page.split('<div class="flip-entry" id="entry-')[1:]:
        fid = chunk[:chunk.find('"')]
        m_url   = re.search(r'<a href="([^"]+)"', chunk)
        m_title = re.search(r'<div class="flip-entry-title">(.*?)</div>', chunk, re.S)
        m_mime  = re.search(
            r'drive-thirdparty\.googleusercontent\.com/\d+/type/([^"?]+)', chunk)
        m_mod   = re.search(
            r'<div class="flip-entry-last-modified"><div>(.*?)</div>', chunk, re.S)
        if not (fid and m_title):
            continue
        url = html.unescape(m_url.group(1)) if m_url else FOLDER_URL % fid
        kind = "file"
        for pat, k in KINDS:
            if re.search(pat, url):
                kind = k
                break
        out.append({
            "id": fid,
            "title": html.unescape(re.sub(r"<[^>]+>", "", m_title.group(1))).strip(),
            "url": url,
            "kind": kind,
            "mime": html.unescape(m_mime.group(1)) if m_mime else "",
            "modified": html.unescape(m_mod.group(1)).strip() if m_mod else "",
        })
    return out


# =========================== FETCHING =============================

def path_for(name):
    return os.path.join(DATA_DIR, "drive-%s.json" % name)


def cached(name):
    """The last good listing, or [] if there has never been one. No network."""
    try:
        with open(path_for(name)) as fh:
            return json.load(fh)["value"]
    except Exception:
        return []


def entries(folder_id, name):
    """Fetch the folder, cache it, and return (entries, live).

    On any failure the cached listing is returned with live=False, so a Drive
    outage degrades to stale rather than deleting every page built from it.
    """
    try:
        req = urllib.request.Request(VIEW_URL % folder_id, headers=UA)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            page = r.read().decode("utf-8", "replace")
        found = parse(page)
        if not found:
            raise ValueError("folder listed 0 items (is it shared with anyone "
                             "who has the link?)")
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path_for(name), "w") as fh:
            json.dump({"fetched": datetime.now().isoformat(timespec="seconds"),
                       "folder": folder_id, "value": found}, fh, indent=1)
        log.debug("  drive   %s: %d items, cached", name, len(found))
        return found, True
    except Exception as e:
        log.warning("  drive   %s: fetch failed (%s)", name, e)
        old = cached(name)
        if old:
            log.warning("  drive   %s: using the cached listing (%d items)",
                        name, len(old))
        return old, False


if __name__ == "__main__":     # python3 scripts/drive_folder.py <folder-id>
    import sys
    logging.basicConfig(level=logging.DEBUG, format="[drive] %(message)s")
    fid = sys.argv[1] if len(sys.argv) > 1 else ""
    items, live = entries(fid, "adhoc")
    for e in items:
        print("%-8s %-46s %s" % (e["kind"], e["title"][:44], e["id"]))
