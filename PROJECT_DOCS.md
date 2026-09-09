# DriveGo — Online Car Rental & Booking (PHASE 1: Frontend Prototype)

> **Status: FRONTEND ONLY. No backend. All data is mocked.**
> Brand: **DriveGo** — *Rent. Drive. Repeat.*

## 1. What was built (Phase 1 — presentation ready)

Zero-dependency static prototype. Open `index.html` directly (or any static server). No install, no build.

```
F:\car\
├── index.html         # home (static HTML; popular cars + locations injected by site.js)
├── cars.html          # search results (static filters; cards injected by site.js)
├── car-details.html   # car detail (?id=; filled by site.js)
├── booking.html       # dates + mock availability step
├── documents.html     # mock document upload (static; JS toggles Uploaded ✓)
├── delivery.html      # store pickup vs home delivery (+₹300)
├── summary.html       # price math filled by site.js
├── payment.html       # mock payment (JS simulates success → confirmation.html)
├── confirmation.html  # booking success (lines filled by site.js)
├── my-bookings.html   # tabs are links (?tab=); cards injected by site.js
├── login.html         # static form → my-bookings.html
├── signup.html        # static form → my-bookings.html
├── admin.html         # static layout; stats/tables filled by site.js
├── styles.css         # full design system (responsive, premium minimal)
├── data.js            # mockData: cars / locations / bookings / admin  ← replace with Django APIs later
├── site.js            # ONLY demo-data binding (reads data.js, fills page placeholders)
└── PROJECT_DOCS.md (this file)
```

> Structure rule: **pages = HTML, styling = CSS, demo data = JS.**
> `site.js` never builds page layouts — each `.html` file holds its own
> navbar, footer, forms and content; JS only fills marked placeholders
> (`#popularCars`, `#carsGrid`, `#sumTotal`, …) from `data.js`.

### Pages (plain files / links — no router)

| # | Page | File | Notes |
|---|------|-------|-------|
| 1 | Home | `index.html` | Hero “Find the perfect car…”, search widget, 6 popular cars, Why Choose Us (4), How It Works 01–04, 5 locations, footer |
| 2 | Search results | `cars.html` | Heading “Cars available in {location}”, top search controls, filters (price/type/seats/transmission/fuel), sort (Recommended/Low/High), AVAILABLE / UNAVAILABLE badges, booked-period note + alternative period |
| 3 | Car details | `car-details.html?id=101` | Gallery, specs, ₹/day, booking widget (location+dates+times), availability state, Continue Booking (disabled if booked), features/policy/location |
| 4 | Booking + availability | `booking.html` | Progress `1 Car → 2 Dates → 3 Verification → 4 Delivery → 5 Payment`, selected car, rental period, mock ✓ available / booked + “Available from …” |
| 5 | Document verification | `documents.html` | License front/back + Govt ID mock upload boxes, JPG/PNG/PDF 5MB note, Uploaded ✓ state, privacy message |
| 6 | Pickup / Delivery | `delivery.html` | Store pickup card vs Home delivery card (+₹300), address/city/PIN/instructions |
| 7 | Summary | `summary.html` | Car, pickup, drop, delivery, rental + delivery + 18% tax, total (e.g. ₹9,204) |
| 8 | Payment (MOCK) | `payment.html` | UPI / Card / NetBanking selector, Pay button → 900ms fake processing → confirm |
| 9 | Confirmation | `confirmation.html` | ✓ Booking Confirmed, ID `DG-20260915-0012`, details, View Booking / Download Receipt (print), “email sent” simulated |
| 10 | My Bookings | `my-bookings.html` | Tabs Upcoming/Completed/Cancelled, cards with image/ID/dates/amount/status |
| 11 | Login | `login.html` | Email/password, remember, forgot, Google (mock → my-bookings) |
| 12 | Sign Up | `signup.html` | Name/email/phone/password/confirm + Google (mock) |
| 13 | Admin | `admin.html` | Sidebar (8 items), stats (cars/available/bookings/revenue), recent bookings table, availability, location overview |

### Demo flow (for tomorrow’s presentation)

```
Home → select Salt Lake + dates → Search Cars → select Innova Crysta
→ check ✓ Available → Continue Booking → mock upload 3 docs
→ choose Home Delivery → Summary (₹9,204) → Pay (mock)
→ Booking Confirmed → My Bookings
```

Also show: an UNAVAILABLE car (Kia Seltos — “Booked 12 Sep–15 Sep, free from 16 Sep”), filters/sort, admin dashboard, login/signup.

### Mock data shapes (kept backend-compatible on purpose)

```js
Car:     { id, brand, model, location, type, pricePerDay, seats, transmission, fuelType, status, image, features, bookedSlots[] }
Booking: { id, car, location, pickup, drop, amount, status, tab }
Location:{ id, name, address, city, cars, bookings, lat, lng }
```

Example locations: Kolkata, Salt Lake, New Town, Howrah, Kolkata Airport.
Example cars: Innova Crysta, Creta, Seltos, XUV700, Nexon, Brezza (mix of available/unavailable).

### Design tokens

White/off-white bg `#F7F8FA`, charcoal `#111827`, accent blue `#146EF5`, rounded cards (18px), subtle shadows, Manrope font, responsive (desktop-first; ≤1100px, ≤960px and ≤600px breakpoints).

---

## 2. PHASE 2 — Backend plan (DO NOT BUILD YET)

When frontend is approved, replace `data.js` exports with Django API calls. Suggested mapping:

```
Frontend (this prototype)      →   Django backend (Phase 2)
data.js CARS                   →   GET /api/cars/?location=&pickup=&drop=
data.js LOCATIONS              →   GET /api/locations/
data.js BOOKINGS               →   GET /api/bookings/ (auth) / POST /api/bookings/
Store.search + availability    →   GET /api/cars/:id/availability/?from=&to=
Docs mock upload               →   POST /api/kyc/documents/ (auth, file storage)
Delivery mock                  →   Booking.deliveryMethod + deliveryAddress fields
Payment mock                   →   POST /api/payments/create-order/ (Razorpay) + verify webhook
Login/signup mock              →   Clerk (frontend) → Django session/token verify
Email mock                     →   Django email service on booking confirm
Admin mock                     →   GET /api/admin/stats|bookings|cars|...
```

### Future models (reference only — do not create yet)

```python
# Car: id, brand, model, location(FK), pricePerDay, seats, transmission,
#      fuelType, status, image, features
# Booking: id, user, car(FK), pickupLocation, pickupDateTime, dropDateTime,
#      deliveryMethod, deliveryAddress, totalAmount, status, paymentStatus
# Location: id, name, address, city, latitude, longitude
```

Stack: Python + Django + Django ORM + PostgreSQL/Supabase + Clerk auth + Razorpay + email service + DRF if needed.

### Strict Phase-1 rules that were followed

Frontend only · no `models.py`/`views.py`/`serializers.py`/migrations/DB/APIs · no Clerk/Razorpay/email/real verification · mock data only · reused empty setup (no packages installed) · no extra features beyond spec.
