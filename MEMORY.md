# DriveGo — Codebase Overview (Memory)

> Brand: **DriveGo** — *Rent. Drive. Repeat.*
> Online car rental & booking. Django 5.2 backend + HTML/CSS/vanilla-JS frontend.
> Active DB: Supabase PostgreSQL (via `DATABASE_URL` in `.env`). `db.sqlite3` re-migrated 2026-09-14 (was stale/legacy).

## Stack & layout

```
F:\car\
├── manage.py               # entry point (DJANGO_SETTINGS_MODULE=drivego.settings)
├── drivego/                # project: settings.py (env-based), urls.py (→ rental.urls)
├── rental/                 # the only app
│   ├── models.py           # Location, Car, CarBlock, Customer, Booking, Document, SiteSetting
│   ├── views.py            # all view logic (client + admin controls)
│   ├── forms.py            # SearchForm, BookingDatesForm, DeliveryForm, DocumentUploadForm
│   ├── utils.py            # Clerk/Supabase/Razorpay/email integrations
│   ├── urls.py             # route map
│   ├── context_processors.py # frontend_config → CLERK_KEY, RAZORPAY_KEY_ID, current_customer
│   ├── admin.py            # (removed — no Django admin app installed)
|   ├── tests.py            # 45 tests (13 classes)
|   ├── management/commands/promote_admin.py
│   └── migrations/         # 0001 → 0005 (admin controls, CarBlock)
├── templates/              # base.html + 14 pages + partials/ (navbar, footer, car_card)
├── static/                 # css/styles.css (design system), js/site.js (UI-only JS)
├── media/documents/        # uploaded KYC files (local fallback when Supabase off)
├── requirements.txt        # Django, dj-database-url, razorpay, requests, PyJWT, python-dotenv, psycopg2-binary
├── .env / .env.example     # all config (secrets live in .env, gitignored)
├── PROJECT_DOCS.md         # full project doc (phases, setup, verified test list)
├── MEMORY.md               # this file
├── supabase_roles.sql      # user_roles table + RLS (run once in Supabase)
```

## Config (.env → drivego/settings.py)

- `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`
- `DATABASE_URL` → Supabase Postgres if set, else local SQLite
- `CLERK_PUBLISHABLE_KEY` / `CLERK_SECRET_KEY` → Clerk auth (required — no demo login fallback)
- `SUPABASE_URL` / `SUPABASE_KEY` → `user_roles` lookup + Storage upload for docs
- `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` → live payments; **absence ⇒ `RAZORPAY_MOCK=True`** (simulated)
- `EMAIL_HOST*` → SMTP; absent ⇒ console backend
- `TAX_RATE=0.18`, `DELIVERY_CHARGE=300`

## Data model (7 tables in prod DB)

- `locations` — pickup locations, `is_active`
- `cars` — brand/model/reg, category, seats, transmission, fuel, `price_per_day`, `image_url`, `features` (CSV), `status` (AVAILABLE/MAINTENANCE/INACTIVE)
  - `display_image`, `feature_list`, `blocking_bookings(pickup,drop)`, `is_available_for(...)`, `next_free_after(...)`
- `customers` — Clerk-backed users; `role` field (CUSTOMER/ADMIN)
- `bookings` — `booking_id` (DG-YYYYMMDD-NNNN), car/user/pickup_location FKs, datetimes, delivery fields, price breakdown (`rental/delivery/tax/total`), `status` (PENDING/PENDING_VERIFICATION/CONFIRMED/ACTIVE/COMPLETED/CANCELLED/REJECTED), `payment_status` (PENDING/PAID/FAILED/REFUNDED), Razorpay-order/payment ids, `admin_note` (shown to customer on confirmation), `calculate_price()/apply_price()`, classmethod `overlaps(...)`
- `documents` — KYC per booking (LICENSE_FRONT/BACK, GOVT_ID), `unique_together(booking, document_type)`, verification status (PENDING/VERIFIED/REJECTED) + `rejection_reason`, `view_url` property (Supabase public object URL or local `file.url`)
- `car_blocks` — CarBlock: admin manual hold (start/end/note); `Car.blocked_window()` feeds `is_available_for()` + `is_listed_available()`
- `settings` — SiteSetting key/value store (e.g. `demo_mode`)
- + `django_session`, `django_migrations` (Django auth/admin apps intentionally removed)

## Flow / URLs (rental/urls.py)

```
home  cars  cars/<id>  booking/<car_id>  verification/<id>  delivery/<id>  summary/<id>
payment/<id>  payment/verify/  payment/failure/<id>  confirmation/<id>  my-bookings/  profile/
login/  signup/  logout/  auth/clerk-callback/  dashboard/  dashboard/demo-toggle/
dashboard/bookings/<id>/cancel/  .../docs/approve/  .../docs/reject/  .../trip/<start|complete>/  .../note/
dashboard/cars/<id>/status/  dashboard/cars/add/  dashboard/locations/add/  dashboard/blocks/add/
```

