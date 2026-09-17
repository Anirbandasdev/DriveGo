function bindNav() {
  var nav = document.getElementById("siteNav");
  var toggle = document.getElementById("navToggle");
  var menu = document.getElementById("navMenu");
  var scrim = document.getElementById("navScrim");
  if (!nav || !toggle || !menu) return;
  function set(open) {
    nav.classList.toggle("is-open", open);
    menu.hidden = !open;
    if (scrim) scrim.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    document.body.classList.toggle("nav-locked", open);
  }
  toggle.addEventListener("click", function () { set(menu.hidden); });
  if (scrim) scrim.addEventListener("click", function () { set(false); });
  menu.addEventListener("click", function (e) { if (e.target.closest("a")) set(false); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !menu.hidden) { set(false); toggle.focus(); } });
  window.matchMedia("(min-width: 861px)").addEventListener("change", function (mq) { if (mq.matches) set(false); });
}

/* Cars page: auto-apply filters on desktop, bottom-sheet filters on phones. */
function initCarFilters() {
  var root = document.querySelector(".cl");
  var form = document.getElementById("filterForm");
  if (!root || !form) return;
  var sheet = window.matchMedia("(max-width: 900px)");
  var openBtn = document.querySelector("[data-filters-open]");
  function setOpen(open) {
    root.classList.toggle("filters-open", open);
    document.body.classList.toggle("nav-locked", open);
    if (openBtn) openBtn.setAttribute("aria-expanded", open ? "true" : "false");
    document.querySelectorAll(".cl-scrim").forEach(function (s) { s.hidden = !open; });
  }
  if (openBtn) openBtn.addEventListener("click", function () { setOpen(true); });
  document.querySelectorAll("[data-filters-close]").forEach(function (el) {
    el.addEventListener("click", function () { setOpen(false); });
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
  var price = document.getElementById("fPrice");
  var priceOut = document.getElementById("priceVal");
  if (price && priceOut) {
    price.addEventListener("input", function () { priceOut.textContent = rupees(price.value); });
  }
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () {
      /* In the phone sheet, wait for "Show cars" so people can set several filters. */
      if (sheet.matches && el.closest("#filterPanel")) return;
      form.submit();
    });
  });
}

/* Car page on phones: sticky price bar, hidden while the booking box is on screen. */
function initMobileBookBar() {
  var bar = document.querySelector("[data-mbar]");
  var box = document.getElementById("book");
  if (!bar || !box || !("IntersectionObserver" in window)) return;
  new IntersectionObserver(function (entries) {
    bar.classList.toggle("is-hidden", entries[0].isIntersecting);
  }, { threshold: 0.15 }).observe(box);
}

function bindDocModal() {
  var modal = document.getElementById("docModal");
  if (!modal) return;
  var body = document.getElementById("docModalBody");
  var title = document.getElementById("docModalTitle");
  var openLink = document.getElementById("docModalOpen");
  function close() { modal.hidden = true; body.innerHTML = ""; document.body.classList.remove("doc-modal-open"); }
  document.querySelectorAll("[data-doc-view]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var url = btn.getAttribute("data-doc-view") || "";
      var label = btn.getAttribute("data-doc-title") || "Document";
      title.textContent = label;
      body.innerHTML = "";
      if (!url) {
        openLink.removeAttribute("href");
        var msg = document.createElement("p");
        msg.className = "doc-modal-empty";
        msg.textContent = "No file is stored for this document yet.";
        body.appendChild(msg);
      } else {
        openLink.href = url;
        if (btn.getAttribute("data-doc-kind") === "pdf") {
          var frame = document.createElement("iframe");
          frame.src = url;
          frame.className = "doc-modal-frame";
          frame.setAttribute("title", label);
          body.appendChild(frame);
        } else {
          var img = document.createElement("img");
          img.src = url;
          img.alt = label;
          img.className = "doc-modal-img";
          img.dataset.fbk = "1";
          img.onerror = function () {
            var a = document.createElement("a");
            a.href = url;
            a.target = "_blank";
            a.rel = "noopener";
            a.className = "btn btn-primary";
            a.textContent = "Preview unavailable — open file";
            body.innerHTML = "";
            body.appendChild(a);
          };
          body.appendChild(img);
        }
      }
      modal.hidden = false;
      document.body.classList.add("doc-modal-open");
      var card = modal.querySelector(".doc-modal-card");
      if (card) card.focus();
    });
  });
  modal.querySelectorAll("[data-doc-close]").forEach(function (el) {
    el.addEventListener("click", function (e) { if (e.target === el || el.dataset.docClose !== undefined) close(); });
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !modal.hidden) close(); });
}

