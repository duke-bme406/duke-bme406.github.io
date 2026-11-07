/* syllabus.js: BME 406 syllabus page (/syllabus/).
 * Sidebar contents with scroll-spy, the phone contents drawer, opening a
 * collapsed section when jumping to a heading, and Collapse all. Copied from
 * the Chory Lab site (public/assets/js/site.js) so the page behaves like
 * chorylab.com/bme590. This site has no sticky site header, so only the
 * document bar counts toward the scroll offset.
 */
(function () {
  /* ---- Sticky bar stack ---- */
  /* A synced-document page can carry three sticky bars: the site header, the
     portal bar on internal pages, and the document bar. Their heights change
     with the breakpoint (76/66 and 54/48), so they are measured and published
     as custom properties rather than hardcoded. portal.css positions the
     document bar and the contents sidebar from them, and jumpTo() uses them as
     its scroll offset. On a page with fewer bars the sum is simply smaller,
     which is why nothing here is conditional on the page. */
  function stickyChrome() {
    var h = 0;
    [].forEach(function (sel) {
      var el = document.querySelector(sel);
      if (el) h += el.offsetHeight;
    });
    var bar = document.querySelector(".hb-bar");
    var full = h + (bar ? bar.offsetHeight : 0);
    var root = document.documentElement;
    root.style.setProperty("--chrome-top", h + "px");
    root.style.setProperty("--chrome-full", full + "px");
    return full;
  }

  /* ---- Handbook: scroll-spy the sidebar table of contents ---- */
  function initHandbookToc() {
    var toc = document.getElementById("hbToc");
    var content = document.getElementById("hbContent");
    if (!toc || !content) return;

    /* Phones: style.css turns the contents into a slide-in drawer. Give it a
       header with a Close button and a floating Contents button that is
       always within reach of a thumb. Both are hidden on wider screens. */
    var head = document.createElement("div");
    head.className = "hb-toc-head";
    head.innerHTML = '<span>Contents</span><button type="button" class="hb-toc-close">Close</button>';
    toc.insertBefore(head, toc.firstChild);
    var fab = document.createElement("button");
    fab.type = "button";
    fab.className = "hb-toc-fab";
    fab.textContent = "Contents";
    document.body.appendChild(fab);
    function setTocOpen(on) {
      document.body.classList.toggle("hb-toc-open", on);
      var t = document.querySelector(".hb-toc-toggle");
      if (t) t.setAttribute("aria-expanded", on ? "true" : "false");
      if (on) {                                     // start the list at where the reader is
        var cur = toc.querySelector("a.active");
        if (cur) toc.scrollTop = Math.max(0, cur.offsetTop - toc.clientHeight / 3);
      }
    }
    fab.addEventListener("click", function () { setTocOpen(true); });
    head.querySelector(".hb-toc-close").addEventListener("click", function () { setTocOpen(false); });
    document.addEventListener("click", function (e) {   // a tap on the dimmed page closes it
      if (!document.body.classList.contains("hb-toc-open")) return;
      if (e.target.closest("#hbToc, .hb-toc-toggle, .hb-toc-fab")) return;
      setTocOpen(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setTocOpen(false);
    });

    // caret click toggles a section's sub-list (does not navigate)
    toc.addEventListener("click", function (e) {
      var caret = e.target.closest(".toc-caret");
      if (caret) {
        var sec = caret.closest(".toc-sec");
        var open = sec.classList.toggle("open");
        caret.setAttribute("aria-expanded", open ? "true" : "false");
        e.preventDefault();
        return;
      }
      if (e.target.closest("a")) document.body.classList.remove("hb-toc-open");  // mobile
    });

    // open the containing <details> when jumping to a heading (TOC click or #hash)
    function openTarget(id) {
      var el = document.getElementById(id);
      if (!el) return;
      var d = el.closest("details.hb-sec");
      if (d && !d.open) d.open = true;
    }
    toc.addEventListener("click", function (e) {
      var a = e.target.closest("a[href^='#']");
      if (a) openTarget(decodeURIComponent(a.getAttribute("href").slice(1)));
    });
    /* Landing on a #hash: the browser scrolls to where the heading is BEFORE
       the collapsed <details> above it expand, so the heading ends up ~1300px
       below the viewport and the link looks broken. Open the section first,
       then scroll ourselves, offset to clear the two sticky bars. */
    function jumpTo(id) {
      openTarget(id);
      var el = document.getElementById(id);
      if (!el) return;
      /* One scroll is not enough. Opening the <details> reflows, images above
         the target settle late, and each shifts the target by hundreds of
         pixels after we have already scrolled. So correct the position a few
         times over the first second, and stop the moment the reader touches
         the page so we never yank it out from under them. */
      var cancelled = false;
      function stop() { cancelled = true; }
      ["wheel", "touchstart", "keydown", "mousedown"].forEach(function (ev) {
        window.addEventListener(ev, stop, { once: true, passive: true });
      });
      function place() {
        if (cancelled) return;
        var y = el.getBoundingClientRect().top + window.scrollY
                - (stickyChrome() + 14);
        /* "instant", not "auto". html { scroll-behavior: smooth } is set, and
           "auto" means "obey the CSS", so it animated this 23,000px jump over
           1.6 seconds: slow, and exactly the kind of long scroll this site
           avoids. Only "instant" overrides the CSS. */
        window.scrollTo({ top: Math.max(0, y), behavior: "instant" });
      }
      requestAnimationFrame(place);
      window.addEventListener("load", place, { once: true });
      [120, 400, 900].forEach(function (ms) { setTimeout(place, ms); });
    }
    if (location.hash.length > 1) jumpTo(decodeURIComponent(location.hash.slice(1)));
    window.addEventListener("hashchange", function () {
      if (location.hash.length > 1) jumpTo(decodeURIComponent(location.hash.slice(1)));
    });

    var links = {};
    Array.prototype.forEach.call(toc.querySelectorAll("a[href^='#']"), function (a) {
      links[decodeURIComponent(a.getAttribute("href").slice(1))] = a;
    });
    var heads = content.querySelectorAll("h2[id], h3[id]");
    if (!heads.length) return;
    var current = null;
    function setActive(id) {
      if (id === current) return;
      current = id;
      Array.prototype.forEach.call(toc.querySelectorAll("a.active"), function (a) { a.classList.remove("active"); });
      var a = links[id];
      if (!a) return;
      a.classList.add("active");
      var sec = a.closest(".toc-sec");
      if (sec && sec.classList.contains("has-kids")) {
        sec.classList.add("open");
        var c = sec.querySelector(".toc-caret");
        if (c) c.setAttribute("aria-expanded", "true");
      }
      // keep the active item visible WITHIN the sidebar only (never scroll page)
      var top = a.offsetTop, h = a.offsetHeight;
      if (top < toc.scrollTop || top + h > toc.scrollTop + toc.clientHeight) {
        toc.scrollTop = top - toc.clientHeight / 2;
      }
    }
    var io = new IntersectionObserver(function (entries) {
      var visible = entries.filter(function (e) { return e.isIntersecting; })
                           .sort(function (x, y) { return x.boundingClientRect.top - y.boundingClientRect.top; });
      if (visible.length) setActive(visible[0].target.id);
    }, { rootMargin: "-70px 0px -70% 0px", threshold: 0 });
    Array.prototype.forEach.call(heads, function (h) { io.observe(h); });
  }

  /* ---- Handbook: expand / collapse all sections ---- */
  function initHandbookSections() {
    var btn = document.getElementById("hbCollapseAll");
    var content = document.getElementById("hbContent");
    if (!btn || !content) return;
    btn.addEventListener("click", function () {
      var secs = content.querySelectorAll("details.hb-sec");
      var collapse = btn.getAttribute("aria-pressed") !== "true";
      Array.prototype.forEach.call(secs, function (d) { d.open = !collapse; });
      btn.setAttribute("aria-pressed", collapse ? "true" : "false");
      btn.textContent = collapse ? "Expand all" : "Collapse all";
    });
  }

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    stickyChrome();
    window.addEventListener("resize", stickyChrome);
    initHandbookToc();
    initHandbookSections();
  });
})();
