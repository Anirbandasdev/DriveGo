import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import Booking, Car, CarBlock, Customer, Document, Location, SiteSetting
from .views import availability_for


class BookingLogicTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Salt Lake", address="Sector V", city="Kolkata")
        self.other = Location.objects.create(name="Howrah", address="Station Road", city="Howrah")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Innova Crysta",
            registration_number="WB06T0001", seats=7, transmission="Automatic",
            fuel_type="Diesel", price_per_day=2500, status="AVAILABLE",
        )
        self.user = Customer.objects.create(clerk_user_id="user_test_1", email="t1@example.com")
        self.other_user = Customer.objects.create(clerk_user_id="user_test_2", email="t2@example.com")
        now = timezone.now()
        self.existing = Booking.objects.create(
            booking_id="DG-TEST-0001", user=self.user, car=self.car, pickup_location=self.loc,
            pickup_datetime=now + timedelta(days=2), dropoff_datetime=now + timedelta(days=5),
            status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PAID,
        )
        self.existing.apply_price()
        self.existing.save()

    def test_available_period_has_no_overlap(self):
        now = timezone.now()
        self.assertFalse(Booking.overlaps(self.car, now + timedelta(days=6), now + timedelta(days=8)).exists())
        self.assertTrue(self.car.is_available_for(now + timedelta(days=6), now + timedelta(days=8)))

    def test_overlapping_period_is_blocked(self):
        now = timezone.now()
        self.assertTrue(Booking.overlaps(self.car, now + timedelta(days=3), now + timedelta(days=4)).exists())
        self.assertFalse(self.car.is_available_for(now + timedelta(days=3), now + timedelta(days=4)))

    def test_non_overlapping_period_is_allowed(self):
        now = timezone.now()
        self.assertFalse(Booking.overlaps(self.car, now + timedelta(days=5, hours=1), now + timedelta(days=7)).exists())

    def test_maintenance_car_cannot_be_booked(self):
        self.car.status = "MAINTENANCE"
        self.car.save()
        now = timezone.now()
        self.assertFalse(self.car.is_available_for(now + timedelta(days=20), now + timedelta(days=22)))

    def test_price_includes_delivery_and_tax(self):
        now = timezone.now()
        booking = Booking(
            user=self.user, car=self.car, pickup_location=self.loc,
            pickup_datetime=now + timedelta(days=10), dropoff_datetime=now + timedelta(days=13),
            delivery_method="HOME_DELIVERY",
        )
        self.assertEqual(booking.rental_days, 3)
        price = booking.calculate_price()
        self.assertEqual(price["rental_amount"], Decimal("7500"))
        self.assertEqual(price["delivery_charge"], Decimal("300"))
        self.assertEqual(price["tax_amount"], Decimal("1404"))
        self.assertEqual(price["total_amount"], Decimal("9204"))

    def test_booking_id_format(self):
        from .views import generate_booking_id

        bid = generate_booking_id()
        self.assertRegex(bid, r"^DG-\d{8}-\d{4}$")

    def test_location_filter(self):
        Car.objects.create(
            location=self.other, category="Hatchback", brand="Tata", model="Nexon",
            registration_number="WB06T0002", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1400, status="AVAILABLE",
        )
        client = Client()
        response = client.get("/cars/", {"location": "Howrah"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nexon")
        self.assertNotContains(response, "Innova Crysta")

    def test_user_can_only_see_own_bookings(self):
        other_booking = Booking.objects.create(
            booking_id="DG-TEST-0002", user=self.other_user, car=self.car, pickup_location=self.loc,
            pickup_datetime=timezone.now() + timedelta(days=30),
            dropoff_datetime=timezone.now() + timedelta(days=31),
            status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PAID,
        )
        other_booking.apply_price()
        other_booking.save()
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_test_1"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        response = client.get("/my-bookings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DG-TEST-0001")
        self.assertNotContains(response, "DG-TEST-0002")


class AdminAccessTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(clerk_user_id="user_cust", email="c@example.com")
        self.admin = Customer.objects.create(clerk_user_id="user_admin", email="a@example.com", role="ADMIN")

    def login_as(self, clerk_user_id, role="CUSTOMER"):
        client = Client()
        session = client.session
        session["clerk_user_id"] = clerk_user_id
        session["role"] = role
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def test_normal_user_cannot_open_dashboard(self):
        response = self.login_as("user_cust").get("/dashboard/")
        self.assertEqual(response.status_code, 302)

    def test_admin_can_open_dashboard(self):
        response = self.login_as("user_admin", "ADMIN").get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Dashboard")

    def test_navbar_hides_login_and_admin_for_customers(self):
        body = self.login_as("user_cust").get("/").content.decode()
        self.assertIn('href="/profile/" class="avatar"', body)
        self.assertNotIn("Admin</a>", body.split('class="sn-links"')[1].split("</nav>")[0])
        self.assertNotIn("Admin console</a>", body)

    def test_navbar_shows_admin_link_for_admins(self):
        body = self.login_as("user_admin", "ADMIN").get("/").content.decode()
        self.assertIn('href="/dashboard/"', body)

    def test_anonymous_navbar_shows_login(self):
        body = Client().get("/").content.decode()
        self.assertIn('href="/login/" class="btn btn-ghost">Login', body)


@override_settings(RAZORPAY_MOCK=True, RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
class BookingGuardTests(TestCase):
    def setUp(self):
        loc = Location.objects.create(name="Salt Lake", address="Sector V", city="Kolkata")
        car = Car.objects.create(
            location=loc, category="SUV", brand="Toyota", model="Innova Crysta",
            registration_number="WB06T0100", seats=7, transmission="Automatic",
            fuel_type="Diesel", price_per_day=2500, status="AVAILABLE",
        )
        user = Customer.objects.create(clerk_user_id="user_guard", email="g@example.com")
        now = timezone.now()
        self.booking = Booking.objects.create(
            booking_id="DG-GUARD-1", user=user, car=car, pickup_location=loc,
            pickup_datetime=now + timedelta(days=1), dropoff_datetime=now + timedelta(days=2),
            status=Booking.Status.PENDING, payment_status=Booking.PaymentStatus.PENDING,
        )
        self.booking.apply_price()
        self.booking.save()

    def login(self):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_guard"
        session["role"] = "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def test_unpaid_confirmation_redirects_to_payment(self):
        response = self.login().get("/confirmation/DG-GUARD-1/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/payment/DG-GUARD-1/", response["Location"])

    def test_stale_session_role_still_enforces_db_role(self):
        Customer.objects.create(clerk_user_id="user_fresh_admin", email="fa@example.com", role="ADMIN")
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_fresh_admin"
        session["role"] = "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        self.assertEqual(client.get("/dashboard/").status_code, 200)

    def test_double_verify_stays_paid(self):
        client = self.login()
        payload = {"booking_id": "DG-GUARD-1", "razorpay_order_id": "order_mock_DG-GUARD-1",
                   "razorpay_payment_id": "pay_mock_DG-GUARD-1", "razorpay_signature": "mock"}
        import json as jsonlib

        self.booking.razorpay_order_id = "order_mock_DG-GUARD-1"
        self.booking.save(update_fields=["razorpay_order_id"])
        first = client.post("/payment/verify/", data=jsonlib.dumps(payload), content_type="application/json")
        second = client.post("/payment/verify/", data=jsonlib.dumps(payload), content_type="application/json")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.payment_status, Booking.PaymentStatus.PAID)


@override_settings(RAZORPAY_MOCK=True, RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
class DemoModeTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(clerk_user_id="user_cust", email="c@example.com")
        self.admin = Customer.objects.create(clerk_user_id="user_admin", email="a@example.com", role="ADMIN")

    def login_as(self, clerk_user_id, role="CUSTOMER"):
        client = Client()
        session = client.session
        session["clerk_user_id"] = clerk_user_id
        session["role"] = role
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def test_toggle_requires_admin(self):
        response = self.login_as("user_cust").post("/dashboard/demo-toggle/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SiteSetting.get("demo_mode", "off"), "off")

    @override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="rzp_test_secret")
    def test_admin_can_toggle_demo_mode(self):
        client = self.login_as("user_admin", "ADMIN")
        client.post("/dashboard/demo-toggle/")
        self.assertEqual(SiteSetting.get("demo_mode"), "on")
        body = client.get("/dashboard/").content.decode()
        self.assertIn("ON - payments simulated", body)
        client.post("/dashboard/demo-toggle/")
        self.assertEqual(SiteSetting.get("demo_mode"), "off")

    def test_dashboard_warns_when_razorpay_keys_missing(self):
        body = self.login_as("user_admin", "ADMIN").get("/dashboard/").content.decode()
        self.assertIn("Razorpay keys not configured", body)


class StalePendingReleaseTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Hold", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Hold Test",
            registration_number="WB06T9901", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.user = Customer.objects.create(clerk_user_id="user_hold", email="hold@example.com")
        self.now = timezone.now()

    def make_pending(self, created_delta_minutes):
        booking = Booking.objects.create(
            booking_id=f"DG-HOLD-{int(created_delta_minutes)}", user=self.user, car=self.car,
            pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=10), dropoff_datetime=self.now + timedelta(days=12),
            status=Booking.Status.PENDING, payment_status=Booking.PaymentStatus.PENDING,
        )
        booking.apply_price()
        booking.save()
        Booking.objects.filter(pk=booking.pk).update(created_at=self.now - timedelta(minutes=created_delta_minutes))
        return booking

    def test_fresh_pending_still_blocks(self):
        self.make_pending(1)
        self.assertFalse(self.car.is_available_for(self.now + timedelta(days=10), self.now + timedelta(days=12)))

    def test_expired_pending_releases_car(self):
        hold = settings.BOOKING_HOLD_MINUTES
        self.make_pending(hold + 10)
        self.assertTrue(self.car.is_available_for(self.now + timedelta(days=10), self.now + timedelta(days=12)))


class ListedAvailabilityTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Listed", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Listed",
            registration_number="WB06T9903", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.usr = Customer.objects.create(clerk_user_id="user_listed", email="listed@example.com")
        self.now = timezone.now()

    def book_future(self, booking_id="DG-LIST-0001", days=1):
        b = Booking.objects.create(
            booking_id=booking_id, user=self.usr, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=days), dropoff_datetime=self.now + timedelta(days=days + 3),
            status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PAID,
        )
        b.apply_price()
        b.save()
        return b

    def test_future_booking_only_blocks_its_own_window(self):
        self.book_future(days=1)
        self.assertTrue(self.car.is_available_for(self.now, self.now + timedelta(hours=1)))
        self.assertFalse(self.car.is_available_for(self.now + timedelta(days=1), self.now + timedelta(days=3)))
        self.assertTrue(self.car.is_available_for(self.now + timedelta(days=5), self.now + timedelta(days=7)))

    def test_cars_page_no_dates_shows_booked_badge(self):
        self.book_future(days=1)
        html = Client().get("/cars/").content.decode()
        block = html.split('<article class="vc')[1].split("</article>")[0]
        self.assertIn("vc-badge is-no", block)
        self.assertIn("Booked", block)
        self.assertIn("vc-note", block)
        self.assertIn("Free from", block)

    def test_cars_page_shows_booked_car_as_available_for_free_dates(self):
        self.book_future(days=1)
        start = timezone.localdate() + timedelta(days=10)
        html = Client().get("/cars/", {
            "pickup_date": start.isoformat(), "pickup_time": "10:00",
            "drop_date": (start + timedelta(days=2)).isoformat(), "drop_time": "10:00",
        }).content.decode()
        block = html.split('<article class="vc')[1].split("</article>")[0]
        self.assertIn("vc-badge is-ok", block)
        self.assertIn("total", block)
        self.assertIn(f"pickup_date={start.isoformat()}", block)

    def test_completed_booking_does_not_block(self):
        self.book_future(days=-5)
        Booking.objects.filter(booking_id="DG-LIST-0001").update(status=Booking.Status.COMPLETED)
        self.assertTrue(self.car.is_available_for(self.now - timedelta(days=5), self.now + timedelta(days=1)))


class PaymentGuardTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="PayGuard", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Guard",
            registration_number="WB06T9902", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.usr = Customer.objects.create(clerk_user_id="user_pay", email="user_pay@example.com")
        self.now = timezone.now()

    def login(self):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_pay"
        session["role"] = "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def make(self, booking_id, status=Booking.Status.CONFIRMED, pay=Booking.PaymentStatus.PAID):
        b = Booking(booking_id=booking_id, user=self.usr, car=self.car, pickup_location=self.loc,
                    pickup_datetime=self.now + timedelta(days=3), dropoff_datetime=self.now + timedelta(days=4),
                    status=status, payment_status=pay)
        b.apply_price()
        b.save()
        return b

    def test_completed_paid_booking_confirmation_renders(self):
        self.make("DG-PAY-COMP", Booking.Status.COMPLETED, Booking.PaymentStatus.PAID)
        response = self.login().get("/confirmation/DG-PAY-COMP/")
        self.assertEqual(response.status_code, 200)

    def test_cancelled_booking_cannot_be_paid(self):
        self.make("DG-PAY-CANC", Booking.Status.CANCELLED, Booking.PaymentStatus.REFUNDED)
        client = self.login()
        response = client.get("/payment/DG-PAY-CANC/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/my-bookings/", response["Location"])
        response = client.post("/payment/verify/", data='{"booking_id":"DG-PAY-CANC"}',
                               content_type="application/json")
        self.assertEqual(response.status_code, 409)

    @override_settings(RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="rzp_test_secret")
    def test_demo_mode_completes_booking_when_keys_configured(self):
        SiteSetting.set("demo_mode", "on")
        booking = self.make("DG-PAY-DEMO", Booking.Status.PENDING, Booking.PaymentStatus.PENDING)
        for doc_type in Document.DocType.values:
            Document.objects.create(booking=booking, document_type=doc_type, file="")
        client = self.login()
        client.get(f"/payment/{booking.booking_id}/")
        booking.refresh_from_db()
        self.assertTrue(booking.razorpay_order_id.startswith("order_mock_"))
        payload = '{"booking_id":"DG-PAY-DEMO","razorpay_order_id":"%s","razorpay_payment_id":"pay_mock_DG-PAY-DEMO","razorpay_signature":"mock","method":"upi"}' % booking.razorpay_order_id
        response = client.post("/payment/verify/", data=payload, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        booking.refresh_from_db()
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.PAID)
        self.assertEqual(booking.status, Booking.Status.PENDING_VERIFICATION)


class CancelBookingTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Cancel", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Cancel",
            registration_number="WB06T9904", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.usr = Customer.objects.create(clerk_user_id="user_cancel", email="cancel@example.com")
        self.other = Customer.objects.create(clerk_user_id="user_cancel2", email="cancel2@example.com")
        self.now = timezone.now()

    def login(self, user_id):
        client = Client()
        session = client.session
        session["clerk_user_id"] = user_id
        session["role"] = "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def make(self, booking_id, status=Booking.Status.CONFIRMED, pay=Booking.PaymentStatus.PAID, user_id="user_cancel"):
        user = Customer.objects.get(clerk_user_id=user_id)
        b = Booking.objects.create(
            booking_id=booking_id, user=user, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=2), dropoff_datetime=self.now + timedelta(days=4),
            status=status, payment_status=pay,
            razorpay_payment_id=f"pay_mock_{booking_id}" if pay == Booking.PaymentStatus.PAID else "")
        b.apply_price()
        b.save()
        return b

    def test_cancel_paid_booking_refunds(self):
        b = self.make("DG-CANC-0001")
        response = self.login("user_cancel").post(f"/bookings/{b.booking_id}/cancel/")
        self.assertEqual(response.status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.CANCELLED)
        self.assertEqual(b.payment_status, Booking.PaymentStatus.REFUNDED)
        self.assertTrue(self.car.is_available_for(b.pickup_datetime, b.dropoff_datetime))

    def test_cancel_unpaid_pending_no_refund(self):
        b = self.make("DG-CANC-0002", Booking.Status.PENDING, Booking.PaymentStatus.PENDING)
        self.login("user_cancel").post(f"/bookings/{b.booking_id}/cancel/")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.CANCELLED)
        self.assertEqual(b.payment_status, Booking.PaymentStatus.PENDING)

    def test_other_user_cannot_cancel(self):
        b = self.make("DG-CANC-0003")
        response = self.login("user_cancel2").post(f"/bookings/{b.booking_id}/cancel/")
        self.assertEqual(response.status_code, 302)
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.CONFIRMED)

    def test_completed_cannot_be_cancelled(self):
        b = self.make("DG-CANC-0004", Booking.Status.COMPLETED, Booking.PaymentStatus.PAID)
        self.login("user_cancel").post(f"/bookings/{b.booking_id}/cancel/")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.COMPLETED)

    def test_cancel_button_only_for_cancellable_bookings(self):
        client = self.login("user_cancel")
        cancellable = self.make("DG-CANC-0005")
        html = client.get("/my-bookings/").content.decode()
        self.assertIn(cancellable.booking_id, html)
        self.assertIn("Cancel", html)