var PLACEHOLDER = "data:image/svg+xml," + encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='450'>" +
  "<rect width='800' height='450' fill='#1A1D23'/>" +
  "<text x='400' y='215' font-family='Arial' font-size='44' font-weight='bold' fill='#ffffff' text-anchor='middle'>DriveGo.</text>" +
  "<text x='400' y='255' font-family='Arial' font-size='20' fill='#9CA3AF' text-anchor='middle'>Rent. Drive. Repeat.</text></svg>");

function bindImgFallback() {
  document.addEventListener("error", function (e) {
    var t = e.target;
    if (t && t.tagName === "IMG" && !t.dataset.fbk) { t.dataset.fbk = "1"; t.src = PLACEHOLDER; }
  }, true);
  /* Empty src attributes never fire "error"; patch them up front. */
  document.querySelectorAll("img").forEach(function (img) {
    if (!img.getAttribute("src") && !img.dataset.fbk) { img.dataset.fbk = "1"; img.src = PLACEHOLDER; }
  });
}

function rupees(n) {
  return "₹" + Number(n || 0).toLocaleString("en-IN");
}

function camel(name) {
  return name.replace(/_(\w)/g, function (_, c) { return c.toUpperCase(); });
}

/* Live availability for the car page and the dates step. The server renders the
   initial state; this re-checks whenever the dates change, so a car that is booked
   for one window can still be booked for any other free window. */