Boot-flow: Car → Dates (`booking/<car_id>`) → Verification (docs) → Delivery → Summary → Payment → Confirmation, guarded by `@customer_required` and per-owner lookup via `owned_booking()`.

## Key logic

- **Availability**: overlapping-blocking query on PENDING/PENDING_VERIFICATION/CONFIRMED/ACTIVE bookings (only fresh PENDING is hold-time-limited; paid PENDING_VERIFICATION always blocks); admin `CarBlock` dates also block. Booking creation + payment verification both re-check inside `transaction.atomic()` with `select_for_update()` (concurrency guard).
- **Pricing**: `rental_days = max((drop.date - pickup.date).days, 1)`; total = (rate×days) + delivery(300 if HOME_DELIVERY) + 18% tax; example brand-tested: 7500+300+1404=₹9204.
- **Auth**: Clerk-JS on frontend posts `clerk_user_id` to `clerk_callback` → server verifies via Clerk API, `update_or_create` Customer, role from Supabase `user_roles` (falls back to local Customer.role), stores in session. Admins → `/dashboard/`. `clerk_admin_required` decorator enforces ADMIN (re-reads DB role, not session).
  - `python manage.py promote_admin <id-or-email>` to grant ADMIN.
- **Payments**: no Razorpay keys ⇒ mock mode (`order_mock_*` / `pay_mock_*`), verify endpoint also checks amount for real orders, marks PAID + **PENDING_VERIFICATION** (pay-first flow), sends receipt email. Admin can toggle `demo_mode` in SiteSetting to force-mock even with keys.
- **Doc verification → confirmation**: admin approves documents → booking CONFIRMED (+ email). Reject sets `rejection_reason`, customer sees reason on verification/confirmation pages and re-uploads. Admin cancel marks PAID→REFUNDED and frees the car. Trip lifecycle: CONFIRMED → (start) → ACTIVE → (complete) → COMPLETED.
- **Docs**: upload creates/updates Document per type, PENDING status (clears rejection_reason), uploads to Supabase Storage when configured else `media/documents/<booking_id>/`; admins view via `Document.view_url`.

## Templates (all extend base.html; shared navbar/footer partials; pop page-specific block)

- `index` (home), `cars` (search/filter/sort + availability badges), `car-details`, `booking`, `verification`, `delivery`, `summary`, `payment`, `confirmation`, `my-bookings` (tabs), `profile`, `login`, `signup`, `logged_out`, `admin_dashboard`
- `partials/navbar.html` — role-aware (Login btn vs avatar, Admin link only for admins), `data-auth` syncs Clerk session (`drivegoSyncSession` in base.html)
- `partials/car_card.html` — badge + booked-period chip + "Free from …" for unavailable cars
- `base.html` holds global Clerk session-sync JS; page-specific JS in `{% block scripts %}`

## Static

