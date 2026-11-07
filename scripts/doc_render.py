#!/usr/bin/env python3
"""
doc_render.py
Shared Google Doc renderer, copied from the Chory Lab website
(scripts/sync_handbook.py) so the BME 406 syllabus looks and behaves like
chorylab.com/bme590. sync_syllabus.py overrides the settings it needs; the
handbook defaults below are left as they were and are not used here.

Emma edits the doc; this renders it as a styled, navigable web page (sidebar
table of contents with scroll-spy, clean typography) instead of an embedded PDF.
It replicates the Apps Script publisher's privacy rule: any link whose real
destination matches a sensitive pattern (the lab site, Google Docs/Forms,
Google Calendar) is removed, keeping the visible text. Public/reference links
are kept.

Only the standard library is used, so it runs in the GitHub Action with no
extra install. On any fetch failure the existing page is left untouched.

Not run directly. Used by scripts/sync_syllabus.py.
"""

import base64
import html as htmlmod
import logging
import os
import re
import urllib.parse
import urllib.request

# ----------------------------------------------------------------------------
# CONFIG (edit these)
# ----------------------------------------------------------------------------
DOC_ID   = "1zD5hhdG9Zkxv3Rb6CxT0Mz02yixYwtMLRtivRY6RDJg"   # live handbook doc
PDF_ID   = "1Pzt1K2a9di_uSeYIEV7iEKbu4YO2zaor"              # published public PDF (fallback)
DOC_HTML_URL = "https://docs.google.com/document/d/%s/export?format=html" % DOC_ID
PDF_VIEW_URL = "https://drive.google.com/file/d/%s/view" % PDF_ID
PDF_DL_URL   = "https://drive.google.com/uc?export=download&id=%s" % PDF_ID
DOC_EDIT_URL = "https://docs.google.com/document/d/%s/edit" % DOC_ID   # internal page links here so lab members can comment

# "Update from handbook" button, internal page only. It posts to
# scripts/apps-script/HandbookRefreshEndpoint.gs, which asks GitHub to re-run
# build.yml now instead of waiting for the nightly run. Leave blank to hide it.
HB_REFRESH_ENDPOINT = ""   # blank = no "Get latest version" button on the BME 406 site
HB_REFRESH_LABEL    = "Get latest version"                  # button text

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_HTML = os.path.join(BASE_DIR, "templates", "handbook.template.html")
OUTPUT_HTML   = os.path.join(BASE_DIR, "public", "handbook", "index.html")
OUTPUT_HTML_INTERNAL = os.path.join(BASE_DIR, "public", "handbook-internal", "index.html")
IMG_DIR       = os.path.join(BASE_DIR, "public", "assets", "handbook")
IMG_WEB       = "/assets/handbook"
FETCH_TIMEOUT = 45

# Remove any link whose real URL contains one of these (same list as the Apps
# Script PDF publisher). Text is kept; the link is dropped.
STRIP_PATTERNS = ["chorylab.com", "docs.google.com", "forms.gle",
                  "calendar.google.com", "calendar.app.google"]

# Sidebar table of contents is built from these heading levels.
TOC_LEVELS = (1, 2)

# City-guide section. "Things to do in Durham" is ~80 bullets of the form
# "Name. One-line description.", which reads badly as one long stack of
# bullets. Inside this section only, lists render as a two-column
# name + description list. Matched against the doc H1, lowercased.
GUIDE_SECTION  = "things to do in durham"
GUIDE_META_MAX = 40                        # an aside like ", about 1.5 hours" stays on the name line
RUNIN_MAX      = 90                        # longest run-in label before a colon that still counts as a label
PLACE_BADGES   = ["MICHELIN recommended"]  # a leading phrase pulled out as a small tag

# Emma marks hyperlinks in the doc with a chain glyph. On the web the link is
# already styled as one, and on the public page (where private links are
# stripped) the glyph is left sitting in the prose pointing at nothing, so it
# is dropped. Set to False to keep it.
LINK_MARKER      = "\U0001f517"
DROP_LINK_MARKER = True

# Footnote anchors in the export are <a href="#ftnt1" id="ftnt_ref1">. The id
# has to survive cleaning or the jump lands nowhere in either direction.
KEEP_ID_PREFIX = "ftnt"