function initAvailability() {
  var form = document.querySelector("[data-availability]");
  if (!form) return;
  var url = form.getAttribute("data-availability");
  var fields = ["pickup_date", "pickup_time", "drop_date", "drop_time"];
  var input = function (name) { return form.querySelector('[name="' + name + '"]'); };
  var status = form.querySelector("[data-av-status]");
  var message = form.querySelector("[data-av-message]");
  var detail = form.querySelector("[data-av-detail]");
  var suggest = form.querySelector("[data-av-suggest]");
  var suggestLabel = form.querySelector("[data-av-suggest-label]");
  var apply = form.querySelector("[data-av-apply]");
  var estimate = document.querySelector("[data-av-estimate]");
  var submit = form.querySelector("[data-av-submit]");
  var cta = form.querySelector("[data-av-cta]");
  var isCheckout = form.method.toLowerCase() === "post";
  var cal = document.querySelector("[data-calendar]");
  var picking = null;
  var timer = null;
  var seq = 0;

  document.querySelectorAll(".av-nojs").forEach(function (el) { el.hidden = true; });

  function values() {
    var out = {};
    fields.forEach(function (f) { out[f] = (input(f) || {}).value || ""; });
    return out;
  }

  function setState(reason) {
    status.className = "av-status is-" + reason;
  }

  function paintCalendar() {
    if (!cal) return;
    var v = values();
    cal.querySelectorAll(".cal-day").forEach(function (d) {
      var date = d.getAttribute("data-date");
      var inTrip = !picking && v.pickup_date && v.drop_date && date >= v.pickup_date && date <= v.drop_date;
      d.classList.toggle("in-range", !!inTrip);
      d.classList.toggle("is-edge", !picking && (date === v.pickup_date || date === v.drop_date));
      d.classList.toggle("is-anchor", picking === date);
    });
  }

  function render(data) {
    setState(data.reason);
    message.textContent = data.message;
    detail.textContent = data.clash ? "Booked " + data.clash.from_label + " → " + data.clash.to_label
      : data.available ? "You can book this car for the selected trip." : "";
    if (data.suggestion) {
      suggest.hidden = false;
      suggestLabel.textContent = data.suggestion.label;
      fields.forEach(function (f) { apply.dataset[camel(f)] = data.suggestion[f]; });
    } else {
      suggest.hidden = true;
    }
    if (data.price) {
      document.querySelectorAll("[data-price]").forEach(function (el) {
        var key = el.getAttribute("data-price");
        if (!(key in data.price)) return;
        el.textContent = key === "days" ? data.price[key] : rupees(data.price[key]);
      });
      if (estimate) estimate.hidden = false;
    } else if (estimate) {
      estimate.hidden = true;
    }
    document.querySelectorAll('[data-live="pickup_label"]').forEach(function (el) { el.textContent = data.pickup_label; });
    document.querySelectorAll('[data-live="drop_label"]').forEach(function (el) { el.textContent = data.drop_label; });
    if (submit) submit.disabled = !data.available;
    var mbar = document.querySelector("[data-mbar-status]");
    if (mbar) {
      mbar.className = data.available ? "is-ok" : "is-no";
      mbar.textContent = data.available ? "Free for your dates" : data.reason === "booked" ? "Booked — pick other dates" : data.message;
    }
    if (cta) {
      cta.textContent = data.available ? (isCheckout ? "Reserve & continue" : "Book for these dates")
        : data.reason === "booked" ? "Pick other dates to book" : "Unavailable";
    }
    paintCalendar();
  }

  function check() {
    var v = values();
    if (!v.pickup_date || !v.drop_date) return;
    var mine = ++seq;
    setState("checking");
    message.textContent = "Checking availability…";
    detail.textContent = "";
    if (submit) submit.disabled = true;
    var qs = new URLSearchParams(v).toString();
    fetch(url + "?" + qs, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (mine !== seq) return;
        if (!data.ok) { setState("invalid"); message.textContent = data.error || "Choose valid dates."; return; }
        render(data);
        if (!isCheckout && window.history.replaceState) {
          window.history.replaceState(null, "", window.location.pathname + "?" + qs + window.location.hash);
        }
      })
      .catch(function () {
        if (mine !== seq) return;
        setState("invalid");
        message.textContent = "Could not check availability. Check your connection and try again.";
      });
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(check, 250);
  }

  var pickupDate = input("pickup_date");
  var dropDate = input("drop_date");
  var lastPickup = pickupDate.value;
  pickupDate.addEventListener("change", function () {
    /* Moving the pickup day keeps the same trip length. */
    if (pickupDate.value && lastPickup && dropDate.value) {
      var days = Math.max(1, Math.round((new Date(dropDate.value) - new Date(lastPickup)) / 86400000));
      var dt = new Date(pickupDate.value + "T00:00:00Z");
      dt.setUTCDate(dt.getUTCDate() + days);
      dropDate.value = dt.toISOString().slice(0, 10);
    }
    lastPickup = pickupDate.value;
    dropDate.min = pickupDate.value;
    schedule();
  });
  ["pickup_time", "drop_date", "drop_time"].forEach(function (f) {
    input(f).addEventListener("change", schedule);
  });

  if (apply) {
    apply.addEventListener("click", function () {
      fields.forEach(function (f) {
        var value = apply.dataset[camel(f)];
        if (value) input(f).value = value;
      });
      lastPickup = pickupDate.value;
      check();
    });
  }

  /* Calendar: first click picks the pickup day, second click the return day. */
  if (cal) {
    cal.addEventListener("click", function (e) {
      var day = e.target.closest(".cal-day");
      if (!day || day.disabled) return;
      var date = day.getAttribute("data-date");
      if (!picking) {
        picking = date;
        paintCalendar();
        message.textContent = "Now pick your return day";
        detail.textContent = "";
        setState("checking");
        return;
      }
      if (date < picking) { pickupDate.value = date; dropDate.value = picking; }
      else { pickupDate.value = picking; dropDate.value = date; }
      lastPickup = pickupDate.value;
      picking = null;
      check();
    });
    paintCalendar();
  }
}

