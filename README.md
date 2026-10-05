# DriveGo – Car Rental Website

DriveGo is a car rental website for Kolkata. Customers can search for a car, check if it is free on their dates, enter the driving licence details, and book and pay online. An admin panel lets staff manage bookings, cars and customers.

## Main features

**Customers**
- Search cars by location, dates, type and price
- See live availability on a calendar
- Book in 5 steps: Dates → Driver → Delivery → Review → Payment
- Driving licence checked automatically (number format, issuing state, expiry date, age)
- Pick up from a store or get home delivery
- Pay online and cancel before pickup for a full refund
- View all bookings on the My Bookings page
- Edit name, mobile number and the saved licence from My Bookings

**Admin**
- Dashboard with revenue and today's pickups and returns
- See the licence every upcoming trip was booked on, and re-run the check on any booking
- Manage bookings, cars, locations and blocked dates
- Give or remove admin access

## Tech stack

| Part | Technology |
|------|------------|
| Backend | Python, Django |
| Frontend | HTML, CSS, JavaScript (Django templates) |
| Database | PostgreSQL (Supabase) |
| Login | Clerk |
| Payments | Razorpay |
| Hosting | Vercel |

## How a booking works

1. The customer chooses a car and dates. The car is held for 60 minutes.
2. They say who is driving and enter that person's licence number, date of birth and expiry date. It is checked on the spot: the RTO number format, a real state code, an expiry date that covers the trip, and a driver aged 18 or over. A licence checked once is saved to the profile, so the next booking skips this step.
3. They choose store pickup or home delivery.
4. They pay: price per day × number of days, plus delivery and 18% tax.
5. The booking is confirmed straight away. Staff match the original licence against the booking at handover.

## Project folders

```
drivego/     Django settings and main URLs
rental/      Main app: models, views, forms, tests
templates/   HTML pages
static/      CSS and JavaScript files
```

## How to run

```bash
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py runserver
```

Then open http://127.0.0.1:8000.

Add your Clerk, Supabase and Razorpay keys in the `.env` file. Without Razorpay keys, payments run in demo mode.

To run the tests:

```bash
python manage.py test rental
```
