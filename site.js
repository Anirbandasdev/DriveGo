/* ============================================================
   DriveGo — site.js (SHARED HELPERS ONLY)
   Every page is plain static HTML + CSS.
   This file ONLY injects mock demo data from data.js
   (later: replace data.js reads with Django API fetches).
   Booking-flow state is kept in localStorage (demo only).
   ============================================================ */
const fmt = (n) => "₹" + Number(n).toLocaleString("en-IN");
const param = (k) => new URLSearchParams(location.search).get(k);
const store = {
  get(key, fb) { try { return JSON.parse(localStorage.getItem(key)) ?? fb; } catch { return fb; } },
  set(key, val) { localStorage.setItem(key, JSON.stringify(val)); },
};
const DEFAULT_SEARCH = { location: "Salt Lake", pickupDate: "2026-09-15", pickupTime: "10:00", dropDate: "2026-09-18", dropTime: "18:00" };
const getSearch = () => ({ ...DEFAULT_SEARCH, ...store.get("drivego_search", {}) });
const getCar = (id) => CARS.find((c) => c.id === Number(id || store.get("drivego_car", 101))) || CARS[0];

/* One reusable car card — reference-style, filled from data.js */
function carCardHTML(c) {
  const ok = c.status === "available";
  const badge = ok ? `<span class="car-badge ok">Available</span>` : `<span class="car-badge no">Booked</span>`;
  const note = ok ? "" : `<div class="booked-note"><svg class="ic" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg> Booked ${c.bookedSlots[0].from} – ${c.bookedSlots[0].to}<br>Free from ${c.nextFree}</div>`;
  return `
  <div class="car-card">
    <div class="car-photo"><img src="${c.image}" alt="${c.brand} ${c.model}" loading="lazy"/>${badge}</div>
    <div class="car-info">
      <h3>${c.brand} ${c.model}</h3>
      <ul class="car-specs">
        <li><svg class="ic" viewBox="0 0 24 24"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/></svg>${c.seats} Seats</li>
        <li><svg class="ic" viewBox="0 0 24 24"><path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/></svg>${c.transmission}</li>
        <li><svg class="ic" viewBox="0 0 24 24"><line x1="3" x2="15" y1="22" y2="22"/><line x1="4" x2="14" y1="9" y2="9"/><path d="M14 22V4a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v18"/><path d="M14 13h2a2 2 0 0 1 2 2v2a2 2 0 0 0 4 0V9.83a2 2 0 0 0-.59-1.42L18 5"/></svg>${c.fuelType}</li>
      </ul>
      ${note}
      <div class="car-price"><b>${fmt(c.pricePerDay)}</b><span class="muted"> /day</span></div>
      <a class="btn-view" href="car-details.html?id=${c.id}">View Details <svg class="ic" viewBox="0 0 24 24"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg></a>
    </div>
  </div>`;
}

/* Mobile nav toggle (present on every page) */
function bindNav() {
  const t = document.getElementById("navToggle"), n = document.getElementById("navLinks");
  if (t && n) t.addEventListener("click", () => n.classList.toggle("open"));
}

/* Broken-image safety net (e.g. offline presentation): swap any failed
   image for a branded placeholder so the layout never shows a broken icon */
const PLACEHOLDER = "data:image/svg+xml," + encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='450'>" +
  "<rect width='800' height='450' fill='#1A1D23'/>" +
  "<text x='400' y='215' font-family='Arial' font-size='44' font-weight='bold' fill='#ffffff' text-anchor='middle'>DriveGo.</text>" +
  "<text x='400' y='255' font-family='Arial' font-size='20' fill='#9CA3AF' text-anchor='middle'>Rent. Drive. Repeat.</text></svg>");
function bindImgFallback() {
  document.addEventListener("error", (e) => {
    const t = e.target;
    if (t && t.tagName === "IMG" && !t.dataset.fbk) { t.dataset.fbk = "1"; t.src = PLACEHOLDER; }
  }, true);
}

/* Search forms: plain HTML GET submit; JS only remembers the demo data */
function bindSearchForms() {
  document.querySelectorAll("form[data-search]").forEach((f) => {
    f.addEventListener("submit", () => {
      const fd = new FormData(f);
      store.set("drivego_search", {
        location: fd.get("location"), pickupDate: fd.get("pickupDate"),
        pickupTime: fd.get("pickupTime"), dropDate: fd.get("dropDate"), dropTime: fd.get("dropTime"),
      });
    });
  });
}

