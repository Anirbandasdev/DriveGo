from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import Booking, Car, CarBlock, Customer, Document, Location, SiteSetting


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
        self.assertNotIn("Admin</a>", body.split("nav-links")[1].split("</nav>")[0])

    def test_navbar_shows_admin_link_for_admins(self):
        body = self.login_as("user_admin", "ADMIN").get("/").content.decode()
        self.assertIn('href="/dashboard/"', body)

    def test_anonymous_navbar_shows_login(self):
        body = Client().get("/").content.decode()
        self.assertIn('href="/login/" class="btn btn-ghost">Login', body)


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
        return client

    def test_unpaid_confirmation_redirects_to_payment(self):
        response = self.login().get("/confirmation/DG-GUARD-1/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/payment/DG-GUARD-1/", response["Location"])

    def test_stale_session_role_still_enforces_db_role(self):
        admin = Customer.objects.create(clerk_user_id="user_fresh_admin", email="fa@example.com", role="ADMIN")
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_fresh_admin"
        session["role"] = "CUSTOMER"
        session.save()
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

    def test_future_confirmed_booking_hides_car_from_listing(self):
        self.book_future(days=1)
        self.assertFalse(self.car.is_listed_available())
        self.assertTrue(self.car.is_available_for(self.now, self.now + timedelta(hours=1)))
        self.assertFalse(self.car.is_available_for(self.now + timedelta(days=1), self.now + timedelta(days=3)))

    def test_unbooked_car_is_listed_available(self):
        self.assertTrue(self.car.is_listed_available())

    def test_cars_page_no_dates_shows_booked_badge(self):
        self.book_future(days=1)
        html = Client().get("/cars/").content.decode()
        block = html.split('class="car-card"')[1]
        self.assertIn("car-badge no", block)
        self.assertIn("Booked", block)
        self.assertIn("car-booked-chip", block)

    def test_completed_booking_does_not_hide_car(self):
        self.book_future(days=-5)
        Booking.objects.filter(booking_id="DG-LIST-0001").update(status=Booking.Status.COMPLETED)
        self.assertTrue(self.car.is_listed_available())


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
        return client

    def make(self, booking_id, status=Booking.Status.CONFIRMED, pay=Booking.PaymentStatus.PAID, user_id="user_cancel"):
        user = Customer.objects.get(clerk_user_id=user_id)
        b = Booking.objects.create(
            booking_id=booking_id, user=user, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=2), dropoff_datetime=self.now + timedelta(days=4),
            status=status, payment_status=pay)
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
        self.assertTrue(self.car.is_listed_available())

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

    def test_future_block_hides_car_from_listing(self):
        CarBlock.objects.create(
            car=self.car,
            start_datetime=self.now + timedelta(days=2),
            end_datetime=self.now + timedelta(days=4),
        )
        self.assertFalse(self.car.is_listed_available())

    def test_admin_can_add_block(self):
        admin = Customer.objects.create(clerk_user_id="user_ablk", email="blk@example.com", role="ADMIN")
        client = Client()
        session = client.session
        session["clerk_user_id"] = "user_ablk"
        session["role"] = "ADMIN"
        session.save()
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
        return client

    def make(self, bid, status=Booking.Status.PENDING_VERIFICATION, pay=Booking.PaymentStatus.PAID):
        b = Booking.objects.create(
            booking_id=bid, user=self.user, car=self.car, pickup_location=self.loc,
            pickup_datetime=self.now + timedelta(days=3), dropoff_datetime=self.now + timedelta(days=5),
            status=status, payment_status=pay,
        )
        b.apply_price()
        b.save()
        return b

    def add_doc(self, b, verified=False):
        return Document.objects.create(
            booking=b, document_type=Document.DocType.LICENSE_FRONT, file="",
            verification_status=Document.VerificationStatus.VERIFIED if verified else Document.VerificationStatus.PENDING,
        )

    def test_admin_approve_docs_confirms_paid_booking(self):
        b = self.make("DG-ADM-0001")
        doc = self.add_doc(b)
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
        self.assertTrue(self.car.is_listed_available())

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
