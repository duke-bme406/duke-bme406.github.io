/* lectures.js: BME 406 lectures page (/syllabus/lectures/). Copied from chorylab.com/bme590/lectures.
 * One slide at a time, sized to fit the window, drawn with PDF.js. The arrow
 * buttons, the left/right arrow keys and a swipe on phones step through the
 * slides; past the last slide they move on to the next lecture. The lecture
 * title sits in the dark bar. Each lecture has its own address:
 * /syllabus/lectures/#<slug>.
 */
(function () {
  var DEBUG = true;                                        // console lines at each step
  var SWIPE_PX = 50;                                       // finger travel that counts as a swipe
  function log(m) { if (DEBUG && window.console) console.log("[lectures] " + m); }

  var data = [];
  try { data = JSON.parse(document.getElementById("lecData").textContent || "[]"); } catch (e) { log("bad data " + e); }
  var list = document.getElementById("lecList"), pick = document.getElementById("lecPick");
  var barTitle = document.getElementById("lecBarTitle"), status = document.getElementById("lecStatus");
  var slide = document.getElementById("lecSlide"), stage = document.getElementById("lecStage");
  var dl = document.getElementById("lecDownload");
  var prev = document.getElementById("slidePrev"), next = document.getElementById("slideNext");
  var lib = window.pdfjsLib, tag = document.getElementById("pdfjsLib");
  if (lib && tag) lib.GlobalWorkerOptions.workerSrc = tag.getAttribute("data-worker");
  var cur = -1, pdf = null, pageNo = 1, token = 0, task = null;

  if (!data.length) {
    log("no lectures");
    [prev, next, dl].forEach(function (el) { if (el) el.style.display = "none"; });
    status.textContent = "No lectures posted yet.";
    return;
  }

  function indexOf(slug) {
    for (var i = 0; i < data.length; i++) if (data[i].slug === slug) return i;
    return -1;
  }

  function buttons() {
    var total = pdf ? pdf.numPages : 0;
    prev.disabled = !(pageNo > 1 || cur > 0);
    next.disabled = !(pageNo < total || cur < data.length - 1);
    prev.setAttribute("aria-label", pageNo > 1 ? "Previous slide" : "Previous lecture");
    next.setAttribute("aria-label", pageNo < total ? "Next slide" : "Next lecture");
    if (total) status.textContent = "Slide " + pageNo + " of " + total +
      (pageNo === total && cur < data.length - 1 ? "  ·  next arrow opens the next lecture" : "");
  }

  function openLecture(i, startAtEnd) {
    if (i < 0 || i >= data.length) return;
    cur = i;
    var l = data[i];
    log("open " + l.slug);
    barTitle.textContent = l.name;
    document.title = l.name + " | " + (document.body.getAttribute("data-short") || "Lectures");
    dl.href = l.href;
    pick.value = l.slug;
    Array.prototype.forEach.call(list.querySelectorAll("a.lec-item"), function (a) {
      a.classList.toggle("active", a.getAttribute("data-slug") === l.slug);
    });
    if (history.replaceState) history.replaceState(null, "", "#" + l.slug);
    var my = ++token;
    pdf = null; slide.innerHTML = ""; status.textContent = "Loading...";
    if (!lib) {                                            // PDF.js did not load: use the browser's own viewer
      log("pdf.js missing, using iframe");
      var f = document.createElement("iframe");
      f.className = "lec-frame"; f.src = l.href; f.title = l.name;
      slide.appendChild(f); status.textContent = "";
      return;
    }
    lib.getDocument(l.href).promise.then(function (doc) {
      if (my !== token) return;
      pdf = doc;
      log(l.slug + ": " + doc.numPages + " slides");
      draw(startAtEnd ? doc.numPages : 1);
    }).catch(function (e) {
      if (my !== token) return;
      log("load error " + e);
      status.innerHTML = "";
      status.appendChild(document.createTextNode("This lecture could not be shown here. "));
      var a = document.createElement("a"); a.href = l.href; a.textContent = "Open the PDF"; a.target = "_blank"; a.rel = "noopener";
      status.appendChild(a);
    });
  }

  function room() {                                        // the largest slide that fits without scrolling
    var top = stage.getBoundingClientRect().top + window.pageYOffset;
    var bar = document.querySelector(".lec-bar"), head = null;
    var fixedTop = (head ? head.offsetHeight : 0) + (bar ? bar.offsetHeight : 0);
    var h = window.innerHeight - Math.max(fixedTop, top - window.pageYOffset) - 70;   // counter line + margin
    return { w: slide.clientWidth, h: Math.max(h, 200) };
  }

  function draw(n) {
    if (!pdf) return;
    pageNo = Math.min(Math.max(n, 1), pdf.numPages);
    buttons();
    var my = token;
    pdf.getPage(pageNo).then(function (page) {
      if (my !== token) return;
      var r = room(), vp = page.getViewport({ scale: 1 });
      var scale = Math.min(r.w / vp.width, r.h / vp.height);
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var v = page.getViewport({ scale: scale * dpr });
      var c = document.createElement("canvas");
      c.width = Math.floor(v.width); c.height = Math.floor(v.height);
      c.style.width = Math.floor(vp.width * scale) + "px";
      c.setAttribute("aria-label", "Slide " + pageNo);
      if (task) { try { task.cancel(); } catch (e) {} }
      task = page.render({ canvasContext: c.getContext("2d"), viewport: v });
      return task.promise.then(function () {
        if (my !== token) return;
        slide.innerHTML = ""; slide.appendChild(c);          // swap only when drawn, so nothing flickers
        log("slide " + pageNo + " drawn at " + c.style.width);
      });
    }).catch(function (e) { if (e && e.name !== "RenderingCancelledException") log("draw error " + e); });
  }

  function step(d) {
    if (!pdf) return;
    if (d > 0) { if (pageNo < pdf.numPages) draw(pageNo + 1); else if (cur < data.length - 1) openLecture(cur + 1, false); }
    else       { if (pageNo > 1) draw(pageNo - 1); else if (cur > 0) openLecture(cur - 1, true); }
  }

  list.addEventListener("click", function (e) {
    var a = e.target.closest("a.lec-item");
    if (!a) return;
    e.preventDefault();
    openLecture(indexOf(a.getAttribute("data-slug")), false);
  });
  pick.addEventListener("change", function () { openLecture(indexOf(pick.value), false); });
  prev.addEventListener("click", function () { step(-1); });
  next.addEventListener("click", function () { step(1); });
  document.addEventListener("keydown", function (e) {
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); step(-1); }
    if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); step(1); }
  });
  var x0 = null;
  slide.addEventListener("touchstart", function (e) { x0 = e.touches[0].clientX; }, { passive: true });
  slide.addEventListener("touchend", function (e) {
    if (x0 === null) return;
    var dx = e.changedTouches[0].clientX - x0; x0 = null;
    if (Math.abs(dx) > SWIPE_PX) step(dx < 0 ? 1 : -1);
  });
  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { log("resize, redraw"); draw(pageNo); }, 300);
  });
  window.addEventListener("hashchange", function () {
    var i = indexOf(location.hash.slice(1));
    if (i >= 0 && i !== cur) openLecture(i, false);
  });

  var start = indexOf(location.hash.slice(1));
  openLecture(start >= 0 ? start : 0, false);
})();