logging.basicConfig(level=logging.DEBUG, format="[render] %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def fetch_doc():
    try:
        log.debug("fetching handbook document")
        req = urllib.request.Request(DOC_HTML_URL, headers={"User-Agent": "duke-bme406-site/1.0"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        log.warning("fetch failed (%s)", e)
        return ""


def _rgb(hexv):
    hexv = hexv.lstrip("#")
    return int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16)


def color_bucket(hexv):
    """Collapse an arbitrary doc text colour to one consistent site bucket, so a
    very long document reads consistently instead of a dozen near-identical
    reds/blues. Returns a css class suffix or None (leave default)."""
    r, g, b = _rgb(hexv)
    mx, mn = max(r, g, b), min(r, g, b)
    if mx - mn < 26:                                   # greyscale
        avg = (r + g + b) / 3
        if avg < 60: return None                       # essentially black -> default
        if avg < 150: return "ink"
        return "muted"
    if r > 150 and r >= g + 30 and r >= b + 30: return "red"
    if b > 110 and b >= r and b >= g - 20:  return "blue"
    if g > 110 and g >= r - 20 and g >= b:  return "green"
    if r > 150 and g > 90 and b < 90:       return "amber"
    return None


def hl_bucket(hexv):
    """Collapse a highlight/background colour to a consistent pale bucket.
    Greyscale is tested first so a near-grey like #e8eaec is not mistaken for
    blue on its marginally-highest blue channel."""
    r, g, b = _rgb(hexv)
    if min(r, g, b) > 240: return None                 # white-ish -> no highlight
    if max(r, g, b) - min(r, g, b) < 24: return "grey" # low saturation -> grey
    if r > 220 and g > 200 and b < 225 and b < g: return "yellow"
    if b >= r and b >= g:                return "blue"
    if g >= r and g >= b:                return "green"
    return "grey"


def format_classes(doc):
    """Learn which export classes mean bold / italic / underline, and map colour
    and highlight classes to consistent site buckets."""
    m = re.search(r"<style[^>]*>(.*?)</style>", doc, re.DOTALL)
    css = m.group(1) if m else ""
    bold = set(re.findall(r"\.(c\d+)\s*\{[^}]*font-weight\s*:\s*700", css))
    ital = set(re.findall(r"\.(c\d+)\s*\{[^}]*font-style\s*:\s*italic", css))
    under = set(re.findall(r"\.(c\d+)\s*\{[^}]*text-decoration\s*:\s*underline", css))
    colorbucket, hlbucket = {}, {}
    for cm in re.finditer(r"\.(c\d+)\s*\{([^}]*)\}", css):
        name, bodyc = cm.group(1), cm.group(2)
        c = re.search(r"(?<!background-)color:\s*(#[0-9a-fA-F]{6})", bodyc)
        bg = re.search(r"background-color:\s*(#[0-9a-fA-F]{6})", bodyc)
        if c:
            bkt = color_bucket(c.group(1))
            if bkt: colorbucket[name] = "hb-c-" + bkt
        if bg:
            bkt = hl_bucket(bg.group(1))
            if bkt: hlbucket[name] = "hb-hl-" + bkt
    # Element rules matter too. Google's export styles headings on the element
    # ("h4 { color:#e06666 }"), not on the spans inside, so a run-in H4 label in
    # a bullet ("Open Environment and Dialogue:") carried no span colour at all
    # and rendered default ink beside identical siblings that happened to use an
    # explicitly coloured span. Read the element colours and apply them as the
    # inherited context.
    elem = {}
    # Walk every rule rather than anchoring on a preceding "}": the export
    # writes rules back to back, so an alternation that CONSUMES the brace
    # eats the separator the next rule needs and silently skips every other
    # one (h1 and h3 were found, h2 and h4 were not).
    for em in re.finditer(r"([^{}]*)\{([^}]*)\}", css):
        body_css = em.group(2)
        c = re.search(r"(?<!background-)color:\s*(#[0-9a-fA-F]{6})", body_css)
        if not c:
            continue
        bkt = color_bucket(c.group(1))
        if not bkt:
            continue
        for sel in em.group(1).split(","):
            sel = sel.strip()
            if re.fullmatch(r"h[1-6]", sel):
                elem[sel] = "hb-c-" + bkt

    # Nesting depth. The export does NOT nest <ul>s: a sub-list is a SIBLING
    # <ul> and the depth is carried by the item's margin-left (36pt per level)
    # and by the lst-kix_...-N class suffix. Without this every bullet rendered
    # at the same indent, so sub-points read as peers of their parent.
    indent = {}
    for im in re.finditer(r"\.(c\d+)\s*\{([^}]*)\}", css):
        mm = re.search(r"margin-left:\s*([\d.]+)pt", im.group(2))
        if mm:
            indent[im.group(1)] = float(mm.group(1))

    return {"bold": bold, "ital": ital, "under": under,
            "color": colorbucket, "hl": hlbucket,
            "elem": elem, "indent": indent}


# An address someone typed rather than linked is still an address. Docs only
# auto-links what the author linked, so "employeetravel@duke.edu" arrives as
# text and would otherwise be uncopyable on a phone.
EMAIL_RE = re.compile(r"(?<![\w.@+-])([\w.+-]+@[\w-]+(?:\.[\w-]+)+)(?![\w@-])")


def linkify_emails(fragment):
    """Wrap bare email addresses in mailto links, leaving existing anchors and
    tag attributes alone.

    Splitting on whole <a>...</a> blocks first is what keeps this safe: an
    address that is already a link, or that appears inside an href, must not be
    linked a second time.
    """
    if "@" not in fragment:
        return fragment
    parts = re.split(r"(<a\b[^>]*>.*?</a>)", fragment, flags=re.S)
    out = []
    for part in parts:
        if part.startswith("<a"):
            out.append(part)
            continue
        # only touch text nodes, never inside a tag
        bits = re.split(r"(<[^>]+>)", part)
        out.append("".join(
            b if b.startswith("<") else EMAIL_RE.sub(
                lambda m: '<a href="mailto:%s">%s</a>' % (m.group(1), m.group(1)), b)
            for b in bits))
    return "".join(out)


def redact_emails(fragment):
    """Take email addresses out of the text entirely, for the public build.

    Unlinking is not enough on its own: the address still sits on the page as
    text, and a scraper reads text perfectly well. The handbook names Duke
    staff, so the public copy should not carry their addresses at all.

    Only text nodes are scrubbed, never attributes, because a URL can contain
    something that looks like an address. The Duke calendar link in this very
    document has "...00007886demobedework@mysite.edu" inside its href.

    Tidying happens after the whole fragment is back together, and tolerates
    tags sitting between the punctuation, because the address and the brackets
    around it usually live in different spans: removing the address from
    "(<span>a@b.edu</span>)" leaves an empty span between two brackets, and a
    per-node cleanup cannot see that it has made "()".
    """
    if "@" not in fragment:
        return fragment

    parts = re.split(r"(<a\b[^>]*>.*?</a>)", fragment, flags=re.S)
    done = []
    for part in parts:
        if part.startswith("<a"):
            # href untouched; only what the reader sees is scrubbed
            done.append(re.sub(r"(>)([^<]*)(<)",
                               lambda m: m.group(1) + EMAIL_RE.sub("", m.group(2)) + m.group(3),
                               part))
            continue
        bits = re.split(r"(<[^>]+>)", part)
        done.append("".join(b if b.startswith("<") else EMAIL_RE.sub("", b) for b in bits))
    out = "".join(done)

    # elements that held nothing but an address, innermost first
    for _ in range(3):
        stripped = re.sub(r"<(span|a)\b[^>]*>\s*</\1>", "", out)
        if stripped == out:
            break
        out = stripped

    TAGS = r"(?:<[^>]+>\s*)*"
    out = re.sub(r"\(\s*" + TAGS + r"\)", "", out)          # emptied parentheses
    out = re.sub(r"&lt;\s*" + TAGS + r"&gt;", "", out)       # emptied angle brackets
    out = re.sub(r"([,;])\s*" + TAGS + r"\1", r"\1", out)    # doubled separators
    out = re.sub(r"\s+(</[^>]+>)\s*([,.;:])", r"\1\2", out)  # " </span>," -> "</span>,"
    out = re.sub(r"\s+([,.;:])", r"\1", out)                # space before punctuation
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out


def unwrap_link(href):
    if "google.com/url" in href:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("q")
        if q:
            return q[0]
    return href


def strip_link(url):
    u = (url or "").lower()
    # An address on a page anyone can load is a spam target, and the public
    # handbook is built precisely to drop the private destinations. Internal
    # pages call build(strip_links=False) and keep them.
    if u.startswith(("mailto:", "tel:")):
        return True
    return any(p in u for p in STRIP_PATTERNS)


def slugify(text, used):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "section"
    base, i = s, 2
    while s in used:
        s = "%s-%d" % (base, i); i += 1
    used.add(s)
    return s


def clean_inline(fragment, styles, strip_links=True, rich=True):
    """Clean one block's inline content. Spans become <span class="..."> carrying
    bold/italic/underline plus a consistent colour/highlight bucket. Links are
    kept unless `strip_links` and the destination is sensitive. When `rich` is
    False (headings), only bold/italic are kept, so heading colour stays uniform.
    """
    out = fragment

    # Google marks a bookmark with an empty, href-less <a id="id.xxxx"></a>.
    # fix_a below only rewrites anchors that HAVE an href, so the opening tag
    # survives untouched, while the closing tag is turned into a pop token and
    # swallowed by the stack walk. The anchor is then never closed and every
    # block after it inherits link styling. One of these ran stray underlines
    # down two thirds of the Chemical Hygiene Plan. They carry no content and
    # nothing links to them, so they go. Footnote targets are left alone: the
    # back-links point at them.
    out = re.sub(r'<a(?![^>]*href=)(?![^>]*id="%s)[^>]*>\s*</a>' % KEEP_ID_PREFIX,
                 "", out)

    def fix_a(m):
        href = unwrap_link(htmlmod.unescape(m.group(1)))
        # mailto: and tel: were being dropped along with the javascript: and
        # about: junk, so every "email Alice" link in the handbook rendered as
        # dead text with the address gone. They are as much a destination as an
        # http one, and on an internal page they are the whole point.
        keep = href.startswith(("#", "http", "mailto:", "tel:"))
        if keep and (not strip_links or not strip_link(href)):
            tgt = htmlmod.escape(href)
            # a new tab makes no sense for a mail client or a dialler
            ext = ("" if href.startswith(("#", "mailto:", "tel:"))
                   else ' target="_blank" rel="noopener"')
            aid = re.search(r'id="(%s[^"]*)"' % KEEP_ID_PREFIX, m.group(0))   # footnote anchors
            return '<a href="%s"%s%s>' % (tgt, ' id="%s"' % aid.group(1) if aid else "", ext)
        return "<a-stripped>"
    out = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>', fix_a, out)
    out = out.replace("</a>", "</a-close>")

    def fix_span(m):
        names = (re.findall(r'class="([^"]*)"', m.group(0)) or [""])[0].split()
        cls = []
        if any(n in styles["bold"] for n in names): cls.append("hb-b")
        if any(n in styles["ital"] for n in names): cls.append("hb-i")
        if rich:
            if any(n in styles["under"] for n in names): cls.append("hb-u")
            for n in names:
                if n in styles["color"]: cls.append(styles["color"][n]); break
            for n in names:
                if n in styles["hl"]: cls.append(styles["hl"][n]); break
        return '<span class="%s">' % " ".join(cls) if cls else "<span>"
    out = re.sub(r"<span[^>]*>", fix_span, out)

    tokens = re.split(r"(<[^>]+>)", out)
    stack, parts, drop_u = [], [], set()
    for t in tokens:
        if t.startswith("<span class=") or t.startswith("<a href="):
            stack.append((t, len(parts))); parts.append(t)
        elif t in ("<span>", "<a-stripped>"):
            if t == "<a-stripped>":
                # the text of a removed link keeps the doc's underline otherwise,
                # so it still reads as a link that does nothing
                for tag, idx in reversed(stack):
                    if tag.startswith("<span class="):
                        drop_u.add(idx); break
            stack.append((t, len(parts))); parts.append("")
        elif t in ("</span>", "</a-close>"):
            opened = stack.pop()[0] if stack else ""
            if opened.startswith("<span class="): parts.append("</span>")
            elif opened.startswith("<a href="): parts.append("</a>")
            else: parts.append("")
        else:
            parts.append(t)
    for idx in drop_u:
        parts[idx] = re.sub(r'class="([^"]*)"',
                            lambda c: 'class="%s"' % " ".join(x for x in c.group(1).split() if x != "hb-u"),
                            parts[idx])
    out = "".join(parts)

    out = re.sub(r"<br[^>]*>", "<br>", out)
    out = re.sub(r"(<img\b[^>]*?)/?>", r"\1>", out)
    # sup/sub are kept: footnote markers are superscript in the doc and should
    # stay superscript on the page. Their class is Google's own ("c73 c53 c32"),
    # which means nothing here and which Google renumbers between exports, so
    # every rebuild produced a diff on a page nobody had touched. Drop it.
    out = re.sub(r"<(sup|sub)\b[^>]*>", r"<\1>", out)
    out = re.sub(r"<(?!/?(?:span|a|br|sup|sub)\b|img\b)[^>]*>", "", out)   # drop other tags
    if DROP_LINK_MARKER:
        out = out.replace(LINK_MARKER, "").replace("&#128279;", "")
    out = re.sub(r'<span class="[^"]*">\s*</span>', "", out)       # empty styled spans
    out = re.sub(r"<span>\s*</span>", "", out)
    out = out.replace("&nbsp;", " ")
    out = re.sub(r"[ \t]+", " ", out)
    return out.strip()


def flatten_heading(frag):
    """Strip inline weight, slant and underline from a heading.

    The doc marks part of a heading bold and part not, e.g. "Independent" plain
    then "<b> Study in the Chory Lab</b>", which renders as two weights in one
    title. Worse, .hb-b also sets a colour, so the summary's hover rule only
    recoloured the unwrapped first word and the heading half-highlighted. The
    heading element already carries its own weight and colour, so drop the
    inline ones and let it render as one thing."""
    def fix(m):
        keep = [c for c in m.group(1).split() if c not in ("hb-b", "hb-i", "hb-u")]
        return '<span class="%s">' % " ".join(keep) if keep else "<span>"
    frag = re.sub(r'<span class="([^"]*)">', fix, frag)
    frag = re.sub(r"<span>(.*?)</span>", r"\1", frag, flags=re.DOTALL)
    return frag


def plain(fragment):
    t = re.sub(r"<[^>]+>", "", fragment or "")
    return re.sub(r"\s+", " ", htmlmod.unescape(t)).strip()


def save_images(doc):
    """Base64 images in the export are written to files and given web paths."""
    os.makedirs(IMG_DIR, exist_ok=True)
    mapping = {}
    for i, m in enumerate(re.finditer(r'src="data:image/(png|jpe?g|gif);base64,([^"]+)"', doc), 1):
        ext = "jpg" if m.group(1).startswith("jpe") else m.group(1)
        name = "handbook-%02d.%s" % (i, ext)
        try:
            with open(os.path.join(IMG_DIR, name), "wb") as f:
                f.write(base64.b64decode(m.group(2)))
            mapping[m.group(2)[:64]] = "%s/%s" % (IMG_WEB, name)
        except Exception as e:
            log.warning("image %d decode failed (%s)", i, e)
    log.debug("saved %d handbook images", len(mapping))
    return mapping


def is_runin(cleaned):
    """True when a paragraph starts with a coloured run-in label ending in a
    colon, e.g. <span class="hb-b hb-c-red">For each receipt:</span> ..."""
    m = re.match(r'\s*<span class="([^"]*)">(.*?)</span>', cleaned, re.DOTALL)
    if not m or "hb-c-" not in m.group(1):
        return False
    label = plain(m.group(2))
    rest = plain(cleaned[m.end():])
    return 0 < len(label) <= RUNIN_MAX and (label.endswith(":") or rest.startswith(":"))


def split_html(frag, n):
    """Split a fragment after its n-th visible character. Any inline tag open
    at the cut is closed on the left and reopened on the right, so a bullet
    like "Saturday. <a>Farmers market</a> ..." cannot be cut into unbalanced
    halves."""
    vis, i, stack = 0, 0, []
    while i < len(frag) and vis < n:
        if frag[i] == "<":
            j = frag.find(">", i)
            if j < 0:
                break
            tag, name = frag[i:j + 1], re.match(r"</?(\w+)", frag[i:j + 1])
            if name:
                if tag.startswith("</"):
                    if stack: stack.pop()
                elif not tag.endswith("/>") and name.group(1) not in ("br", "img"):
                    stack.append(tag)
            i = j + 1
            continue
        if frag[i] == "&":
            j = frag.find(";", i)
            i = j + 1 if 0 < j < i + 10 else i + 1
        else:
            i += 1
        vis += 1
    closers = "".join("</%s>" % re.match(r"<(\w+)", t).group(1) for t in reversed(stack))
    return frag[:i] + closers, "".join(stack) + frag[i:]


def lstrip_vis(frag, chars=". ,"):
    """Drop leading punctuation and spaces, keeping any tags they sit inside."""
    kept, i = [], 0
    while i < len(frag):
        if frag[i] == "<":
            j = frag.find(">", i)
            if j < 0:
                break
            kept.append(frag[i:j + 1]); i = j + 1
            continue
        if frag[i] in chars:
            i += 1
            continue
        break
    return "".join(kept) + frag[i:]


def balanced(frag):
    return (frag.count("<span") == frag.count("</span>")
            and frag.count("<a ") == frag.count("</a>"))


def split_place(inner):
    """Split one guide bullet into (name, aside, badge, description).

    The doc writes these as "<link>Name</link>. Description." or, where there
    is no link, "Label. Description." A short parenthetical or comma aside
    right after the name ("(Raleigh, about 35 minutes)") stays on the name
    line; anything longer is description."""
    s = inner.strip()
    aside = badge = ""
    m = re.match(r"^(?:<span[^>]*>\s*)*(<a\b[^>]*>)(.*?)</a>", s, re.DOTALL)
    if m and plain(m.group(2)):
        name = "%s%s</a>" % (m.group(1), m.group(2))
        rest = re.sub(r"^(?:\s*</span>)+", "", s[m.end():]).strip()
    else:
        dot = re.search(r"\.(\s|$)", plain(s))
        if not dot or dot.start() > 70:
            return ("", "", "", s) if len(plain(s)) > 45 else (s, "", "", "")
        left, right = split_html(s, dot.start())
        return left.strip(), "", "", lstrip_vis(right).strip()

    dot = re.search(r"\.(\s|$)", plain(rest))
    if dot and 0 < dot.start() < GUIDE_META_MAX and plain(rest)[:1] in "(,":
        left, right = split_html(rest, dot.start())
        tail = lstrip_vis(right).strip()
        if tail:                                  # only an aside if copy follows it
            aside, rest = lstrip_vis(left, " ,").strip(), tail
    desc = lstrip_vis(rest).strip()
    for b in PLACE_BADGES:                        # e.g. "MICHELIN recommended."
        if plain(desc).lower().startswith(b.lower()):
            badge, desc = b, lstrip_vis(split_html(desc, len(b))[1]).strip()
            break
    return name, aside, badge, desc


def build(doc, strip_links=True):
    styles = format_classes(doc)
    body = doc[doc.index("<body"):] if "<body" in doc else doc
    body = re.sub(r"<style.*?</style>", "", body, flags=re.DOTALL)
    imgmap = save_images(doc)
    for key, web in imgmap.items():
        body = re.sub(r'src="data:image/[^;]+;base64,' + re.escape(key) + r'[^"]*"',
                      'src="%s"' % web, body)

    # Unwrap the outline list scaffolding around REAL headings (h1-h3) so they
    # become top-level blocks. h4-h6 are left inside their bullet lists on
    # purpose: in this doc they are Heading-4 "run-in" labels, e.g.
    # "<li><h4><b>Holistic Recruitment:</b> we actively ...</h4></li>", where
    # only the label is bold. Treated as a heading, the whole line would render
    # bold; left as a list item, the label stays bold and the body stays normal.
    for _ in range(3):
        body = re.sub(r'<li[^>]*>\s*(<h[1-3]\b[^>]*>.*?</h[1-3]>)\s*</li>', r"\1", body, flags=re.DOTALL)
        body = re.sub(r'<(ol|ul)\b[^>]*>\s*(<h[1-3]\b[^>]*>.*?</h[1-3]>)\s*</\1>', r"\2", body, flags=re.DOTALL)

    used_ids, toc, htmlparts = set(), [], []

    # BASE_INDENT is where a top-level bullet sits; Google steps 36pt per level.
    BASE_INDENT, INDENT_STEP, MAX_LEVEL = 36.0, 36.0, 4

    def li_level(attrs):
        """Nesting depth of one item, from its margin-left, with the
        lst-kix_...-N class suffix as a fallback when no margin is set."""
        best = None
        for name in re.findall(r"c\d+", attrs):
            if name in styles["indent"]:
                ml = styles["indent"][name]
                best = ml if best is None else max(best, ml)
        # The doc does not always step 36pt per level. The travel section puts
        # its sub-bullets at 54pt, which is half a step, and round(0.5) in
        # Python is 0, so they rendered as peers of their parent. Round half up
        # and also trust the lst-kix_...-N suffix, which is the real nesting
        # level in Docs; whichever is deeper wins.
        by_margin = 0
        if best is not None:
            by_margin = int((best - BASE_INDENT) / INDENT_STEP + 0.5) if best > BASE_INDENT else 0
        km = re.search(r"lst-kix_[A-Za-z0-9]+-(\d+)", attrs)
        by_kix = int(km.group(1)) if km else 0
        log.debug("list item indent %s pt, kix level %s", best, by_kix)
        return max(0, min(MAX_LEVEL, max(by_margin, by_kix)))

    def render_list(chunk, list_level=0):
        tag = "ol" if chunk.lstrip().startswith("<ol") else "ul"
        open_attrs = (re.match(r"\s*<(?:ol|ul)\b([^>]*)>", chunk) or [None, ""])[1]
        items = []
        for lim in re.finditer(r"<li\b([^>]*)>(.*?)</li>", chunk, re.DOTALL):
            li_attrs, li = lim.group(1), lim.group(2)
            nested = ""
            m = re.search(r"<(ol|ul)\b.*?</\1>", li, re.DOTALL)
            if m:
                nested = render_list(m.group(0), list_level + 1)
                li = li[:m.start()] + li[m.end():]

            # A run-in heading inside the bullet: keep the element's colour,
            # which the spans inside it do not carry. Weight is deliberately
            # NOT applied, so the label stays bold and the sentence after it
            # does not, which is how the doc reads.
            def wrap_heading(hm):
                """A run-in H4 label inside a bullet. The element colour applies
                to the LABEL only, not the sentence after it. Colouring the
                whole line is what the doc literally says (h4 sets the colour
                and the body spans set none, so they inherit), but it turns a
                paragraph red and does not match the siblings whose label is an
                explicitly coloured bold span. Cut at the run-in colon."""
                cls = styles["elem"].get("h" + hm.group(1))
                body = clean_inline(hm.group(2), styles, strip_links)
                if not body:
                    return body
                if not cls:
                    return body
                flat = plain(body)
                idx = flat.find(":")
                if 0 < idx <= RUNIN_MAX:
                    label, rest = split_html(body, idx + 1)
                    return '<span class="hb-b %s">%s</span>%s' % (cls, label, rest)
                return '<span class="%s">%s</span>' % (cls, body)
            parts_li, pos = [], 0
            for hm in re.finditer(r"<h([1-6])\b[^>]*>(.*?)</h\1>", li, re.DOTALL):
                parts_li.append(clean_inline(li[pos:hm.start()], styles, strip_links))
                parts_li.append(wrap_heading(hm))
                pos = hm.end()
            parts_li.append(clean_inline(li[pos:], styles, strip_links))
            inner = "".join(x for x in parts_li if x)

            if inner or nested:
                lvl = max(li_level(li_attrs + " " + open_attrs), list_level)
                cls = ' class="hb-lvl-%d"' % lvl if lvl else ""
                items.append("      <li%s>%s%s</li>" % (cls, inner, nested))
        return "" if not items else "    <%s>\n%s\n    </%s>" % (tag, "\n".join(items), tag)

    def render_places(chunk):
        """The city-guide treatment: name, optional aside and tag, description."""
        items = []
        for li in re.findall(r"<li\b[^>]*>(.*?)</li>", chunk, re.DOTALL):
            li = re.sub(r"<(ol|ul)\b.*?</\1>", "", li, flags=re.DOTALL)
            inner = clean_inline(li, styles, strip_links)
            if not plain(inner):
                continue
            name, aside, badge, desc = split_place(inner)
            if not all(balanced(x) for x in (name, aside, desc)):
                log.warning("guide entry did not split cleanly, left whole: %s",
                            plain(inner)[:60])
                name, aside, badge, desc = "", "", "", inner
            # a reopened tag can be left empty by the split
            name, aside, desc = (re.sub(r"<span[^>]*>\s*</span>", "", x)
                                 for x in (name, aside, desc))
            bits = []
            if name:
                bits.append('<span class="hb-place-name">%s</span>' % name)
            if aside:
                bits.append('<span class="hb-place-aside">%s</span>' % aside)
            if badge:
                bits.append('<span class="hb-place-tag">%s</span>' % htmlmod.escape(badge))
            if desc:
                bits.append('<span class="hb-place-desc">%s</span>' % desc)
            # a description that starts lower-case is a continuation of the
            # name ("Durham Geeks on Meetup for board games"), not a sentence
            # of its own, so it stays on the same line
            run = " run" if plain(desc)[:1].islower() else ""
            items.append('      <li class="hb-place%s">%s</li>' % (run, "".join(bits)))
        return "" if not items else '    <ul class="hb-places">\n%s\n    </ul>' % "\n".join(items)

    def render_table(chunk):
        """Doc table -> site table. Changed for the BME 406 site (Nov 2026):
        merged cells keep their colspan/rowspan, each paragraph in a cell
        stays on its own line, a column that is empty in every ordinary row
        is dropped (the schedule's first column only holds the week titles,
        which span the whole row), and a row that is one merged cell is
        styled as a section row."""
        grid = []
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", chunk, re.DOTALL):
            row = []
            for cm in re.finditer(r"<t([dh])\b([^>]*)>(.*?)</t\1>", tr, re.DOTALL):
                kind, attrs, inner = cm.group(1), cm.group(2), cm.group(3)
                span = int((re.search(r'colspan="(\d+)"', attrs) or [0, 1])[1])
                rspan = int((re.search(r'rowspan="(\d+)"', attrs) or [0, 1])[1])
                paras = re.findall(r"<p\b[^>]*>(.*?)</p>", inner, re.DOTALL) or [inner]
                lines = [clean_inline(x, styles, strip_links) for x in paras]
                lines = [x for x in lines if plain(x)]
                row.append({"k": kind, "span": span, "rspan": rspan, "html": "<br>".join(lines)})
            if row:
                grid.append(row)
        if not grid:
            return ""
        ncols = max(sum(c["span"] for c in r) for r in grid)
        plain_rows = [r for r in grid if len(r) == ncols and all(c["span"] == 1 and c["rspan"] == 1 for c in r)]
        drop = set()
        if plain_rows and not any(c["rspan"] > 1 for r in grid for c in r):
            drop = {i for i in range(ncols) if all(not plain(r[i]["html"]) for r in plain_rows)}
            if len(drop) >= ncols:
                drop = set()
        if drop:
            log.debug("table: dropping empty column(s) %s of %d", sorted(drop), ncols)
        rows = []
        for r in grid:
            if len(r) == 1 and r[0]["span"] == ncols:                  # one merged cell: a section row
                c = r[0]
                rows.append('      <tr class="hb-row-span"><t%s colspan="%d">%s</t%s></tr>'
                            % (c["k"], ncols - len(drop), c["html"], c["k"]))
                continue
            cells, col = [], 0
            for c in r:
                span = c["span"]
                if span == 1 and col in drop:
                    col += 1
                    continue
                span -= len([i for i in range(col, col + c["span"]) if i in drop]) if span > 1 else 0
                attrs = (' colspan="%d"' % span if span > 1 else "") + (' rowspan="%d"' % c["rspan"] if c["rspan"] > 1 else "")
                cells.append("<t%s%s>%s</t%s>" % (c["k"], attrs, c["html"], c["k"]))
                col += c["span"]
            rows.append("      <tr>%s</tr>" % "".join(cells))
        return '  <div class="hb-table"><table>\n%s\n  </table></div>' % "\n".join(rows)

    in_guide = False
    guide_nav_at, guide_subs, footnotes_started = None, [], [False]
    last_list_end = [-1]             # end of the previous list block, for merging
    notes = {}                       # ftnt id -> definition html, for the popups

    # walk top-level blocks in document order
    for m in re.finditer(r"<h([1-6])\b([^>]*)>(.*?)</h\1>"
                         r"|<table\b.*?</table>"
                         r"|<(ol|ul)\b.*?</\4>"
                         r"|<p\b[^>]*>(.*?)</p>"
                         r"|<img\b[^>]*>", body, re.DOTALL):
        block = m.group(0)
        if block.startswith("<h"):
            level = int(m.group(1)); attrs = m.group(2); inner = m.group(3)
            text = plain(inner)
            if not text:
                continue
            gid = (re.search(r'id="([^"]*)"', attrs) or [None, None])[1]
            hid = gid if gid else slugify(text, used_ids)
            used_ids.add(hid)
            if level == 1:
                in_guide = GUIDE_SECTION in text.lower()
            elif in_guide and level == 2:
                if guide_nav_at is None:
                    guide_nav_at = len(htmlparts)     # jump links go above the first subsection
                guide_subs.append((hid, text))
            out_level = min(level + 1, 6)             # doc h1 -> page h2 (page owns the h1)
            htmlparts.append('  <h%d id="%s">%s</h%d>'
                             % (out_level, htmlmod.escape(hid),
                                flatten_heading(clean_inline(inner, styles, strip_links, rich=False)),
                                out_level))
            if level in TOC_LEVELS:
                toc.append((level, hid, text))
        elif block.startswith("<table"):
            t = render_table(block)
            if t: htmlparts.append(t)
        elif block.startswith("<ol") or block.startswith("<ul"):
            l = render_places(block) if in_guide else render_list(block)
            if l:
                # The export writes a parent bullet and its sub-bullets as
                # separate sibling lists, so each one got its own bottom margin
                # and a parent sat a full gap away from its own sub-points.
                # Lists that touch in the doc (nothing but whitespace between
                # them) are merged into one list on the page.
                tag = "ol" if l.lstrip().startswith("<ol") else "ul"
                prev = htmlparts[-1] if htmlparts else ""
                touching = not body[last_list_end[0]:m.start()].strip()
                if (touching and l.startswith("    <%s>\n" % tag)
                        and prev.startswith("    <%s>\n" % tag)
                        and prev.endswith("</%s>" % tag)):
                    htmlparts[-1] = (prev[:-len("\n    </%s>" % tag)] + "\n"
                                     + l[len("    <%s>\n" % tag):])
                    log.debug("merged adjacent %s list", tag)
                else:
                    htmlparts.append(l)
            last_list_end[0] = m.end()
        elif block.startswith("<img"):
            src = (re.search(r'src="([^"]*)"', block) or [None, ""])[1]
            key = re.sub(r'^data:image/[^;]+;base64,', "", src)[:64]
            web = imgmap.get(key)
            if web:
                htmlparts.append('  <p class="hb-figure"><img src="%s" alt="" loading="lazy"></p>' % web)
        else:  # paragraph
            inner = m.group(5) or ""
            # skip the doc's own title and its auto-table-of-contents (anchor-only paragraphs)
            links = re.findall(r'href="([^"]*)"', inner)
            if links and all(h.startswith("#") for h in links) and not plain(re.sub(r"<a\b.*?</a>", "", inner, flags=re.S)):
                continue
            cleaned = clean_inline(inner, styles, strip_links)
            ptext = plain(cleaned)
            if ptext.lower() in ("chory lab handbook", "table of contents"):
                continue
            if cleaned:
                # footnote definitions land at the very end; label them once so
                # they are not read as more bibliography
                # Footnote definitions used to be listed at the foot of the
                # page. They are shown in a popup on the marker instead, so
                # harvest the text here and drop the block.
                fm = re.match(r'^\s*<a href="#ftnt_ref(\d+)"[^>]*>.*?</a>\s*(.*)$',
                              cleaned, re.DOTALL)
                if fm:
                    notes[fm.group(1)] = fm.group(2).strip()
                    footnotes_started[0] = True
                    continue
                # centre pull-quotes and their attribution line
                if ptext[:1] in "“”‘’\"'":
                    htmlparts.append('  <p class="hb-quote">%s</p>' % cleaned)
                elif re.match(r"^[\-–—]\s+\S", ptext):
                    htmlparts.append('  <p class="hb-quote-cite">%s</p>' % cleaned)
                elif is_runin(cleaned):
                    # A paragraph that opens with a coloured label such as
                    # "When you are finished:" is a step. Several of these in a
                    # row read as one block, so they get extra space above.
                    htmlparts.append('  <p class="hb-runin">%s</p>' % cleaned)
                else:
                    htmlparts.append("  <p>%s</p>" % cleaned)

    def footnote_popup(m):
        num, label = m.group(1), m.group(2)
        text = notes.get(num)
        if not text:
            return m.group(0)                       # no definition: leave as-is
        return ('<span class="hb-fn" tabindex="0" role="note" '
                'aria-label="Footnote %s"><sup class="hb-fn-mark">%s</sup>'
                '<span class="hb-fn-pop"><span class="hb-fn-num">%s</span>%s</span>'
                "</span>" % (htmlmod.escape(label), htmlmod.escape(label),
                             htmlmod.escape(label), text))

    # <sup><a href="#ftnt9" id="ftnt_ref9">[9]</a></sup> -> hover popup
    MARKER = re.compile(
        r'<sup>\s*<a href="#ftnt(\d+)"[^>]*>\[?([^<\]]+)\]?</a>\s*</sup>')
    htmlparts = [MARKER.sub(footnote_popup, part) for part in htmlparts]

    if guide_nav_at is not None and len(guide_subs) > 2:
        htmlparts.insert(guide_nav_at, '  <nav class="hb-guide-nav">%s</nav>' % "".join(
            '<a href="#%s">%s</a>' % (htmlmod.escape(h), htmlmod.escape(t)) for h, t in guide_subs))

    content = wrap_sections(htmlparts)
    # Internal pages make every address reachable; the public one carries none.
    content = redact_emails(content) if strip_links else linkify_emails(content)
    return content, render_toc(toc), len(toc)


def wrap_sections(parts):
    """Wrap each top section (page <h2>, i.e. a doc H1) and everything under it
    in a collapsible <details>, so long sections can be folded away. Anything
    before the first h2 renders outside a section."""
    pre, sections, cur = [], [], None
    for part in parts:
        if re.match(r"\s*<h2 id=", part):
            if cur is not None:
                sections.append(cur)
            cur = {"head": part.strip(), "body": []}
        elif cur is None:
            pre.append(part)
        else:
            cur["body"].append(part)
    if cur is not None:
        sections.append(cur)

    out = list(pre)
    for s in sections:
        m = re.search(r'<h2 id="([^"]*)">(.*)</h2>', s["head"], re.DOTALL)
        hid, title = (m.group(1), m.group(2)) if m else ("", s["head"])
        out.append(
            '  <details class="hb-sec" open>\n'
            '    <summary class="hb-sec-summary"><h2 id="%s">%s</h2></summary>\n'
            '    <div class="hb-sec-body">\n%s\n    </div>\n'
            '  </details>' % (hid, title, "\n".join(s["body"])))
    return "\n".join(out)


def render_toc(toc):
    """Nested, collapsible TOC: each top section (level 1) is a row with a caret
    that reveals its level-2 children."""
    tree = []
    for level, hid, text in toc:
        if level == 1:
            tree.append([hid, text, []])
        elif tree:
            tree[-1][2].append((hid, text))
    lines = []
    for hid, text, kids in tree:
        has = bool(kids)
        head = ('<button class="toc-caret" type="button" aria-label="Toggle subsections" aria-expanded="false"></button>'
                if has else '<span class="toc-caret-none"></span>')
        lines.append('          <li class="toc-sec%s">' % (" has-kids" if has else ""))
        lines.append('            <span class="toc-sec-head">%s<a class="toc-l1" href="#%s">%s</a></span>'
                     % (head, htmlmod.escape(hid), htmlmod.escape(text)))
        if has:
            lines.append('            <ul class="toc-sub">')
            for khid, ktext in kids:
                lines.append('              <li><a class="toc-l2" href="#%s">%s</a></li>'
                             % (htmlmod.escape(khid), htmlmod.escape(ktext)))
            lines.append('            </ul>')
        lines.append('          </li>')
    return "\n".join(lines)


def refresh_button():
    """The internal page's "Update from handbook" button, or nothing if the
    endpoint is not set yet. The public page never gets it."""
    if not HB_REFRESH_ENDPOINT:
        log.debug("HB_REFRESH_ENDPOINT blank; no refresh button")
        return ""
    return ('<button class="hb-refresh" id="hbRefresh" type="button" data-endpoint="%s">%s</button>'
            '<link rel="stylesheet" href="/assets/css/handbook-refresh.css">'
            '<script src="/assets/js/handbook-refresh.js" defer></script>'
            % (htmlmod.escape(HB_REFRESH_ENDPOINT), htmlmod.escape(HB_REFRESH_LABEL)))