function initDelivery() {
  var form = document.querySelector("[data-delivery]");
  if (!form) return;
  var addr = form.querySelector("[data-address]");
  var price = document.querySelector(".trip-price[data-rental]");
  function set(key, text) {
    var el = document.querySelector('[data-price="' + key + '"]');
    if (el) el.textContent = text;
  }
  function paint() {
    var checked = form.querySelector('input[name="delivery_method"]:checked');
    var home = !!checked && checked.value === "HOME_DELIVERY";
    addr.hidden = !home;
    if (!price) return;
    /* Preview only; the review step shows the server-calculated amounts. */
    var rental = Number(price.dataset.rental);
    var fee = home ? Number(price.dataset.deliveryFee) : 0;
    var tax = Math.round((rental + fee) * Number(price.dataset.taxPercent) / 100);
    set("delivery_charge", fee ? rupees(fee) : "Free");
    set("tax_amount", rupees(tax));
    set("total_amount", rupees(rental + fee + tax));
  }
  form.querySelectorAll('input[name="delivery_method"]').forEach(function (r) { r.addEventListener("change", paint); });
  paint();
}

function initDropZones() {
  document.querySelectorAll("[data-drop]").forEach(function (zone) {
    var input = zone.querySelector('input[type="file"]');
    var title = zone.querySelector("[data-drop-title]");
    var hint = zone.querySelector("[data-drop-hint]");
    var original = [title.textContent, hint.textContent];
    function show() {
      var file = input.files && input.files[0];
      zone.classList.toggle("has-file", !!file);
      zone.classList.remove("is-invalid");
      if (!file) { title.textContent = original[0]; hint.textContent = original[1]; return; }
      title.textContent = file.name;
      var mb = file.size / (1024 * 1024);
      hint.textContent = (mb < 1 ? Math.max(1, Math.round(file.size / 1024)) + " KB" : mb.toFixed(1) + " MB") + " · ready to upload";
      if (mb > 5 || !/\.(jpe?g|png|pdf)$/i.test(file.name)) {
        zone.classList.add("is-invalid");
        hint.textContent = "Use a JPG, PNG or PDF under 5 MB";
      }
    }
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("is-drag"); });
    });
    ["dragleave", "drop"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.remove("is-drag"); });
    });
    zone.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
  });
}

function bindConfirms() {
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-confirm]");
    if (el && !window.confirm(el.getAttribute("data-confirm"))) { e.preventDefault(); e.stopPropagation(); }
  }, true);
}

/* Search forms: keep the trip length when the pickup date moves. */
function bindAutoDropDate() {
  document.querySelectorAll('input[name="pickup_date"]').forEach(function (p) {
    var form = p.closest("form");
    var d = form ? form.querySelector('input[name="drop_date"]') : null;
    if (!d || p.dataset.autoDrop) return;
    p.dataset.autoDrop = "1";
    var previous = p.value;
    p.addEventListener("change", function () {
      if (!p.value) return;
      var days = 1;
      if (previous && d.value && d.value > previous) {
        days = Math.max(1, Math.round((new Date(d.value) - new Date(previous)) / 86400000));
      }
      var dt = new Date(p.value + "T00:00:00Z");
      dt.setUTCDate(dt.getUTCDate() + days);
      d.value = dt.toISOString().slice(0, 10);
      d.min = p.value;
      previous = p.value;
    });
  });
}

function bindAdminNav() {
  var shell = document.querySelector(".adm");
  if (!shell) return;
  document.querySelectorAll("[data-adm-toggle]").forEach(function (el) {
    el.addEventListener("click", function () { shell.classList.toggle("nav-open"); });
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") shell.classList.remove("nav-open"); });
}

document.addEventListener("DOMContentLoaded", function () {
  bindNav();
  bindImgFallback();
  bindConfirms();
  var page = document.body.dataset.page;
  if (page === "details") initMobileBookBar();
  if (page === "cars") initCarFilters();
  if (page === "details" || page === "booking") initAvailability();
  else bindAutoDropDate();
  if (page === "delivery") initDelivery();
  if (page === "documents") initDropZones();
  if (page === "admin") { bindDocModal(); bindAdminNav(); }
});