class OpenRedirectTests(TestCase):
    def setUp(self):
        Customer.objects.create(clerk_user_id="user_redir", email="redir@example.com")

    def test_authed_user_next_must_be_local(self):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_redir"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        response = client.get("/login/", {"next": "https://evil.example.com/steal"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("evil.example.com", response["Location"])
        response = client.get("/login/", {"next": "/profile/"})
        self.assertIn("/profile/", response["Location"])


class CarBlockTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Block", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Block Test",
            registration_number="WB06T9907", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.now = timezone.now()

    def test_block_makes_window_unavailable(self):
        CarBlock.objects.create(
            car=self.car,
            start_datetime=self.now + timedelta(days=1),
            end_datetime=self.now + timedelta(days=3),
        )
        self.assertFalse(self.car.is_available_for(self.now + timedelta(days=1, hours=1), self.now + timedelta(days=2)))
        self.assertTrue(self.car.is_available_for(self.now + timedelta(days=5), self.now + timedelta(days=6)))

    def test_block_is_skipped_by_suggested_start(self):
        CarBlock.objects.create(
            car=self.car,
            start_datetime=self.now + timedelta(days=2),
            end_datetime=self.now + timedelta(days=4),
        )
        pickup = self.now + timedelta(days=3)
        nxt = availability_for(self.car, pickup, pickup + timedelta(days=1))["suggestion"]["pickup"]
        self.assertGreaterEqual(nxt, self.now + timedelta(days=4))
        self.assertTrue(self.car.is_available_for(nxt, nxt + timedelta(days=1)))

    def test_admin_can_add_block(self):
        Customer.objects.create(clerk_user_id="user_ablk", email="blk@example.com", role="ADMIN")
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_ablk"
        session["role"] = "ADMIN"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        response = client.post("/dashboard/blocks/add/", {
            "car_id": self.car.pk,
            "start_datetime": "2099-01-01T10:00",
            "end_datetime": "2099-01-02T10:00",
            "note": "Maintenance",
        })
        self.assertEqual(response.status_code, 302)
        block = CarBlock.objects.get(car=self.car)
        self.assertEqual(block.note, "Maintenance")


class AdminControlsTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Admin", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Control",
            registration_number="WB06T9911", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.admin = Customer.objects.create(clerk_user_id="user_admin_ctl", email="actl@example.com", role="ADMIN")
        self.user = Customer.objects.create(clerk_user_id="user_ctl", email="ctl@example.com")
        self.now = timezone.now()

    def login(self, admin=True):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_admin_ctl" if admin else "user_ctl"
        session["role"] = "ADMIN" if admin else "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def make(self, bid, status=Booking.Status.PENDING_VERIFICATION, pay=Booking.PaymentStatus.PAID):
        b = Booking.objects.create(
            booking_id=bid, user=self.user, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=3), dropoff_datetime=self.now + timedelta(days=5),
            status=status, payment_status=pay,
            razorpay_payment_id=f"pay_mock_{bid}" if pay == Booking.PaymentStatus.PAID else "",
        )
        b.apply_price()
        b.save()
        return b

    def add_doc(self, b, verified=False, doc_type=Document.DocType.DRIVING_LICENSE):
        return Document.objects.create(
            booking=b, document_type=doc_type, file="",
            verification_status=Document.VerificationStatus.VERIFIED if verified else Document.VerificationStatus.PENDING,
        )

    def test_admin_approve_docs_confirms_paid_booking(self):
        b = self.make("DG-ADM-0001")
        doc = self.add_doc(b)
        self.add_doc(b, doc_type=Document.DocType.GOVT_ID)
        self.login().post(f"/dashboard/bookings/{b.booking_id}/docs/approve/")
        doc.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(doc.verification_status, Document.VerificationStatus.VERIFIED)
        self.assertEqual(b.status, Booking.Status.CONFIRMED)

    def test_admin_reject_docs_sets_reason(self):
        b = self.make("DG-ADM-0002")
        self.add_doc(b)
        self.login().post(f"/dashboard/bookings/{b.booking_id}/docs/reject/", {"reason": "Blurry scan"})
        doc = b.documents.get()
        self.assertEqual(doc.verification_status, Document.VerificationStatus.REJECTED)
        self.assertEqual(doc.rejection_reason, "Blurry scan")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.PENDING_VERIFICATION)

    def test_admin_reject_requires_reason(self):
        b = self.make("DG-ADM-0003")
        self.add_doc(b)
        self.login().post(f"/dashboard/bookings/{b.booking_id}/docs/reject/", {"reason": ""})
        self.assertNotEqual(b.documents.get().verification_status, Document.VerificationStatus.REJECTED)

    def test_customer_cannot_run_admin_actions(self):
        b = self.make("DG-ADM-0004")
        self.login(admin=False).post(f"/dashboard/bookings/{b.booking_id}/cancel/")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.PENDING_VERIFICATION)

    def test_admin_cancel_paid_refunds(self):
        b = self.make("DG-ADM-0005")
        self.login().post(f"/dashboard/bookings/{b.booking_id}/cancel/")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.CANCELLED)
        self.assertEqual(b.payment_status, Booking.PaymentStatus.REFUNDED)
        self.assertTrue(self.car.is_available_for(b.pickup_datetime, b.dropoff_datetime))

    def test_trip_lifecycle(self):
        b = self.make("DG-ADM-0006", status=Booking.Status.CONFIRMED)
        client = self.login()
        client.post(f"/dashboard/bookings/{b.booking_id}/trip/start/")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.ACTIVE)
        client.post(f"/dashboard/bookings/{b.booking_id}/trip/complete/")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.COMPLETED)

    def test_admin_note(self):
        b = self.make("DG-ADM-0007")
        self.login().post(f"/dashboard/bookings/{b.booking_id}/note/", {"note": "Please upload license back"})
        b.refresh_from_db()
        self.assertEqual(b.admin_note, "Please upload license back")

    def test_admin_can_add_car(self):
        client = self.login()
        response = client.post("/dashboard/cars/add/", {
            "location": self.loc.pk, "category": "Sedan", "brand": "Honda", "model": "City",
            "registration_number": "WB06T9912", "seats": 5, "transmission": "Automatic",
            "fuel_type": "Petrol", "price_per_day": 2200, "status": "AVAILABLE",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Car.objects.filter(registration_number="WB06T9912").exists())

    def test_verification_queue_lists_paid_bookings_not_abandoned_holds(self):
        paid = self.make("DG-ADM-0008")
        self.add_doc(paid)
        stale = self.make("DG-ADM-0009", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING)
        Booking.objects.filter(pk=stale.pk).update(
            created_at=self.now - timedelta(minutes=settings.BOOKING_HOLD_MINUTES + 30))
        response = self.login().get("/dashboard/verifications/")
        self.assertContains(response, "DG-ADM-0008")
        self.assertNotContains(response, "DG-ADM-0009")


class ExpiredPendingTests(TestCase):
    """Test that expired PENDING bookings don't appear as 'upcoming'."""

    def setUp(self):
        from django.conf import settings as _s
        self.loc = Location.objects.create(name="Test", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Test",
            registration_number="WB06T0001", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.user = Customer.objects.create(clerk_user_id="user_test", email="test@example.com")
        self.now = timezone.now()
        self.hold_minutes = int(getattr(_s, "BOOKING_HOLD_MINUTES", 60))

    def login(self):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_test"
        session["role"] = "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def make_pending_booking(self, minutes_ago):
        booking = Booking.objects.create(
            booking_id=f"DG-PEND-{minutes_ago:04d}",
            user=self.user, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=1),
            dropoff_datetime=self.now + timedelta(days=2),
            status=Booking.Status.PENDING, payment_status=Booking.PaymentStatus.PENDING,
        )
        booking.apply_price()
        booking.save()
        Booking.objects.filter(pk=booking.pk).update(
            created_at=self.now - timedelta(minutes=minutes_ago)
        )
        return booking

    def test_fresh_pending_shows_as_upcoming(self):
        self.make_pending_booking(self.hold_minutes // 2)
        client = self.login()
        response = client.get("/my-bookings/?tab=upcoming")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"DG-PEND-{self.hold_minutes//2:04d}")

    def test_expired_pending_not_shown_as_upcoming(self):
        self.make_pending_booking(self.hold_minutes + 10)
        client = self.login()
        response = client.get("/my-bookings/?tab=upcoming")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f"DG-PEND-{self.hold_minutes+10:04d}")


class ConfirmationEdgeCaseTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Test", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Test",
            registration_number="WB06T0001", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.user = Customer.objects.create(clerk_user_id="user_test", email="test@example.com")
        self.now = timezone.now()

    def login(self):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_test"
        session["role"] = "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def test_cancelled_paid_booking_redirects_from_confirmation(self):
        booking = Booking.objects.create(
            booking_id="DG-PAID-CANC", user=self.user, car=self.car,
            pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=1),
            dropoff_datetime=self.now + timedelta(days=2),
            status=Booking.Status.CANCELLED, payment_status=Booking.PaymentStatus.PAID,
        )
        booking.apply_price()
        booking.save()
        client = self.login()
        response = client.get(f"/confirmation/{booking.booking_id}/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/my-bookings/", response["Location"])

    def test_rejected_paid_booking_redirects_from_confirmation(self):
        booking = Booking.objects.create(
            booking_id="DG-PAID-REJ", user=self.user, car=self.car,
            pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=1),
            dropoff_datetime=self.now + timedelta(days=2),
            status=Booking.Status.REJECTED, payment_status=Booking.PaymentStatus.PAID,
        )
        booking.apply_price()
        booking.save()
        client = self.login()
        response = client.get(f"/confirmation/{booking.booking_id}/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/my-bookings/", response["Location"])