/* ---- Per-page data binding (one small init per page) ---- */
const Pages = {
  home() {
    document.getElementById("popularCars").innerHTML = CARS.slice(0, 4).map(carCardHTML).join("");
    document.getElementById("locGrid").innerHTML = LOCATIONS.map((l) => `
      <a class="loc-card" href="cars.html?location=${encodeURIComponent(l.name)}">
        <img src="${l.image}" alt="${l.name}" loading="lazy"/>
        <div class="loc-info"><b>${l.name}</b><span class="muted small"><svg class="ic" viewBox="0 0 24 24"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg> ${l.cars}+ Cars Available</span></div>
      </a>`).join("");
  },

  cars() {
    const s = { ...getSearch(), ...(param("location") ? { location: param("location") } : {}) };
    document.getElementById("carsTitle").textContent = `Cars available in ${s.location}`;
    document.getElementById("carsSub").textContent =
      `Pickup ${s.pickupDate} ${s.pickupTime} → Drop ${s.dropDate} ${s.dropTime} · Mock availability`;
    const f = { maxPrice: 3000, type: "All", seats: "Any", transmission: "Any", fuel: "Any", sort: "Recommended" };
    const render = () => {
      let list = CARS.filter((c) => c.location === s.location)
        .filter((c) => c.pricePerDay <= f.maxPrice && (f.type === "All" || c.type === f.type)
          && (f.seats === "Any" || c.seats >= Number(f.seats))
          && (f.transmission === "Any" || c.transmission === f.transmission)
          && (f.fuel === "Any" || c.fuelType === f.fuel));
      if (f.sort === "Low") list = [...list].sort((a, b) => a.pricePerDay - b.pricePerDay);
      if (f.sort === "High") list = [...list].sort((a, b) => b.pricePerDay - a.pricePerDay);
      document.getElementById("carsGrid").innerHTML =
        list.map(carCardHTML).join("") || `<p class="muted">No cars match these filters (demo).</p>`;
    };
    [["maxPrice", "fPrice", (el) => { f.maxPrice = Number(el.value); document.getElementById("priceVal").textContent = fmt(el.value); }],
     ["type", "fType", (el) => f.type = el.value], ["seats", "fSeats", (el) => f.seats = el.value],
     ["transmission", "fTrans", (el) => f.transmission = el.value], ["fuel", "fFuel", (el) => f.fuel = el.value],
     ["sort", "fSort", (el) => f.sort = el.value],
    ].forEach(([, id, apply]) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener(el.type === "range" ? "input" : "change", () => { apply(el); render(); });
    });
    render();
  },

  details() {
    const c = getCar(param("id"));
    store.set("drivego_car", c.id);
    const ok = c.status === "available";
    document.getElementById("carTitle").textContent = `${c.brand} ${c.model}`;
    document.getElementById("carSub").textContent = `${c.location} · ${c.seats} Seats · ${c.transmission} · ${c.fuelType}`;
    document.getElementById("carImg").src = c.image;
    document.getElementById("carImg").alt = `${c.brand} ${c.model}`;
    document.getElementById("carPrice").textContent = fmt(c.pricePerDay);
    document.getElementById("carBadge").innerHTML = ok ? `<span class="badge ok">Available</span>` : `<span class="badge no">Booked</span>`;
    document.getElementById("carFeatures").innerHTML =
      [...c.features, "AC", "5 Doors"].map((x) => `<span>${x}</span>`).join("");
    document.getElementById("carLocation").innerHTML =
      `<svg class="ic" viewBox="0 0 24 24"><path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/></svg> ${(LOCATIONS.find((l) => l.name === c.location) || {}).address || c.location}`;
    document.getElementById("availState").className = ok ? "badge ok" : "badge no";
    document.getElementById("availState").textContent = ok
      ? "✓ Available for your selected dates"
      : `Booked ${c.bookedSlots[0].from} – ${c.bookedSlots[0].to} · Try from ${c.nextFree}`;
    const btn = document.getElementById("continueBtn");
    if (!ok) {
      btn.href = "cars.html";
      btn.classList.remove("btn-primary"); btn.classList.add("btn-ghost");
      btn.textContent = "Not Available — See Alternatives";
    }
  },

  booking() {
    const c = getCar(), s = getSearch(), ok = c.status === "available";
    document.getElementById("bkCar").textContent = `${c.brand} ${c.model} · ${s.pickupDate} → ${s.dropDate}`;
    const box = document.getElementById("bkAvail");
    box.className = ok ? "badge ok" : "badge no";
    box.textContent = ok ? "✓ This car is available" :
      `This car is already booked during part of your selected period. Available from ${c.nextFree}`;
  },

  documents() {
    document.querySelectorAll("[data-upload]").forEach((box) => {
      box.addEventListener("click", () => box.classList.toggle("uploaded"));
    });
  },

  delivery() {
    const cards = document.querySelectorAll("[data-method]");
    const addr = document.getElementById("addrForm");
    const paint = (m) => {
      cards.forEach((el) => el.classList.toggle("sel", el.dataset.method === m));
      addr.style.display = m === "home" ? "block" : "none";
    };
    let m = store.get("drivego_delivery", "store");
    paint(m);
    cards.forEach((el) => el.addEventListener("click", () => { m = el.dataset.method; store.set("drivego_delivery", m); paint(m); }));
  },

  summary() {
    const c = getCar(), s = getSearch(), days = 3;
    const rent = c.pricePerDay * days, del = store.get("drivego_delivery", "store") === "home" ? 300 : 0;
    const tax = Math.round((rent + del) * 0.18), total = rent + del + tax;
    store.set("drivego_total", total);
    const set = (id, v) => document.getElementById(id).textContent = v;
    set("sumCar", `${c.brand} ${c.model} · ${fmt(c.pricePerDay)}/day`);
    set("sumPickup", `${s.location} Garage · 15 September, 10:00 AM`);
    set("sumDrop", `${s.location} Garage · 18 September, 6:00 PM`);
    set("sumDelivery", store.get("drivego_delivery", "store") === "home" ? "Home Delivery" : "Pickup from Store");
    set("sumRent", fmt(rent)); set("sumDel", fmt(del)); set("sumTax", fmt(tax)); set("sumTotal", fmt(total));
    document.getElementById("payLink").textContent = `Proceed to Payment · ${fmt(total)}`;
  },

  payment() {
    const t = store.get("drivego_total", 9204);
    document.getElementById("payTotal").textContent = fmt(t);
    document.getElementById("payBtn").textContent = `Pay ${fmt(t)}`;
    document.querySelectorAll("[data-pay]").forEach((el) => {
      el.addEventListener("click", () => {
        document.querySelectorAll("[data-pay]").forEach((x) => x.classList.remove("sel"));
        el.classList.add("sel");
      });
    });
    document.getElementById("payBtn").addEventListener("click", () => {
      document.getElementById("payMsg").textContent = "Processing mock payment…";
      setTimeout(() => location.href = "confirmation.html", 900);
    });
  },

  confirmation() {
    const c = getCar(), s = getSearch();
    document.getElementById("cfCar").textContent = `${c.brand} ${c.model}`;
    document.getElementById("cfPickup").textContent = `${s.location} · 15 Sep, 10:00 AM`;
    document.getElementById("cfDrop").textContent = `${s.location} · 18 Sep, 6:00 PM`;
    document.getElementById("cfDel").textContent = store.get("drivego_delivery", "store") === "home" ? "Home Delivery" : "Pickup from Store";
    document.getElementById("cfTotal").textContent = fmt(store.get("drivego_total", 9204));
  },

  bookings() {
    const tab = param("tab") || "upcoming";
    document.querySelectorAll("[data-tab]").forEach((el) => el.classList.toggle("on", el.dataset.tab === tab));
    const badge = (st) => st === "Confirmed" || st === "Upcoming" ? "ok" : st === "Completed" ? "info" : "no";
    document.getElementById("bkGrid").innerHTML = BOOKINGS.filter((b) => b.tab === tab).map((b) => `
      <div class="card"><img src="${b.carImg}" alt="${b.car}"/><div class="card-body">
        <span class="badge ${badge(b.status)}">${b.status}</span>
        <h3>${b.car}</h3>
        <p class="small muted">${b.id} · ${b.location}<br>Pickup: ${b.pickup} → Drop: ${b.drop}</p>
        <div class="price-row"><b>${fmt(b.amount)}</b><span class="small muted">Receipt (demo)</span></div>
      </div></div>`).join("");
  },

  admin() {
    const s = ADMIN.stats;
    const set = (id, v) => document.getElementById(id).textContent = v;
    set("stCars", s.totalCars); set("stAvail", s.availableCars); set("stBk", s.activeBookings); set("stRev", s.revenue);
    document.getElementById("recentRows").innerHTML =
      ADMIN.recent.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("");
    document.getElementById("availBox").innerHTML =
      Object.entries(ADMIN.availability).map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join("");
    document.getElementById("locBox").innerHTML =
      LOCATIONS.map((l) => `<div class="kv"><span>${l.name}</span><span class="small muted">${l.cars} cars · ${l.bookings} bookings</span></div>`).join("");
  },
};

document.addEventListener("DOMContentLoaded", () => {
  bindNav(); bindSearchForms(); bindImgFallback();
  const page = document.body.dataset.page;
  if (page && Pages[page]) Pages[page]();
});
