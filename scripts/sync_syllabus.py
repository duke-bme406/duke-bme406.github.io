#!/usr/bin/env python3
"""
sync_syllabus.py
Builds the BME 406 syllabus page (/syllabus/) and the lectures page
(/syllabus/lectures/) from the live syllabus Google Doc and the lectures
Drive folder. Same build as chorylab.com/bme590 (Chory Lab website,
scripts/sync_syllabus.py), adapted for this site.

  - Course links (Google Docs, Calendar, Forms) are KEPT. Email addresses are
    removed, since the site never lists email addresses.
  - Everything in the doc above the "Course Overview" heading is left off the
    page (class times, rooms, instructor and TA details).
  - The doc uses Heading 1 for its main sections and Heading 2/3 below that,
    so no level shift is needed (BME 590 used Heading 2/3).
  - Lectures: PDFs in a subfolder of the lectures folder whose name has the
    current year in it, e.g. "BME406 2026 Lectures".

The doc and the lectures folder must stay shared as "Anyone with the link can
view" or the export fails. On any doc fetch failure the existing page is left
untouched.

Run by .github/workflows/build.yml on every push, once a day, and on demand.
Run locally: python3 scripts/sync_syllabus.py
"""

import html as htmlmod
import json
import logging
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

sys.dont_write_bytecode = True                               # keep the tracked __pycache__ untouched
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_render as hb                                     # shared renderer (copy of the lab site handbook renderer)
import drive_folder as drive                                 # no-auth Drive folder listing

# ----------------------------------------------------------------------------
# CONFIG (edit these)
# ----------------------------------------------------------------------------
SYL_DOC_ID       = "16YwbUC_vxF-wXNH2CB64cXvJsggbE5OYP4J6Eio0oiw"   # live BME406 syllabus doc ("Syllabus BME406L Sp26")
SYL_DOC_HTML_URL = "https://docs.google.com/document/d/%s/export?format=html" % SYL_DOC_ID
SYL_DOC_VIEW_URL = "https://docs.google.com/document/d/%s/edit" % SYL_DOC_ID
SYL_DOC_PDF_URL  = "https://docs.google.com/document/d/%s/export?format=pdf" % SYL_DOC_ID
SYL_TITLE        = "BME 406L/706L: Biotechnology Design II"   # page title; the doc's title line sits above Course Overview and is not published
SYL_SHORT        = "BME 406"                                  # used in the bar and browser tab
SYL_ORDERING_URL   = "/ordering/"                             # syllabus bar button to the ordering app guide
SYL_ORDERING_LABEL = "Ordering app"
SYL_LECTURES_FOLDER_ID = "1-MFmKr6rdeIaAiH5Myf7i4JVuHXBv_-B"   # "BME406L Lectures": holds one subfolder per year
SYL_LECTURES_YEAR = None       # None = current year at build time; set e.g. 2026 to pin it
SYL_LECTURES_BUTTON  = "Lectures"   # syllabus bar button that opens the lectures page; only shown when there are lectures
SYL_LECTURES_URL     = "/syllabus/lectures/"   # lectures page: list on the left, the chosen PDF rendered on the right
SYL_PDFJS_URL        = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"          # renders PDFs in the page on every browser, phones included
SYL_PDFJS_WORKER_URL = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js"
SYL_PDF_DOWNLOAD_URL = "https://drive.google.com/uc?export=download&id=%s"   # each lecture PDF is copied onto the site at build time
SYL_PDF_TIMEOUT      = 90
SYL_START_HEADING = "Course Overview"   # everything in the doc above this heading is left off the page (class details, rooms, people)
SYL_LEVEL_SHIFT  = False       # this doc already uses H1 for main sections; True would read H2 as top level (BME 590 doc)

SYL_BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYL_TEMPLATE_HTML = os.path.join(SYL_BASE_DIR, "templates", "syllabus.template.html")
SYL_OUTPUT_HTML   = os.path.join(SYL_BASE_DIR, "syllabus", "index.html")
SYL_IMG_DIR       = os.path.join(SYL_BASE_DIR, "syllabus", "img")   # images from the doc, if any
SYL_IMG_WEB       = "/syllabus/img"
SYL_PDF_DIR       = os.path.join(SYL_BASE_DIR, "syllabus", "lectures")   # served at /syllabus/lectures/<name>.pdf
SYL_PDF_WEB       = "/syllabus/lectures"
SYL_LECTURES_TEMPLATE = os.path.join(SYL_BASE_DIR, "templates", "lectures.template.html")
SYL_LECTURES_HTML     = os.path.join(SYL_BASE_DIR, "syllabus", "lectures", "index.html")