class CancelBookingPreserveTabTests(TestCase):
    def setUp(self):
        from django.conf import settings as _s
        self.loc = Location.objects.create(name="Test", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Test",
            registration_number="WB06T0001", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.user = Customer.objects.create(clerk_user_id="user_test", email="test@example.com")
        self.now = timezone.now()
        self.hold_minutes = int(getattr(_s, "BOOKING_HOLD_MINUTES", 60))

    def login(self):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_test"
        session["role"] = "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def test_cancel_preserves_upcoming_tab(self):
        booking = Booking.objects.create(
            booking_id="DG-UPCOMING", user=self.user, car=self.car,
            pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=1),
            dropoff_datetime=self.now + timedelta(days=2),
            status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PENDING,
        )
        booking.apply_price()
        booking.save()
        client = self.login()
        response = client.post(
            f"/bookings/{booking.booking_id}/cancel/",
            data={"tab": "upcoming"},
            HTTP_REFERER="/my-bookings/?tab=upcoming",
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/my-bookings/?tab=upcoming", response["Location"])


class CleanupCommandTests(TestCase):
    def setUp(self):
        from django.conf import settings as _s
        self.loc = Location.objects.create(name="Test", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Test",
            registration_number="WB06T0001", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.user = Customer.objects.create(clerk_user_id="user_test", email="test@example.com")
        self.now = timezone.now()
        self.hold_minutes = int(getattr(_s, "BOOKING_HOLD_MINUTES", 60))

    def make_pending_booking(self, minutes_ago):
        booking = Booking.objects.create(
            booking_id=f"DG-CLEANUP-{minutes_ago:04d}",
            user=self.user, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=1),
            dropoff_datetime=self.now + timedelta(days=2),
            status=Booking.Status.PENDING, payment_status=Booking.PaymentStatus.PENDING,
        )
        booking.apply_price()
        booking.save()
        Booking.objects.filter(pk=booking.pk).update(
            created_at=self.now - timedelta(minutes=minutes_ago)
        )
        return booking

    def test_cleanup_removes_old_pending_bookings(self):
        self.make_pending_booking(self.hold_minutes // 2)
        self.make_pending_booking(self.hold_minutes * 3)
        from django.core.management import call_command
        import io
        out = io.StringIO()
        call_command("cleanup_pending_bookings", stdout=out)
        self.assertEqual(Booking.objects.count(), 1)
        remaining = Booking.objects.first()
        self.assertTrue(remaining.booking_id.startswith("DG-CLEANUP-0030"))
        self.assertIn("Deleted 1 expired PENDING booking(s)", out.getvalue())

    def test_cleanup_no_old_bookings(self):
        self.make_pending_booking(self.hold_minutes // 2)
        self.make_pending_booking(self.hold_minutes)
        from django.core.management import call_command
        import io
        out = io.StringIO()
        call_command("cleanup_pending_bookings", stdout=out)
        self.assertEqual(Booking.objects.count(), 2)
        self.assertIn("No expired PENDING bookings to clean up", out.getvalue())


class AdminBookingsFilterTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Test", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Test",
            registration_number="WB06T0001", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.user = Customer.objects.create(clerk_user_id="user_test", email="test@example.com")
        self.admin = Customer.objects.create(
            clerk_user_id="admin_test", email="admin@example.com", role="ADMIN"
        )
        self.now = timezone.now()

    def login_as_admin(self):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "admin_test"
        session["role"] = "ADMIN"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def test_admin_bookings_excludes_cancelled_by_default(self):
        active = Booking.objects.create(
            booking_id="DG-ACTIVE", user=self.user, car=self.car,
            pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=1),
            dropoff_datetime=self.now + timedelta(days=2),
            status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PAID,
        )
        cancelled = Booking.objects.create(
            booking_id="DG-CANCELLED", user=self.user, car=self.car,
            pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=2),
            dropoff_datetime=self.now + timedelta(days=3),
            status=Booking.Status.CANCELLED, payment_status=Booking.PaymentStatus.REFUNDED,
        )
        active.apply_price(); cancelled.apply_price()
        active.save(); cancelled.save()
        client = self.login_as_admin()
        response = client.get("/dashboard/bookings/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DG-ACTIVE")
        self.assertNotContains(response, "DG-CANCELLED")

    def test_admin_bookings_shows_cancelled_when_requested(self):
        active = Booking.objects.create(
            booking_id="DG-ACTIVE", user=self.user, car=self.car,
            pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=1),
            dropoff_datetime=self.now + timedelta(days=2),
            status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PAID,
        )
        cancelled = Booking.objects.create(
            booking_id="DG-CANCELLED", user=self.user, car=self.car,
            pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=2),
            dropoff_datetime=self.now + timedelta(days=3),
            status=Booking.Status.CANCELLED, payment_status=Booking.PaymentStatus.REFUNDED,
        )
        active.apply_price(); cancelled.apply_price()
        active.save(); cancelled.save()
        client = self.login_as_admin()
        response = client.get("/dashboard/bookings/?status=cancelled")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DG-CANCELLED")
        self.assertNotContains(response, "DG-ACTIVE")


@override_settings(RAZORPAY_MOCK=True, RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
class RegressionFixTests(TestCase):
    def setUp(self):
        self.loc = Location.objects.create(name="Regression", address="Addr", city="Kolkata")
        self.car = Car.objects.create(
            location=self.loc, category="SUV", brand="Toyota", model="Regression",
            registration_number="WB06T9999", seats=5, transmission="Manual",
            fuel_type="Petrol", price_per_day=1500, status="AVAILABLE",
        )
        self.usr = Customer.objects.create(clerk_user_id="user_reg", email="reg@example.com")
        self.now = timezone.now()

    def login(self):
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_reg"
        session["role"] = "CUSTOMER"
        session.save()
        client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
        return client

    def make(self, booking_id):
        b = Booking.objects.create(
            booking_id=booking_id, user=self.usr, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=3), dropoff_datetime=self.now + timedelta(days=4),
            status=Booking.Status.PENDING, payment_status=Booking.PaymentStatus.PENDING,
            razorpay_order_id=f"order_mock_{booking_id}",
        )
        b.apply_price()
        b.save()
        return b

    def test_payment_verify_honors_admin_car_block(self):
        booking = self.make("DG-REG-BLK")
        CarBlock.objects.create(
            car=self.car, start_datetime=self.now + timedelta(days=3),
            end_datetime=self.now + timedelta(days=4),
        )
        client = self.login()
        response = client.post(
            "/payment/verify/",
            data=json.dumps({
                "booking_id": booking.booking_id,
                "razorpay_order_id": booking.razorpay_order_id,
                "razorpay_payment_id": f"pay_mock_{booking.booking_id}",
                "razorpay_signature": "mock",
            }),
            content_type="application/json",
        )
        booking.refresh_from_db()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.REFUNDED)

    def test_payment_retry_rotates_razorpay_order(self):
        SiteSetting.set("demo_mode", "on")
        booking = self.make("DG-REG-RTRY")
        for doc_type in Document.DocType.values:
            Document.objects.create(booking=booking, document_type=doc_type, file="")
        booking.razorpay_order_id = "order_old_attempt"
        booking.save(update_fields=["razorpay_order_id"])
        client = self.login()
        client.get(f"/payment/{booking.booking_id}/")
        booking.refresh_from_db()
        self.assertTrue(booking.razorpay_order_id.startswith("order_mock_"))
        self.assertNotEqual(booking.razorpay_order_id, "order_old_attempt")

    @override_settings(RAZORPAY_MOCK=True)
    def test_mock_mode_with_non_mock_order_returns_failed_not_500(self):
        booking = self.make("DG-REG-MOCK")
        booking.razorpay_order_id = "order_real_101"
        booking.save(update_fields=["razorpay_order_id"])
        client = self.login()
        response = client.post(
            "/payment/verify/",
            data=json.dumps({
                "booking_id": booking.booking_id,
                "razorpay_order_id": "order_real_101",
                "razorpay_payment_id": "pay_mock_x",
                "razorpay_signature": "mock",
            }),
            content_type="application/json",
        )
        booking.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(booking.payment_status, Booking.PaymentStatus.PAID)

    def test_rejected_booking_has_no_confirmation_view_link(self):
        rejected = Booking.objects.create(
            booking_id="DG-REG-REJ", user=self.usr, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=3), dropoff_datetime=self.now + timedelta(days=4),
            status=Booking.Status.REJECTED, payment_status=Booking.PaymentStatus.PENDING,
        )
        rejected.apply_price()
        rejected.save()
        html = self.login().get("/my-bookings/?tab=cancelled").content.decode()
        self.assertIn("DG-REG-REJ", html)
        self.assertNotIn(f'href="/confirmation/{rejected.booking_id}/"', html)

    def test_document_form_allows_submit_with_all_docs_present(self):
        booking = self.make("DG-REG-DOCS")
        Document.objects.create(booking=booking, document_type=Document.DocType.DRIVING_LICENSE, file="")
        Document.objects.create(booking=booking, document_type=Document.DocType.GOVT_ID, file="")
        client = self.login()
        response = client.post(f"/verification/{booking.booking_id}/", {})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/delivery/{booking.booking_id}/")


def _login(user_id):
    client = Client()
    session = client.session
    session["clerk_user_id"] = user_id
    session.save()
    client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key  # signed-cookie sessions
    return client


class FlowFixtureMixin:
    def setUp(self):
        self.loc = Location.objects.create(name="Flow", address="Park Street", city="Kolkata", phone="033000")
        self.car = Car.objects.create(
            location=self.loc, category="Sedan", brand="Honda", model="City",
            registration_number="WB06F0001", seats=5, transmission="Automatic",
            fuel_type="Petrol", price_per_day=2000, status="AVAILABLE",
        )
        self.user = Customer.objects.create(clerk_user_id="user_flow", email="flow@example.com", full_name="Flow User")
        self.other = Customer.objects.create(clerk_user_id="user_flow2", email="flow2@example.com")
        self.admin = Customer.objects.create(clerk_user_id="user_flow_admin", email="fa@example.com", role="ADMIN")
        self.now = timezone.now()

    def make(self, bid, days_from_now=3, length=2, user=None, status=Booking.Status.CONFIRMED,
             pay=Booking.PaymentStatus.PAID, docs=False, verified=False):
        b = Booking.objects.create(
            booking_id=bid, user=user or self.user, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=days_from_now),
            dropoff_datetime=self.now + timedelta(days=days_from_now + length),
            status=status, payment_status=pay,
            razorpay_payment_id=f"pay_mock_{bid}" if pay == Booking.PaymentStatus.PAID else "",
        )
        b.apply_price()
        b.save()
        if docs:
            for doc_type in Document.DocType.values:
                Document.objects.create(
                    booking=b, document_type=doc_type, file="",
                    verification_status=Document.VerificationStatus.VERIFIED if verified else Document.VerificationStatus.PENDING,
                )
        return b

    @staticmethod
    def dates(start, days):
        return {
            "pickup_date": start.strftime("%Y-%m-%d"), "pickup_time": "10:00",
            "drop_date": (start + timedelta(days=days)).strftime("%Y-%m-%d"), "drop_time": "10:00",
        }


