function bindNav() {
  var t = document.getElementById("navToggle"), n = document.getElementById("navLinks");
  if (t && n) t.addEventListener("click", function () { n.classList.toggle("open"); });
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
}

function initDelivery() {
  var cards = document.querySelectorAll("[data-method]");
  var addr = document.getElementById("addrForm");
  if (!cards.length || !addr) return;
  function paint() {
    var checked = document.querySelector('input[name="delivery_method"]:checked');
    var m = checked ? checked.value : "STORE_PICKUP";
    cards.forEach(function (el) {
      var active = el.dataset.method === (m === "HOME_DELIVERY" ? "home" : "store");
      el.classList.toggle("sel", active);
      var radio = el.querySelector('input[type="radio"]');
      if (radio) radio.checked = active;
    });
    addr.style.display = m === "HOME_DELIVERY" ? "block" : "none";
  }
  cards.forEach(function (el) {
    el.addEventListener("click", function () {
      var radio = el.querySelector('input[type="radio"]');
      if (radio) { radio.checked = true; }
      paint();
    });
  });
  paint();
}

function initPaymentMethods() {
  var opts = document.querySelectorAll("[data-pay]");
  opts.forEach(function (el) {
    el.addEventListener("click", function () {
      opts.forEach(function (x) { x.classList.remove("sel"); });
      el.classList.add("sel");
    });
  });
}

function bindAutoDropDate() {
  document.querySelectorAll('input[name="pickup_date"]').forEach(function (p) {
    var form = p.closest("form");
    var d = form ? form.querySelector('input[name="drop_date"]') : null;
    if (!d || p.dataset.autoDrop) return;
    p.dataset.autoDrop = "1";
    p.addEventListener("change", function () {
      if (!p.value) return;
      var dt = new Date(p.value + "T00:00:00");
      dt.setDate(dt.getDate() + 1);
      d.value = dt.toISOString().slice(0, 10);
    });
  });
}

document.addEventListener("DOMContentLoaded", function () {
  bindNav();
  bindImgFallback();
  bindAutoDropDate();
  var page = document.body.dataset.page;
  if (page === "delivery") initDelivery();
  if (page === "payment") initPaymentMethods();
});