log = logging.getLogger("syllabus")
logging.basicConfig(level=logging.DEBUG, format="[syllabus] %(levelname)s %(message)s")


def cut_before_start(doc):
    """Drop everything in the doc above the SYL_START_HEADING heading, so Emma
    can keep class details and locations at the top of the doc without them
    reaching the site. The heading itself is kept. The <style> block is kept
    because it carries the formatting classes.

    If the heading is missing, NOTHING from the doc body is published (rather
    than everything), so private details can never leak through a typo."""
    body_at = doc.find("<body")
    if body_at < 0:
        log.warning("no <body> in export; publishing no syllabus text")
        return doc[:0]
    body_open_end = doc.find(">", body_at) + 1
    want = re.sub(r"\s+", " ", SYL_START_HEADING).strip().lower()
    for m in re.finditer(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", doc[body_open_end:], re.DOTALL):
        if hb.plain(m.group(1)).lower().rstrip(":") == want:
            start = body_open_end + m.start()
            log.debug("starting at the '%s' heading; dropped %d characters above it",
                      SYL_START_HEADING, start - body_open_end)
            return doc[:body_open_end] + doc[start:]
    log.warning("no '%s' heading found in the doc; publishing no syllabus text "
                "(add a heading with exactly that text)", SYL_START_HEADING)
    return doc[:body_open_end] + "</body></html>"


def shift_levels(doc):
    """Read doc H1/H2 as top sections and H3 as subsections (H2 -> H1, H3 -> H2).
    Tags only; no text is changed."""
    if not SYL_LEVEL_SHIFT:
        return doc
    body_at = doc.find("<body")
    if body_at < 0:
        return doc
    head, body = doc[:body_at], doc[body_at:]
    body = re.sub(r"<(/?)h([23])\b", lambda m: "<%sh%d" % (m.group(1), int(m.group(2)) - 1), body)
    log.debug("shifted H2/H3 up one level")
    return head + body


def natural_key(s):
    """Sort "Lecture 2" before "Lecture 10"."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]


def copy_pdf(entry, name, used):
    """Download one lecture PDF from Drive into syllabus/lectures so it
    opens straight from this site. Returns the site path, or "" on failure."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or "lecture"
    base, i = slug, 2
    while slug in used:
        slug = "%s-%d" % (base, i); i += 1
    used.add(slug)
    try:
        req = urllib.request.Request(SYL_PDF_DOWNLOAD_URL % entry["id"], headers=drive.UA)
        with urllib.request.urlopen(req, timeout=SYL_PDF_TIMEOUT) as r:
            data = r.read()
        if not data.startswith(b"%PDF"):
            raise ValueError("not a PDF (is the file shared with anyone who has the link?)")
        with open(os.path.join(SYL_PDF_DIR, slug + ".pdf"), "wb") as fh:
            fh.write(data)
        log.debug("copied %s (%d bytes) -> %s/%s.pdf", name, len(data), SYL_PDF_WEB, slug)
        return "%s/%s.pdf" % (SYL_PDF_WEB, slug)
    except Exception as e:
        log.warning("could not copy %s (%s); left off the page", name, e)
        return ""


def collect_lectures():
    """This year's lecture PDFs, copied onto the site.

    Lists the subfolders of the lectures folder, keeps those whose name has the
    current year in it (e.g. "BME406 2026 Lectures"), copies every PDF inside
    onto the site, and returns [{slug, name, href, folder}] in lecture order.
    Returns [] until Emma adds this year's folder.
    """
    year = str(SYL_LECTURES_YEAR or datetime.now(timezone.utc).year)
    log.debug("looking for lecture folders with %s in the name", year)
    items, live = drive.entries(SYL_LECTURES_FOLDER_ID, "bme406-lectures")
    log.debug("lectures folder: %d items (live=%s)", len(items), live)
    folders = [e for e in items if e["kind"] == "folder"
               and re.search(r"(?<!\d)%s(?!\d)" % year, e["title"])]
    if not folders:
        log.debug("no %s lecture folder yet", year)
        return []
    folders.sort(key=lambda e: natural_key(e["title"]))
    os.makedirs(SYL_PDF_DIR, exist_ok=True)
    used, lectures = set(), []
    for f in folders:
        files, _ = drive.entries(f["id"], "bme406-lectures-%s" % f["id"])
        files = sorted(files, key=lambda e: natural_key(e["title"]))
        log.debug("folder %s: %d files", f["title"], len(files))
        for e in files:
            if not (e["title"].lower().endswith(".pdf") or "pdf" in e["mime"].lower()):
                log.debug("skipping non-PDF %s", e["title"])
                continue
            name = re.sub(r"\.pdf$", "", e["title"], flags=re.I)   # file extension only, not content
            href = copy_pdf(e, name, used)
            if href:
                lectures.append({"slug": href.rsplit("/", 1)[1][:-4], "name": name,
                                 "href": href, "folder": f["title"]})
    log.debug("lectures: %d PDFs on the site", len(lectures))
    return lectures


def write_lectures_page(lectures, title):
    """/syllabus/lectures/: the lecture list on the left, the chosen PDF drawn on
    the right by PDF.js, with previous/next. Each lecture has its own address
    (/syllabus/lectures/#<slug>) so a link can open one directly."""
    with open(SYL_LECTURES_TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    items = "\n".join(
        '        <li><a class="lec-item" href="%s" data-slug="%s">%s</a></li>'
        % (htmlmod.escape(l["href"]), htmlmod.escape(l["slug"]), htmlmod.escape(l["name"]))
        for l in lectures)
    options = "\n".join('        <option value="%s">%s</option>'
                        % (htmlmod.escape(l["slug"]), htmlmod.escape(l["name"])) for l in lectures)
    data = json.dumps([{"slug": l["slug"], "name": l["name"], "href": l["href"]} for l in lectures])
    out = (tpl.replace("{{TITLE}}", title)
              .replace("{{SYLLABUS_URL}}", "/syllabus/")
              .replace("{{SHORT}}", htmlmod.escape(SYL_SHORT))
              .replace("{{ORDERING_URL}}", SYL_ORDERING_URL)
              .replace("{{ORDERING_LABEL}}", htmlmod.escape(SYL_ORDERING_LABEL))
              .replace("{{ITEMS}}", items or '        <li class="lec-none">No lectures posted yet.</li>')
              .replace("{{OPTIONS}}", options)
              .replace("{{DATA}}", data.replace("</", "<\\/"))
              .replace("{{PDFJS}}", SYL_PDFJS_URL)
              .replace("{{PDFJS_WORKER}}", SYL_PDFJS_WORKER_URL))
    os.makedirs(os.path.dirname(SYL_LECTURES_HTML), exist_ok=True)
    with open(SYL_LECTURES_HTML, "w", encoding="utf-8") as f:
        f.write(out)
    log.debug("wrote %s (%d lectures)", SYL_LECTURES_HTML, len(lectures))


def main():
    log.debug("starting syllabus sync")
    hb.DOC_HTML_URL = SYL_DOC_HTML_URL                    # point the shared fetcher at the syllabus
    hb.IMG_DIR, hb.IMG_WEB = SYL_IMG_DIR, SYL_IMG_WEB     # never overwrite handbook images
    hb.STRIP_PATTERNS = []                                # keep course links; mailto/tel still dropped
    hb.GUIDE_SECTION = "\x00no-guide-section"             # handbook-only city-guide layout off

    doc = hb.fetch_doc()
    if not doc:
        log.warning("no document; leaving existing page untouched")
        return
    log.debug("fetched %d bytes", len(doc))

    title = htmlmod.escape(SYL_TITLE)                    # fixed title (the doc's title line is above Course Overview)
    doc = cut_before_start(doc)                          # hide everything above "Course Overview"
    doc = shift_levels(doc)

    with open(SYL_TEMPLATE_HTML, encoding="utf-8") as f:
        tpl = f.read()

    lectures = collect_lectures()
    write_lectures_page(lectures, title)
    lectures_button = ('<a href="%s">%s</a>' % (SYL_LECTURES_URL, htmlmod.escape(SYL_LECTURES_BUTTON))) if lectures else ""
    content, toc, n = hb.build(doc, strip_links=True)    # strip_links=True also removes email addresses
    out = (tpl.replace("{{CONTENT}}", content)
              .replace("{{TOC}}", toc)
              .replace("{{TITLE}}", title)
              .replace("{{DOC_VIEW}}", SYL_DOC_VIEW_URL)
              .replace("{{DOC_PDF}}", SYL_DOC_PDF_URL)
              .replace("{{SHORT}}", htmlmod.escape(SYL_SHORT))
              .replace("{{ORDERING_URL}}", SYL_ORDERING_URL)
              .replace("{{ORDERING_LABEL}}", htmlmod.escape(SYL_ORDERING_LABEL))
              .replace("{{LECTURES_BUTTON}}", lectures_button)
              .replace("{{REFRESH}}", hb.refresh_button()))       # empty unless doc_render.HB_REFRESH_ENDPOINT is set
    os.makedirs(os.path.dirname(SYL_OUTPUT_HTML), exist_ok=True)
    with open(SYL_OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(out)
    log.debug("wrote %s (%d TOC entries)", SYL_OUTPUT_HTML, n)


if __name__ == "__main__":
    main()