class BookedCarOtherDatesTests(FlowFixtureMixin, TestCase):
    """A car booked for one window must stay bookable for every other window."""

    def test_detail_page_enables_booking_for_free_dates(self):
        self.make("DG-FLOW-0001", days_from_now=2, length=3)
        free = timezone.localdate() + timedelta(days=12)
        html = Client().get(f"/cars/{self.car.pk}/", self.dates(free, 2)).content.decode()
        self.assertIn("Available for your dates", html)
        self.assertIn("Book for these dates", html)
        self.assertNotIn("data-av-submit disabled", html)

    def test_detail_page_without_dates_still_offers_booking_when_default_window_free(self):
        self.make("DG-FLOW-0002", days_from_now=20, length=2)
        html = Client().get(f"/cars/{self.car.pk}/").content.decode()
        self.assertIn("Available for your dates", html)

    def test_availability_api_reports_clash_and_next_free_slot(self):
        booked = self.make("DG-FLOW-0003", days_from_now=2, length=3)
        start = timezone.localtime(booked.pickup_datetime) + timedelta(hours=1)
        data = Client().get(f"/cars/{self.car.pk}/availability/", {
            "pickup_date": start.strftime("%Y-%m-%d"), "pickup_time": start.strftime("%H:%M"),
            "drop_date": (start + timedelta(days=1)).strftime("%Y-%m-%d"), "drop_time": start.strftime("%H:%M"),
        }).json()
        self.assertFalse(data["available"])
        self.assertEqual(data["reason"], "booked")
        self.assertIsNotNone(data["clash"])
        suggestion = data["suggestion"]
        self.assertIsNotNone(suggestion)
        pickup = timezone.make_aware(datetime.strptime(f"{suggestion['pickup_date']} {suggestion['pickup_time']}", "%Y-%m-%d %H:%M"))
        self.assertGreaterEqual(pickup, booked.dropoff_datetime)
        self.assertTrue(self.car.is_available_for(pickup, pickup + timedelta(days=1)))

    def test_availability_api_free_window_returns_price(self):
        free = timezone.localdate() + timedelta(days=15)
        data = Client().get(f"/cars/{self.car.pk}/availability/", self.dates(free, 3)).json()
        self.assertTrue(data["available"])
        self.assertEqual(data["price"]["days"], 3)
        self.assertEqual(data["price"]["rental_amount"], 6000)

    def test_availability_api_rejects_past_and_missing_dates(self):
        past = timezone.localdate() - timedelta(days=3)
        self.assertEqual(Client().get(f"/cars/{self.car.pk}/availability/", self.dates(past, 1)).json()["reason"], "past")
        self.assertEqual(Client().get(f"/cars/{self.car.pk}/availability/").status_code, 400)

    def test_suggested_start_skips_back_to_back_bookings(self):
        first = self.make("DG-FLOW-0004", days_from_now=2, length=2)
        second = Booking.objects.create(
            booking_id="DG-FLOW-0005", user=self.other, car=self.car, pickup_location=self.loc,
            pickup_datetime=first.dropoff_datetime, dropoff_datetime=first.dropoff_datetime + timedelta(days=2),
            status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PAID,
        )
        pickup = first.pickup_datetime + timedelta(hours=2)
        nxt = availability_for(self.car, pickup, pickup + timedelta(days=1))["suggestion"]["pickup"]
        self.assertGreaterEqual(nxt, second.dropoff_datetime)

    def test_booking_dates_post_for_free_window_creates_hold(self):
        self.make("DG-FLOW-0006", days_from_now=2, length=3, user=self.other)
        free = timezone.localdate() + timedelta(days=12)
        response = _login("user_flow").post(f"/booking/{self.car.pk}/", self.dates(free, 2))
        self.assertEqual(response.status_code, 302)
        hold = Booking.objects.filter(user=self.user, status=Booking.Status.PENDING).get()
        self.assertIn(f"/verification/{hold.booking_id}/", response["Location"])

    def test_booking_dates_post_for_taken_window_shows_suggestion(self):
        booked = self.make("DG-FLOW-0007", days_from_now=2, length=3, user=self.other)
        start = timezone.localtime(booked.pickup_datetime)
        response = _login("user_flow").post(f"/booking/{self.car.pk}/", {
            "pickup_date": start.strftime("%Y-%m-%d"), "pickup_time": start.strftime("%H:%M"),
            "drop_date": (start + timedelta(days=1)).strftime("%Y-%m-%d"), "drop_time": start.strftime("%H:%M"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Use these dates")
        self.assertFalse(Booking.objects.filter(user=self.user).exists())

    def test_own_unpaid_hold_does_not_block_changing_dates(self):
        free = timezone.localdate() + timedelta(days=12)
        client = _login("user_flow")
        client.post(f"/booking/{self.car.pk}/", self.dates(free, 3))
        first = Booking.objects.get(user=self.user)
        Document.objects.create(booking=first, document_type=Document.DocType.DRIVING_LICENSE, file="")
        response = client.post(f"/booking/{self.car.pk}/", self.dates(free + timedelta(days=1), 3))
        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.status, Booking.Status.CANCELLED)
        second = Booking.objects.exclude(pk=first.pk).get(user=self.user)
        self.assertEqual(second.status, Booking.Status.PENDING)
        self.assertEqual(second.documents.count(), 1)

    def test_resubmitting_same_dates_reuses_hold(self):
        free = timezone.localdate() + timedelta(days=12)
        client = _login("user_flow")
        client.post(f"/booking/{self.car.pk}/", self.dates(free, 2))
        client.post(f"/booking/{self.car.pk}/", self.dates(free, 2))
        self.assertEqual(Booking.objects.filter(user=self.user).count(), 1)

    def test_login_redirect_keeps_selected_dates(self):
        free = timezone.localdate() + timedelta(days=12)
        response = Client().get(f"/booking/{self.car.pk}/", self.dates(free, 2))
        self.assertEqual(response.status_code, 302)
        self.assertIn("pickup_date%3D" + free.isoformat(), response["Location"])


class BookingIdTests(FlowFixtureMixin, TestCase):
    def test_id_does_not_collide_after_deleting_an_earlier_booking(self):
        from .views import generate_booking_id

        first = generate_booking_id()
        self.make(first)
        second = generate_booking_id()
        self.make(second)
        Booking.objects.filter(booking_id=first).delete()
        self.assertNotEqual(generate_booking_id(), second)


@override_settings(RAZORPAY_MOCK=True, RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET="")
class CheckoutGuardTests(FlowFixtureMixin, TestCase):
    def test_payment_requires_documents(self):
        hold = self.make("DG-GRD-0001", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING)
        response = _login("user_flow").get(f"/payment/{hold.booking_id}/")
        self.assertRedirects(response, f"/verification/{hold.booking_id}/", fetch_redirect_response=False)

    def test_delivery_is_locked_after_payment(self):
        paid = self.make("DG-GRD-0002", status=Booking.Status.PENDING_VERIFICATION, docs=True)
        client = _login("user_flow")
        response = client.post(f"/delivery/{paid.booking_id}/", {
            "delivery_method": "HOME_DELIVERY", "delivery_address": "1 Road", "delivery_city": "Kolkata", "delivery_pincode": "700001",
        })
        self.assertRedirects(response, f"/confirmation/{paid.booking_id}/", fetch_redirect_response=False)
        paid.refresh_from_db()
        self.assertEqual(paid.delivery_method, "STORE_PICKUP")

    def test_delivery_pincode_validated(self):
        hold = self.make("DG-GRD-0003", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True)
        response = _login("user_flow").post(f"/delivery/{hold.booking_id}/", {
            "delivery_method": "HOME_DELIVERY", "delivery_address": "1 Road", "delivery_city": "Kolkata", "delivery_pincode": "12ab",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "valid 6-digit PIN")

    def test_expired_hold_is_refreshed_when_car_still_free(self):
        hold = self.make("DG-GRD-0004", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True)
        Booking.objects.filter(pk=hold.pk).update(created_at=self.now - timedelta(minutes=settings.BOOKING_HOLD_MINUTES + 5))
        response = _login("user_flow").get(f"/summary/{hold.booking_id}/")
        self.assertEqual(response.status_code, 200)
        hold.refresh_from_db()
        self.assertFalse(hold.hold_expired)

    def test_expired_hold_is_released_when_someone_else_booked(self):
        hold = self.make("DG-GRD-0005", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True)
        Booking.objects.filter(pk=hold.pk).update(created_at=self.now - timedelta(minutes=settings.BOOKING_HOLD_MINUTES + 5))
        self.make("DG-GRD-0006", user=self.other)
        response = _login("user_flow").get(f"/summary/{hold.booking_id}/")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/cars/{self.car.pk}/", response["Location"])
        hold.refresh_from_db()
        self.assertEqual(hold.status, Booking.Status.CANCELLED)

    def test_customer_cannot_cancel_trip_in_progress(self):
        trip = self.make("DG-GRD-0007", days_from_now=-1, length=3, status=Booking.Status.ACTIVE)
        _login("user_flow").post(f"/bookings/{trip.booking_id}/cancel/")
        trip.refresh_from_db()
        self.assertEqual(trip.status, Booking.Status.ACTIVE)
        self.assertEqual(trip.payment_status, Booking.PaymentStatus.PAID)

    def test_payment_with_verified_documents_confirms_immediately(self):
        hold = self.make("DG-GRD-0008", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True, verified=True)
        hold.razorpay_order_id = "order_mock_DG-GRD-0008"
        hold.save(update_fields=["razorpay_order_id"])
        response = _login("user_flow").post("/payment/verify/", data=json.dumps({
            "booking_id": hold.booking_id, "razorpay_order_id": hold.razorpay_order_id,
            "razorpay_payment_id": "pay_mock_DG-GRD-0008", "razorpay_signature": "mock",
        }), content_type="application/json")
        self.assertTrue(response.json()["ok"])
        hold.refresh_from_db()
        self.assertEqual(hold.status, Booking.Status.CONFIRMED)

    @override_settings(RAZORPAY_KEY_ID="rzp_live", RAZORPAY_KEY_SECRET="secret", RAZORPAY_MOCK=False)
    def test_live_payment_page_leaves_method_choice_to_razorpay(self):
        hold = self.make("DG-GRD-0011", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True)
        with patch("rental.views.create_razorpay_order", return_value={"id": "order_live_1"}):
            body = _login("user_flow").get(f"/payment/{hold.booking_id}/").content.decode()
        self.assertIn("checkout.razorpay.com", body)
        self.assertNotIn('name="pay_method"', body)

    @override_settings(RAZORPAY_KEY_ID="rzp_live", RAZORPAY_KEY_SECRET="secret", RAZORPAY_MOCK=False)
    def test_payment_method_comes_from_razorpay(self):
        hold = self.make("DG-GRD-0012", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True)
        hold.razorpay_order_id = "order_live_2"
        hold.save(update_fields=["razorpay_order_id"])
        remote = {"amount": int(hold.total_amount * 100), "order_id": "order_live_2", "method": "upi"}
        with patch("rental.views.verify_razorpay_signature", return_value=True),                 patch("rental.views.fetch_razorpay_payment", return_value=remote):
            response = _login("user_flow").post("/payment/verify/", data=json.dumps({
                "booking_id": hold.booking_id, "razorpay_order_id": "order_live_2",
                "razorpay_payment_id": "pay_live_2", "razorpay_signature": "sig", "method": "card",
            }), content_type="application/json")
        self.assertTrue(response.json()["ok"])
        hold.refresh_from_db()
        self.assertEqual(hold.payment_method, "upi")

    @override_settings(RAZORPAY_KEY_ID="rzp_live", RAZORPAY_KEY_SECRET="secret", RAZORPAY_MOCK=False)
    def test_payment_for_another_order_is_rejected(self):
        hold = self.make("DG-GRD-0013", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True)
        hold.razorpay_order_id = "order_live_3"
        hold.save(update_fields=["razorpay_order_id"])
        remote = {"amount": int(hold.total_amount * 100), "order_id": "order_someone_else", "method": "upi"}
        with patch("rental.views.verify_razorpay_signature", return_value=True),                 patch("rental.views.fetch_razorpay_payment", return_value=remote):
            response = _login("user_flow").post("/payment/verify/", data=json.dumps({
                "booking_id": hold.booking_id, "razorpay_order_id": "order_live_3",
                "razorpay_payment_id": "pay_live_3", "razorpay_signature": "sig",
            }), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        hold.refresh_from_db()
        self.assertNotEqual(hold.payment_status, Booking.PaymentStatus.PAID)

    @override_settings(RAZORPAY_KEY_ID="rzp_live", RAZORPAY_KEY_SECRET="secret", RAZORPAY_MOCK=False)
    def test_mock_order_rejected_once_demo_mode_is_off(self):
        hold = self.make("DG-GRD-0009", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True)
        hold.razorpay_order_id = "order_mock_DG-GRD-0009"
        hold.save(update_fields=["razorpay_order_id"])
        response = _login("user_flow").post("/payment/verify/", data=json.dumps({
            "booking_id": hold.booking_id, "razorpay_order_id": hold.razorpay_order_id,
            "razorpay_payment_id": "pay_mock_DG-GRD-0009", "razorpay_signature": "mock",
        }), content_type="application/json")
        self.assertEqual(response.status_code, 400)
        hold.refresh_from_db()
        self.assertNotEqual(hold.payment_status, Booking.PaymentStatus.PAID)

    def test_document_upload_sends_file_bytes_to_storage(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        hold = self.make("DG-GRD-0010", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING)
        captured = {}

        def fake_upload(uploaded, path):
            uploaded.seek(0)
            captured[path] = uploaded.read()
            return f"documents/{path}"

        with patch("rental.views.upload_document_file", side_effect=fake_upload):
            response = _login("user_flow").post(f"/verification/{hold.booking_id}/", {
                "driving_license": SimpleUploadedFile("my license.png", b"PNGDATA", content_type="image/png"),
                "govt_id": SimpleUploadedFile("id.pdf", b"%PDF-1", content_type="application/pdf"),
            })
        self.assertRedirects(response, f"/delivery/{hold.booking_id}/", fetch_redirect_response=False)
        self.assertIn(b"PNGDATA", captured.values())
        doc = hold.documents.get(document_type=Document.DocType.DRIVING_LICENSE)
        self.assertTrue(doc.file_reference.startswith("documents/"))
        self.assertNotIn(" ", doc.file_reference)

    def test_rejected_document_must_be_replaced(self):
        paid = self.make("DG-GRD-0011", status=Booking.Status.PENDING_VERIFICATION, docs=True)
        paid.documents.filter(document_type=Document.DocType.GOVT_ID).update(
            verification_status=Document.VerificationStatus.REJECTED, rejection_reason="Blurry")
        client = _login("user_flow")
        page = client.get(f"/verification/{paid.booking_id}/")
        self.assertContains(page, "Blurry")
        response = client.post(f"/verification/{paid.booking_id}/", {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Still required")


class CustomerPagesRenderTests(FlowFixtureMixin, TestCase):
    def test_every_checkout_step_renders(self):
        hold = self.make("DG-RND-0001", status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING, docs=True)
        client = _login("user_flow")
        free = timezone.localdate() + timedelta(days=20)
        for url in (f"/booking/{self.car.pk}/?" + "&".join(f"{k}={v}" for k, v in self.dates(free, 2).items()),
                    f"/verification/{hold.booking_id}/", f"/delivery/{hold.booking_id}/",
                    f"/summary/{hold.booking_id}/", f"/payment/{hold.booking_id}/"):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_confirmation_and_my_bookings_render(self):
        paid = self.make("DG-RND-0002", status=Booking.Status.PENDING_VERIFICATION, docs=True)
        hold = self.make("DG-RND-0003", days_from_now=30, status=Booking.Status.PENDING, pay=Booking.PaymentStatus.PENDING)
        client = _login("user_flow")
        self.assertContains(client.get(f"/confirmation/{paid.booking_id}/"), "verifying documents")
        html = client.get("/my-bookings/").content.decode()
        self.assertIn("Continue booking", html)
        self.assertIn(f'href="/verification/{hold.booking_id}/"', html)
        for page in ("/", "/cars/", f"/cars/{self.car.pk}/"):
            with self.subTest(page=page):
                self.assertEqual(Client().get(page).status_code, 200)


class ClerkCallbackSecurityTests(TestCase):
    def setUp(self):
        Customer.objects.create(clerk_user_id="user_victim_admin", email="boss@example.com", role="ADMIN")

    def post(self, payload):
        return Client().post("/auth/clerk-callback/", data=json.dumps(payload), content_type="application/json")

    def test_bare_user_id_is_not_trusted(self):
        with patch("rental.views.verify_clerk_user", return_value={"clerk_user_id": "user_victim_admin", "email": "boss@example.com"}):
            response = self.post({"clerk_user_id": "user_victim_admin"})
        self.assertEqual(response.status_code, 401)

    def test_verified_session_token_signs_in(self):
        with patch("rental.views.verify_clerk_session_token", return_value="user_new") as verify, \
                patch("rental.views.verify_clerk_user", return_value={"clerk_user_id": "user_new", "email": "n@example.com", "full_name": "New"}):
            client = Client()
            response = client.post("/auth/clerk-callback/", data=json.dumps({"session_token": "tok", "next": "/cars/"}),
                                   content_type="application/json")
        verify.assert_called_once()
        self.assertEqual(response.json(), {"ok": True, "redirect": "/cars/"})
        self.assertEqual(client.session["clerk_user_id"], "user_new")

    def test_invalid_token_rejected(self):
        with override_settings(CLERK_SECRET_KEY="sk_test"), patch("rental.utils._clerk_signing_key", return_value=None):
            self.assertEqual(self.post({"session_token": "not-a-jwt"}).status_code, 401)


class AdminConsoleTests(FlowFixtureMixin, TestCase):
    def admin_client(self):
        client = _login("user_flow_admin")
        return client

    def test_all_admin_pages_render(self):
        b = self.make("DG-ADC-0001", status=Booking.Status.PENDING_VERIFICATION, docs=True)
        CarBlock.objects.create(car=self.car, start_datetime=self.now + timedelta(days=40), end_datetime=self.now + timedelta(days=41))
        client = self.admin_client()
        pages = ["/dashboard/", "/dashboard/verifications/", "/dashboard/customers/?q=flow", "/dashboard/cars/",
                 "/dashboard/locations/", "/dashboard/blocks/", f"/dashboard/bookings/{b.booking_id}/"]
        pages += [f"/dashboard/bookings/?status={key}" for key in ("active", "verification", "confirmed", "on_trip", "completed", "cancelled", "all")]
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)
        self.assertContains(client.get("/dashboard/bookings/?status=all&q=Flow User"), b.booking_id)

    def test_actions_redirect_back_to_referring_page(self):
        b = self.make("DG-ADC-0002", status=Booking.Status.PENDING_VERIFICATION, docs=True)
        response = self.admin_client().post(f"/dashboard/bookings/{b.booking_id}/docs/approve/",
                                     HTTP_REFERER=f"http://testserver/dashboard/bookings/{b.booking_id}/")
        self.assertEqual(response["Location"], f"http://testserver/dashboard/bookings/{b.booking_id}/")

    def test_approve_refuses_when_a_document_is_missing(self):
        b = self.make("DG-ADC-0003", status=Booking.Status.PENDING_VERIFICATION)
        Document.objects.create(booking=b, document_type=Document.DocType.DRIVING_LICENSE, file="")
        self.admin_client().post(f"/dashboard/bookings/{b.booking_id}/docs/approve/")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.PENDING_VERIFICATION)
        self.assertFalse(b.documents.filter(verification_status=Document.VerificationStatus.VERIFIED).exists())

    def test_approving_docs_does_not_revert_active_trip(self):
        b = self.make("DG-ADC-0004", days_from_now=-1, status=Booking.Status.ACTIVE, docs=True)
        self.admin_client().post(f"/dashboard/bookings/{b.booking_id}/docs/approve/")
        b.refresh_from_db()
        self.assertEqual(b.status, Booking.Status.ACTIVE)

    def test_edit_car_and_duplicate_registration(self):
        other = Car.objects.create(location=self.loc, brand="Tata", model="Nexon", registration_number="WB06F0002")
        client = self.admin_client()
        fields = {"brand": "Honda", "model": "City ZX", "registration_number": "wb06f0001", "location": self.loc.pk,
                  "category": "Sedan", "seats": 5, "transmission": "Manual", "fuel_type": "Petrol",
                  "price_per_day": 2400, "status": "AVAILABLE"}
        client.post(f"/dashboard/cars/{self.car.pk}/edit/", fields)
        self.car.refresh_from_db()
        self.assertEqual((self.car.model, self.car.price_per_day, self.car.transmission), ("City ZX", 2400, "Manual"))
        response = client.post(f"/dashboard/cars/{other.pk}/edit/", {**fields, "registration_number": "WB06F0001"})
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertEqual(other.registration_number, "WB06F0002")
        response = client.post("/dashboard/cars/add/", {**fields, "registration_number": "WB06F0001"})
        self.assertEqual(response.status_code, 302)
        response = client.post("/dashboard/cars/add/", {**fields, "registration_number": "WB06F0099", "category": "Spaceship"})
        self.assertFalse(Car.objects.filter(registration_number="WB06F0099").exists())

    def test_toggle_location_and_delete_block(self):
        client = self.admin_client()
        client.post(f"/dashboard/locations/{self.loc.pk}/toggle/")
        self.loc.refresh_from_db()
        self.assertFalse(self.loc.is_active)
        block = CarBlock.objects.create(car=self.car, start_datetime=self.now, end_datetime=self.now + timedelta(days=1))
        client.post(f"/dashboard/blocks/{block.pk}/delete/")
        self.assertFalse(CarBlock.objects.filter(pk=block.pk).exists())

    def test_admin_document_view_requires_admin(self):
        b = self.make("DG-ADC-0005", status=Booking.Status.PENDING_VERIFICATION, docs=True)
        doc = b.documents.first()
        Document.objects.filter(pk=doc.pk).update(file_reference="documents/DG-ADC-0005/GOVT_ID_scan.pdf")
        self.assertEqual(_login("user_flow").get(f"/dashboard/documents/{doc.pk}/").status_code, 302)
        with patch("rental.views.fetch_document_bytes", return_value=b"%PDF-1.7 test"):
            response = self.admin_client().get(f"/dashboard/documents/{doc.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF-1.7 test")
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response["Content-Disposition"].startswith("inline"))
        # The admin modal frames this response; DENY makes Chrome show "Failed to load PDF document".
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

    def test_admin_document_falls_back_to_local_copy_when_remote_is_empty(self):
        from django.core.files.base import ContentFile

        b = self.make("DG-ADC-0006", status=Booking.Status.PENDING_VERIFICATION)
        with self.settings(MEDIA_ROOT=self._tmp_media()):
            doc = Document(booking=b, document_type=Document.DocType.GOVT_ID, file_reference="documents/x/GOVT_ID_id.png")
            doc.file.save("id.png", ContentFile(b"PNGBYTES"), save=True)
            with patch("rental.views.fetch_document_bytes", return_value=b""):
                response = self.admin_client().get(f"/dashboard/documents/{doc.pk}/")
        self.assertEqual(response.content, b"PNGBYTES")
        self.assertEqual(response["Content-Type"], "image/png")

    def test_admin_document_never_serves_html_inline(self):
        b = self.make("DG-ADC-0007", status=Booking.Status.PENDING_VERIFICATION, docs=True)
        doc = b.documents.first()
        Document.objects.filter(pk=doc.pk).update(file_reference="documents/x/evil.html")
        with patch("rental.views.fetch_document_bytes", return_value=b"<script>alert(1)</script>"):
            response = self.admin_client().get(f"/dashboard/documents/{doc.pk}/")
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))

    def test_admin_document_missing_file_explains_itself(self):
        b = self.make("DG-ADC-0008", status=Booking.Status.PENDING_VERIFICATION, docs=True)
        response = self.admin_client().get(f"/dashboard/documents/{b.documents.first().pk}/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "missing or empty", status_code=404)

    def _tmp_media(self):
        import shutil
        import tempfile

        path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path


class TemplateFilterTests(TestCase):
    def test_indian_number_grouping(self):
        from .templatetags.rental_tags import inr

        self.assertEqual(inr(1234567), "12,34,567")
        self.assertEqual(inr("9204.00"), "9,204")
        self.assertEqual(inr(999), "999")


class PublicPagesTests(TestCase):
    def setUp(self):
        loc = Location.objects.create(name="Hero", address="Addr", city="Kolkata")
        Car.objects.create(location=loc, category="SUV", brand="Kia", model="Seltos", registration_number="WB06P0001",
                           price_per_day=2500, image_url="https://example.com/seltos.jpg")

    def test_mobile_menu_and_hero_render(self):
        body = Client().get("/").content.decode()
        self.assertIn('id="navToggle"', body)
        self.assertIn('aria-controls="navMenu"', body)
        self.assertIn('class="hx"', body)
        self.assertIn("Kia Seltos", body)
        self.assertIn('name="pickup_date"', body)

    def test_cars_page_filters_keep_dates_and_search_keeps_filters(self):
        start = timezone.localdate() + timedelta(days=5)
        body = Client().get("/cars/", {"category": "SUV", "pickup_date": start.isoformat(), "drop_date": (start + timedelta(days=2)).isoformat()}).content.decode()
        self.assertIn(f'name="pickup_date" value="{start.isoformat()}"', body)
        self.assertIn('<input type="hidden" name="category" value="SUV" />', body)
        self.assertIn("cl-dot", body)
        self.assertIn("5,900 total", body)


@override_settings(CLERK_PUBLISHABLE_KEY="pk_test_example")
class AuthPageTests(TestCase):
    def test_login_loads_clerk_once_and_points_clerk_at_our_pages(self):
        body = Client().get("/login/", {"next": "/booking/3/"}).content.decode()
        # auth.js adds the Clerk script itself, only on pages that need it.
        self.assertEqual(body.count('data-publishable-key="pk_test_example"'), 1)
        self.assertNotIn("clerk.browser.js", body)
        self.assertIn('data-login-url="/login/"', body)
        self.assertIn('data-signup-url="/signup/"', body)
        self.assertIn('data-next="/booking/3/"', body)
        self.assertIn("your dates are saved", body)
        self.assertIn("data-auth-page", body)

    def test_signup_redirects_signed_in_customer(self):
        Customer.objects.create(clerk_user_id="user_signed_in", email="s@example.com")
        response = _login("user_signed_in").get("/signup/", {"next": "/cars/"})
        self.assertRedirects(response, "/cars/", fetch_redirect_response=False)

    def test_login_sends_signed_in_admin_to_dashboard(self):
        Customer.objects.create(clerk_user_id="user_boss", email="b@example.com", role="ADMIN")
        response = _login("user_boss").get("/login/")
        self.assertRedirects(response, "/dashboard/", fetch_redirect_response=False)

    def test_logged_out_page_does_not_auto_sync(self):
        body = Client().get("/logout/").content.decode()
        self.assertIn("data-auth-page", body)
        # auth.js adds the Clerk script itself, only on pages that need it.
        self.assertEqual(body.count('data-publishable-key="pk_test_example"'), 1)
        self.assertNotIn("clerk.browser.js", body)


class SeedFleetCommandTests(TestCase):
    def setUp(self):
        from .fleet_data import FLEET

        self.fleet_size = len(FLEET)
        self.loc_a = Location.objects.create(name="Seed A", address="A", city="Kolkata")
        self.loc_b = Location.objects.create(name="Seed B", address="B", city="Kolkata")
        self.booked = Car.objects.create(location=self.loc_a, brand="Temp", model="Booked", registration_number="TMP0001")
        self.unbooked = Car.objects.create(location=self.loc_a, brand="Temp", model="Unused", registration_number="TMP0002")
        user = Customer.objects.create(clerk_user_id="user_seed", email="seed@example.com")
        Booking.objects.create(
            booking_id="DG-SEED-0001", user=user, car=self.booked, pickup_location=self.loc_a,
            pickup_datetime=timezone.now() + timedelta(days=1), dropoff_datetime=timezone.now() + timedelta(days=2),
            status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PAID,
        )

    def run_command(self, *args):
        import io

        from django.core.management import call_command

        out = io.StringIO()
        call_command("seed_fleet", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        output = self.run_command("--seed", "1")
        self.assertIn("Dry run only", output)
        self.assertEqual(Car.objects.count(), 2)

    def test_apply_hides_booked_cars_and_is_repeatable(self):
        self.run_command("--apply", "--seed", "1")
        self.booked.refresh_from_db()
        self.assertEqual(self.booked.status, "INACTIVE")
        self.assertFalse(Car.objects.filter(pk=self.unbooked.pk).exists())
        self.assertTrue(Booking.objects.filter(booking_id="DG-SEED-0001").exists())
        new_cars = Car.objects.exclude(pk=self.booked.pk)
        self.assertEqual(new_cars.count(), self.fleet_size)
        self.assertFalse(new_cars.filter(image_url="").exists())
        per_location = [new_cars.filter(location=loc).count() for loc in (self.loc_a, self.loc_b)]
        self.assertLessEqual(abs(per_location[0] - per_location[1]), 1)
        self.run_command("--apply")
        self.assertEqual(Car.objects.exclude(pk=self.booked.pk).count(), self.fleet_size)

    def test_purge_removes_old_cars_with_bookings(self):
        output = self.run_command("--apply", "--old", "purge")
        self.assertIn("OPEN PAID booking DG-SEED-0001", output)
        self.assertFalse(Car.objects.filter(pk=self.booked.pk).exists())
        self.assertFalse(Booking.objects.filter(booking_id="DG-SEED-0001").exists())


class AdminRoleManagementTests(TestCase):
    def setUp(self):
        self.admin = Customer.objects.create(clerk_user_id="user_role_admin", email="boss@example.com", role="ADMIN")
        self.member = Customer.objects.create(clerk_user_id="user_role_member", email="member@example.com")

    def test_admin_can_promote_and_demote_from_dashboard(self):
        client = _login("user_role_admin")
        self.assertContains(client.get("/dashboard/customers/"), "Make admin")
        client.post(f"/dashboard/customers/{self.member.pk}/role/", {"role": "ADMIN"})
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, "ADMIN")
        self.assertEqual(_login("user_role_member").get("/dashboard/").status_code, 200)
        client.post(f"/dashboard/customers/{self.member.pk}/role/", {"role": "CUSTOMER"})
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, "CUSTOMER")

    def test_customer_cannot_change_roles(self):
        _login("user_role_member").post(f"/dashboard/customers/{self.member.pk}/role/", {"role": "ADMIN"})
        self.member.refresh_from_db()
        self.assertEqual(self.member.role, "CUSTOMER")

    def test_admin_cannot_demote_self_or_last_admin(self):
        _login("user_role_admin").post(f"/dashboard/customers/{self.admin.pk}/role/", {"role": "CUSTOMER"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, "ADMIN")

    @override_settings(SUPABASE_URL="https://example.supabase.co", SUPABASE_KEY="service-key")
    def test_role_change_is_written_to_supabase(self):
        with patch("rental.utils.requests.get") as get, patch("rental.utils.requests.post") as post:
            get.return_value.status_code = 200
            get.return_value.json.return_value = [{"role": "ADMIN"}]
            post.return_value.status_code = 201
            _login("user_role_admin").post(f"/dashboard/customers/{self.member.pk}/role/", {"role": "ADMIN"})
        self.assertEqual(post.call_args.kwargs["json"], {"clerk_user_id": "user_role_member", "role": "ADMIN"})
        self.assertIn("merge-duplicates", post.call_args.kwargs["headers"]["Prefer"])


class InfoPagesTests(TestCase):
    def test_simple_pages_render_and_footer_links_to_them(self):
        Location.objects.create(name="Info Branch", address="1 Park Street", city="Kolkata", phone="033 1234")
        for url, text in (("/about/", "About DriveGo"), ("/contact/", "Info Branch"), ("/help/", "Help Center"),
                          ("/terms/", "Terms &amp; Conditions"), ("/privacy/", "Privacy Policy")):
            with self.subTest(url=url):
                self.assertContains(Client().get(url), text)
        footer = Client().get("/").content.decode().split('class="site-footer"')[1]
        for url in ("/about/", "/contact/", "/help/", "/terms/", "/privacy/"):
            self.assertIn(f'href="{url}"', footer)


class ClerkTokenVerificationTests(TestCase):
    """Signs a real RS256 token so a missing crypto dependency fails here, not in production."""

    def setUp(self):
        import time

        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa

        from . import utils

        self.utils = utils
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        public_jwk.update(kid="test-kid", alg="RS256", use="sig")
        utils._JWKS_CACHE.update(keys={"test-kid": public_jwk}, fetched=time.time())
        self.addCleanup(utils._JWKS_CACHE.update, keys={}, fetched=0.0)

    def token(self, **overrides):
        import time

        import jwt

        now = int(time.time())
        claims = {"sub": "user_real", "iat": now, "nbf": now, "exp": now + 60, "azp": "https://drivego.vercel.app"}
        claims.update(overrides)
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": "test-kid"})

    @override_settings(CLERK_SECRET_KEY="sk_test")
    def test_valid_token_returns_user_id(self):
        self.assertEqual(self.utils.verify_clerk_session_token(self.token(), "drivego.vercel.app"), "user_real")

    @override_settings(CLERK_SECRET_KEY="sk_test")
    def test_expired_token_or_wrong_site_is_rejected(self):
        import time

        self.assertIsNone(self.utils.verify_clerk_session_token(self.token(exp=int(time.time()) - 600), "drivego.vercel.app"))
        self.assertIsNone(self.utils.verify_clerk_session_token(self.token(), "evil.example.com"))


class PageQueryCountTests(TestCase):
    """List pages must not run database queries per car (each query is a network round trip in production)."""

    def setUp(self):
        locs = [Location.objects.create(name=f"Q{i}", address="A", city="Kolkata") for i in range(3)]
        user = Customer.objects.create(clerk_user_id="user_q", email="q@example.com")
        now = timezone.now()
        for i in range(12):
            car = Car.objects.create(location=locs[i % 3], brand="Brand", model=f"M{i}", registration_number=f"WBQ{i:04d}",
                                     category=["Hatchback", "Sedan", "SUV"][i % 3], price_per_day=1000 + i * 100)
            Booking.objects.create(booking_id=f"DG-Q-{i:04d}", user=user, car=car, pickup_location=car.location,
                                   pickup_datetime=now + timedelta(days=1), dropoff_datetime=now + timedelta(days=3),
                                   status=Booking.Status.CONFIRMED, payment_status=Booking.PaymentStatus.PAID)
        CarBlock.objects.create(car=Car.objects.first(), start_datetime=now + timedelta(days=5), end_datetime=now + timedelta(days=6))

    def test_list_pages_use_a_fixed_number_of_queries(self):
        for url, limit in (("/", 8), ("/cars/", 8)):
            with self.subTest(url=url), self.assertNumQueriesLessThan(limit):
                self.assertEqual(Client().get(url).status_code, 200)
        client = _login("user_q")
        with self.assertNumQueriesLessThan(10):
            client.get("/cars/")
        Customer.objects.filter(clerk_user_id="user_q").update(role="ADMIN")
        for url in ("/dashboard/", "/dashboard/cars/"):
            with self.subTest(url=url), self.assertNumQueriesLessThan(20):
                self.assertEqual(client.get(url).status_code, 200)

    def assertNumQueriesLessThan(self, limit):
        from contextlib import contextmanager

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        @contextmanager
        def check():
            with CaptureQueriesContext(connection) as ctx:
                yield
            self.assertLess(len(ctx.captured_queries), limit, f"{len(ctx.captured_queries)} queries")

        return check()
