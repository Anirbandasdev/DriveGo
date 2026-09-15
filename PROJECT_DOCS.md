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
├── documents.html     # mock document upload (static; JS toggles Uploaded state)
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
├── components.js      # reusable Navbar/Footer vanilla partials (single source of truth)
└── PROJECT_DOCS.md (this file)
```

> Structure rule: **pages = HTML, styling = CSS, demo data = JS.**
> `site.js` never builds page layouts — each `.html` file holds its own
> forms and content; JS only fills marked placeholders
> (`#popularCars`, `#carsGrid`, `#sumTotal`, …) from `data.js`.
>
> Shared chrome (navbar/footer) lives once in `components.js` and mounts
> into `<div id="siteNav"></div>` / `<div id="siteFoot"></div>` slots.
> Pages declare `<body data-nav="cars" data-footer="dark">`.
> No React/Vue/Angular — HTML + CSS + vanilla JS only.
>
> Django-template mapping (Phase 2): each page becomes
> `templates/<page>.html` extending `base.html`; the slots become
> `{% include "partials/navbar.html" %}` / `{% include "partials/footer.html" %}`;
> `components.js:Navbar` → `partials/navbar.html`,
> `components.js:Footer` → `partials/footer.html` (dark + light variants).

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

---

## 3. PHASE 2 — Django backend (IMPLEMENTED)

Same pages, same design — now Django-rendered with a real database.

```
F:\car\
├── manage.py
├── drivego/            # settings (env-based), urls, wsgi, asgi
├── rental/             # models, views, forms, urls, admin, utils, tests
│   ├── management/commands/promote_admin.py
│   └── migrations/
├── templates/          # base.html, partials/, 14 pages (converted 1:1 from the prototype)
├── static/css/styles.css + static/js/site.js   # same design, JS trimmed to UI-only
├── requirements.txt / .env.example / .gitignore
└── PROJECT_DOCS.md (this file)
```

### Setup

```bash
pip install -r requirements.txt
copy .env.example .env        # fill in keys when available
python manage.py migrate
python manage.py runserver
```

Sign-in requires Clerk keys in `.env` (`CLERK_PUBLISHABLE_KEY` +
`CLERK_SECRET_KEY`); there is no demo login fallback. To grant admin access,
run `python manage.py promote_admin <clerk-user-id-or-email>`.
Without Razorpay keys the payment page runs in clearly-labeled mock mode and
still verifies server-side; add keys in `.env` for the live Razorpay flow.
Without SMTP settings, emails print to the console. `DATABASE_URL` switches
SQLite to Supabase PostgreSQL; Supabase Storage is used for documents when
`SUPABASE_URL` + `SUPABASE_KEY` are set, otherwise files stay in `media/`.

### Verified working

- 18 automated tests pass (`python manage.py test rental`): overlap accept /
  reject / non-overlap, maintenance blocked, server pricing (7500 + 300 + 18%
  = 9204), booking-ID format, location filter, per-user booking isolation,
  admin gating, navbar avatar/admin-link visibility.
- Full flow tested end-to-end: dates → docs → home delivery → mock payment
  → CONFIRMED/PAID → confirmation + email.
- Overlap booking via the real form is rejected; concurrent creation is
  guarded by `select_for_update` + re-check inside the payment transaction.
- Flow pages require the booking owner's session; Django admin covers all
  models with search/filters.

### Auth model (Clerk + roles)

- Navbar: anonymous visitors see Login; signed-in customers see their avatar
  (Clerk photo or initial) linking to profile; the Admin link + `/dashboard/`
  are visible and accessible to ADMIN role only — normal users get redirected.
- Roles: `Customer.role` (`CUSTOMER`/`ADMIN`). When Supabase is configured,
  the `user_roles` table is the source of truth (see `supabase_roles.sql` —
  run it once in the Supabase SQL editor; RLS on, service key only).
  Otherwise the local `Customer.role` applies.
- Login verifies the Clerk user ID against Clerk's API, stores the role in
  the session, and admins land on `/dashboard/`. Logout clears Django's
  session and signs out of Clerk in the browser.
- Make an admin: user signs in once, then
  `python manage.py promote_admin <clerk-user-id-or-email>`.
- Demo mode: admins can toggle simulated payments from the dashboard
  ("Demo Mode" panel, stored in the `settings` table). ON = Pay button
  completes bookings without Razorpay; OFF = real Razorpay flow.

### Database: 7 tables, no framework clutter

`locations`, `cars`, `customers`, `bookings`, `documents` — plus only
`django_session` (Clerk logins live here) and `django_migrations` (required).
Django's auth/admin apps were removed entirely because login, roles, and the
dashboard are all Clerk-based; the old `auth_*` tables were dropped.

---

## 4. Deploying to Vercel

Vercel detects this project as Django (a `manage.py` at the repo root) and
serves it as a single serverless function with **zero-configuration support**.
`vercel.json` only adds a build step that runs migrations and `collectstatic`
(static files are then served from the Vercel CDN).

### Environment variables to set in the Vercel project

| Variable | Required | Notes |
|----------|----------|-------|
| `SECRET_KEY` | Yes | Long random string |
| `ALLOWED_HOSTS` | No | Defaults already include `.vercel.app`; add your custom domain, e.g. `drivego.com,.drivego.com` |
| `DEBUG` | No | Set to `0` (defaults to `0` when `VERCEL=1`) |
| `DATABASE_URL` | Yes | Supabase/Neon PostgreSQL URL. **SQLite does not persist on Vercel** — set this or deploys will use an empty sandbox DB |
| `SUPABASE_URL` / `SUPABASE_KEY` | If using storage | Documents upload to Supabase Storage; without these, files are saved to Vercel's ephemeral disk and lost between instances |
| `CLERK_PUBLISHABLE_KEY` / `CLERK_SECRET_KEY` | For sign-in | Without them the login/signup flow is disabled |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | For live payments | Without them the payment page runs in clearly-labelled mock mode |
| `CSRF_TRUSTED_ORIGINS` | If custom domain | e.g. `https://drivego.com,https://www.drivego.com` |

### Deploy steps

1. Push the repo to GitHub and import it in Vercel (Python/fluid detection is automatic).
2. Add the env vars above (set `DEBUG=0`). They are available during the build, which is what runs `migrate`.
3. Deploy — the build runs `migrate` + `collectstatic`, then your site is live.

Run locally with `python manage.py runserver` as before; the `SECURE_PROXY_SSL_HEADER`
and `CSRF_TRUSTED_ORIGINS` settings only take effect when the proxy headers are present.