- `css/styles.css` — single design system (Manrope, charcoal #111827, accent #146EF5, rounded 18px cards, breakpoints 1100/960/600)
- `js/site.js` — nav toggle, image fallback SVG, delivery-method cards, payment-method tabs

## Tests (rental/tests.py — run `python manage.py test rental`)

- `BookingLogicTests` — overlap accept/reject, non-overlap, maintenance blocked, pricing incl. delivery+tax, booking-id format, location filter, per-user isolation
- `AdminAccessTests` — non-admin dashboard redirect, admin access, navbar avatar/admin-link/anonymous-login visibility
- `BookingGuardTests` — unpaid confirmation → payment, stale session role re-checked from DB, double payment-verify idempotent
- `DemoModeTests` — toggle admin-gated, on/off behaviour
- `StalePendingReleaseTests` — PENDING holds release after `BOOKING_HOLD_MINUTES`, fresh PENDING still blocks
- `ListedAvailabilityTests` — future/active confirmed booking ⇒ `is_listed_available()` False (booked badge + chip on no-date listings); still bookable for non-overlapping windows; completed bookings don't hide cars
- `CancelBookingTests` — owner can cancel PENDING/PENDING_VERIFICATION/CONFIRMED/ACTIVE (paid → REFUNDED, releases car); others can't; completed/cancelled can't be cancelled
- `PaymentGuardTests` — completed-paid confirmation renders (no loop), cancelled booking cannot be paid, demo mode completes payment even with keys configured (status now PENDING_VERIFICATION)
- `CarBlockTests` — admin block makes window unavailable + hides car from listing; admin can add a block
- `AdminControlsTests` — approve docs → CONFIRMED; reject sets reason; reject requires reason; non-admins blocked; cancel → REFUNDED + car freed; trip start/complete; admin note; add car
- `OpenRedirectTests` — `?next=` must be same-host per `url_has_allowed_host_and_scheme`
- Dashboard warns "Razorpay keys not configured" when keys are missing.

## Bug fixes (2026-09-14, user-approved)

1. **Demo mode broken when keys configured** — `verify_razorpay_signature` checked `settings.RAZORPAY_MOCK` (a boot-time constant) instead of the actual `order_mock_*` prefix. Now decided by order-id prefix only (`rental/utils.py`); `payment` view sets `is_mock` from prefix; cancelled/rejected bookings can't be paid (`payment_verify` → 409).
2. **"Available" after booking** — listing badge logic now uses `Car.is_listed_available()` (= status AVAILABLE and **no upcoming/active booking**), so a car with a confirmed future booking shows **Booked** with the "Booked … - …" chip on `/` and `/cars/` (no-dates); strict window overlap (`is_available_for`) is still used for dated searches and booking/safety guards so non-overlapping windows remain bookable. `car_card.html` shows `get_status_display` (e.g. "Maintenance") instead of always "Booked".
3. **Failed/abandoned payments blocked cars forever** — new `Booking._blocking_q` + `Car.blocking_bookings`/`Booking.overlaps`; PENDING holds are ignored once older than `BOOKING_HOLD_MINUTES` (default 60, `drivego/settings.py` + `.env.example`).
4. **Confirmation ⇄ payment redirect loop** — `confirmation` renders any PAID booking; `payment` guards PENDING/FAILED states.
5. **Booking-ID race** — `booking_dates` generates the id inside a `select_for_update` txn with a 5-attempt `IntegrityError` retry loop (`DG-YYYYMMDD-NNNN` kept).
6. **Open redirect** — `_safe_next()` (title `url_has_allowed_host_and_scheme`) applied to `login_view` and `clerk_callback`.
7. **Cosmetic** — summary/confirmation label "Return to {pickup}"; dynamic default dates + home stats; footer unified (no more "mock data" line); tax shows `TAX_PERCENT`, delivery shows `DELIVERY_CHARGE` from context_processor.
8. **Housekeeping** — stale `db.sqlite3` deleted + re-migrated (empty, matches current schema); stray 0-byte `"Login in anon,"` removed.
9. **Cancel booking (user side)** — POST `/bookings/<booking_id>/cancel/` (`views.cancel_booking`): owner-only, allowed for PENDING/CONFIRMED/ACTIVE; sets `CANCELLED`, marks `PAID`→`REFUNDED`, releases the car. "Cancel"/"Cancel Booking" buttons on `my-bookings.html` cards + `confirmation.html`; `.btn-danger` + `.price-actions` added to `styles.css`.

## Admin controls (2026-09-15, user-approved)

Pay-first verification flow + full admin panel on `/dashboard/`:
1. **Pay first, confirm after doc review** — `/payment/verify/` now sets booking to **PENDING_VERIFICATION** after payment (was CONFIRMED). Paid bookings always block the car (not subject to the 60-min hold release). Admin approves docs → CONFIRMED + confirmation email.
2. **Doc verification queue** — dashboard section listing PENDING/PENDING_VERIFICATION bookings with uploaded docs (view links via `Document.view_url`), Approve & Confirm, Reject-with-reason (sets `rejection_reason`, customer re-uploads → status resets), Cancel & Refund.
3. **Admin cancel = auto full refund** — `admin_cancel_booking` marks CANCELLED + PAID→REFUNDED, frees the car.
4. **Trip lifecycle** — `admin_trip_action` start (CONFIRMED→ACTIVE) / complete (CONFIRMED or ACTIVE→COMPLETED) buttons per row.
5. **Car status manager** — `admin_set_car_status` flips AVAILABLE/MAINTENANCE/INACTIVE; **Add car** form (`admin_add_car`); **Add location** form (`admin_add_location`).
6. **Block dates** — `CarBlock` model + `admin_block_dates`; blocks feed `is_available_for()`/`is_listed_available()` so the car disappears from availability during the hold.
7. **Customer directory** — all customers with booking/trip counts.
8. **Admin note** — `Booking.admin_note` + `admin_set_note`; shown on the confirmation page to the customer.
- Customer-facing updates: confirmation page shows "Payment Received!" + doc status + rejection reasons + admin note; `verification.html` surfaces rejection reasons; my-bookings badge for PENDING_VERIFICATION.

## Notes / housekeeping

- **Secrets live in `.env`** (gitignored) — don't log or commit them.
- **`db.sqlite3`** — regenerated 2026-09-14 via `DATABASE_URL='' python manage.py migrate --no-input`; empty, current schema. Real data lives in Supabase. Local runs must still set `DATABASE_URL=''` (or a sqlite URL) to avoid touching the remote DB.
- **Demo/temp removed (2026-09-15)** — `seed_demo.py` and `probe_audit.py` deleted; demo login (`DEV_LOGIN`) and the `demo_user` fallback removed (Clerk keys now required); "(demo)" UI labels dropped. Mock-payment fallback + demo-mode toggle kept for running without Razorpay keys.