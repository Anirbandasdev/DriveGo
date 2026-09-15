import json
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .forms import BookingDatesForm, DeliveryForm, DocumentUploadForm, SearchForm
from .models import Booking, Car, CarBlock, Customer, Document, Location, SiteSetting
from .utils import (
    create_razorpay_order,
    get_user_role,
    send_booking_confirmation_email,
    upload_document_file,
    verify_clerk_user,
    verify_razorpay_signature,
)


def get_customer(request):
    clerk_user_id = request.session.get("clerk_user_id")
    if not clerk_user_id:
        return None
    return Customer.objects.filter(clerk_user_id=clerk_user_id).first()


def customer_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if get_customer(request) is None:
            messages.info(request, "Please log in to continue your booking.")
            return redirect(f"{reverse('login')}?next={request.path}")
        return view(request, *args, **kwargs)

    return wrapper


def clerk_admin_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        customer = get_customer(request)
        if customer is None:
            messages.info(request, "Please log in to continue your booking.")
            return redirect(f"{reverse('login')}?next={request.path}")
        if get_user_role(customer.clerk_user_id) != "ADMIN":
            messages.error(request, "Admin access only.")
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapper


def owned_booking(request, booking_id):
    customer = get_customer(request)
    if customer is None:
        return None
    return Booking.objects.filter(booking_id=booking_id, user=customer).select_related("car", "pickup_location", "user").first()


def generate_booking_id():
    today = timezone.localdate()
    prefix = f"DG-{today:%Y%m%d}-"
    seq = Booking.objects.filter(booking_id__startswith=prefix).count() + 1
    return f"{prefix}{seq:04d}"


def parse_search_datetimes(data):
    try:
        pickup_date = datetime.strptime(data.get("pickup_date", ""), "%Y-%m-%d").date()
        drop_date = datetime.strptime(data.get("drop_date", ""), "%Y-%m-%d").date()
        pickup_time = datetime.strptime(data.get("pickup_time", "10:00"), "%H:%M").time()
        drop_time = datetime.strptime(data.get("drop_time", "18:00"), "%H:%M").time()
    except (ValueError, TypeError):
        return None, None
    return (
        timezone.make_aware(datetime.combine(pickup_date, pickup_time)),
        timezone.make_aware(datetime.combine(drop_date, drop_time)),
    )


def default_dates():
    today = timezone.localdate()
    return (today + timedelta(days=1)).isoformat(), (today + timedelta(days=4)).isoformat()


def home(request):
    locations = Location.objects.filter(is_active=True).order_by("name")
    popular_cars = [
        {"car": car, "status": "available" if car.is_listed_available() else "unavailable"}
        for car in Car.objects.filter(status="AVAILABLE").select_related("location")[:4]
    ]
    default_pickup_date, default_drop_date = default_dates()
    return render(request, "index.html", {
        "locations": locations,
        "popular_cars": popular_cars,
        "default_pickup_date": default_pickup_date,
        "default_drop_date": default_drop_date,
        "fleet_count": Car.objects.filter(status="AVAILABLE").count(),
        "location_count": locations.count(),
        "trips_count": Booking.objects.filter(status=Booking.Status.COMPLETED).count(),
    })


def car_list(request):
    form = SearchForm(request.GET or None)
    params = request.GET.copy()
    cars = Car.objects.select_related("location").order_by("brand", "model")
    location_name = (params.get("location") or "").strip()
    if location_name:
        cars = cars.filter(location__name__iexact=location_name)
    category = (params.get("category") or params.get("type") or "").strip()
    if category and category != "All":
        cars = cars.filter(category__iexact=category)
    if params.get("max_price"):
        try:
            cars = cars.filter(price_per_day__lte=int(params["max_price"]))
        except ValueError:
            pass
    if params.get("seats") and params["seats"] != "Any":
        try:
            cars = cars.filter(seats__gte=int(params["seats"]))
        except ValueError:
            pass
    if params.get("transmission") and params["transmission"] != "Any":
        cars = cars.filter(transmission__iexact=params["transmission"])
    if params.get("fuel_type") and params["fuel_type"] != "Any":
        cars = cars.filter(fuel_type__iexact=params["fuel_type"])
    sort = params.get("sort", "Recommended")
    if sort == "Low":
        cars = cars.order_by("price_per_day")
    elif sort == "High":
        cars = cars.order_by("-price_per_day")

    pickup, drop = parse_search_datetimes(params)
    dated = pickup and drop and drop > pickup
    results = []
    for car in cars:
        status, booked_slot, next_free = "available", None, None
        if car.status != "AVAILABLE":
            status = "unavailable"
        elif dated:
            if car.is_available_for(pickup, drop):
                status = "available"
            else:
                status = "unavailable"
                clash = car.blocking_bookings(pickup, drop).order_by("pickup_datetime").first()
                if clash:
                    booked_slot = {"from": clash.pickup_datetime, "to": clash.dropoff_datetime}
                nxt = car.next_free_after(pickup, drop)
                next_free = nxt
        elif not car.is_listed_available():
            status = "unavailable"
            upcoming = car.upcoming_bookings().order_by("pickup_datetime").first()
            if upcoming:
                booked_slot = {"from": upcoming.pickup_datetime, "to": upcoming.dropoff_datetime}
                next_free = upcoming.dropoff_datetime
        results.append({"car": car, "status": status, "booked_slot": booked_slot, "next_free": next_free})
    locations = Location.objects.filter(is_active=True).order_by("name")
    default_pickup_date, default_drop_date = default_dates()
    return render(request, "cars.html", {
        "results": results, "locations": locations, "params": params, "form": form,
        "pickup": pickup, "drop": drop, "dated": dated,
        "default_pickup_date": default_pickup_date, "default_drop_date": default_drop_date,
    })


def car_detail(request, car_id):
    car = get_object_or_404(Car.objects.select_related("location"), pk=car_id)
    pickup, drop = parse_search_datetimes(request.GET)
    dated = pickup and drop and drop > pickup
    available, booked_slot, next_free = True, None, None
    if car.status != "AVAILABLE":
        available = False
    elif dated:
        available = car.is_available_for(pickup, drop)
        if not available:
            clash = car.blocking_bookings(pickup, drop).order_by("pickup_datetime").first()
            if clash:
                booked_slot = {"from": clash.pickup_datetime, "to": clash.dropoff_datetime}
            next_free = car.next_free_after(pickup, drop)
    else:
        available = car.is_listed_available()
        if not available:
            upcoming = car.upcoming_bookings().order_by("pickup_datetime").first()
            if upcoming:
                booked_slot = {"from": upcoming.pickup_datetime, "to": upcoming.dropoff_datetime}
                next_free = upcoming.dropoff_datetime
    locations = Location.objects.filter(is_active=True).order_by("name")
    default_pickup_date, default_drop_date = default_dates()
    return render(request, "car-details.html", {
        "car": car, "locations": locations, "pickup": pickup, "drop": drop,
        "dated": dated, "available": available, "booked_slot": booked_slot, "next_free": next_free,
        "default_pickup_date": default_pickup_date, "default_drop_date": default_drop_date,
    })


@customer_required
def booking_dates(request, car_id):
    car = get_object_or_404(Car, pk=car_id)
    initial = {}
    for key in ("pickup_date", "pickup_time", "drop_date", "drop_time"):
        if request.GET.get(key):
            initial[key] = request.GET[key]
    if request.method == "POST":
        form = BookingDatesForm(request.POST)
        if form.is_valid():
            pickup, drop = form.cleaned_datetimes()
            location = car.location
            try:
                with transaction.atomic():
                    car_locked = Car.objects.select_for_update().get(pk=car.pk)
                    if not car_locked.is_available_for(pickup, drop):
                        messages.error(request, "This car was just booked for the selected period. Please try different dates.")
                        return redirect("car_detail", car_id=car.pk)
                    booking = Booking(
                        user=get_customer(request),
                        car=car_locked,
                        pickup_location=location,
                        pickup_datetime=pickup,
                        dropoff_datetime=drop,
                    )
                    booking.apply_price()
                    for _ in range(5):
                        booking.booking_id = generate_booking_id()
                        booking.pk = None
                        try:
                            with transaction.atomic():
                                booking.save()
                            break
                        except IntegrityError:
                            continue
                    else:
                        messages.error(request, "Could not create a booking right now. Please try again.")
                        return redirect("car_detail", car_id=car.pk)
            except Car.DoesNotExist:
                messages.error(request, "Car not found.")
                return redirect("car_list")
            return redirect("verification", booking_id=booking.booking_id)
    else:
        form = BookingDatesForm(initial=initial)
    return render(request, "booking.html", {"car": car, "form": form})


@customer_required
def verification(request, booking_id):
    booking = owned_booking(request, booking_id)
    if booking is None:
        return redirect("my_bookings")
    field_map = {"dl_front": Document.DocType.LICENSE_FRONT, "dl_back": Document.DocType.LICENSE_BACK, "govt_id": Document.DocType.GOVT_ID}
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            for field, doc_type in field_map.items():
                uploaded = form.cleaned_data.get(field)
                if not uploaded:
                    continue
                doc, _ = Document.objects.update_or_create(
                    booking=booking, document_type=doc_type,
                    defaults={"file": uploaded, "verification_status": Document.VerificationStatus.PENDING,
                              "verified_at": None, "rejection_reason": ""},
                )
                remote_path = f"{booking.booking_id}/{doc_type}_{uploaded.name}"
                ref = upload_document_file(uploaded, remote_path)
                if ref:
                    doc.file_reference = ref
                    doc.save(update_fields=["file_reference"])
            missing = form.required_missing(booking)
            if missing:
                messages.error(request, "Saved. Still required: " + ", ".join(missing))
            else:
                messages.success(request, "Documents uploaded.")
                return redirect("delivery", booking_id=booking.booking_id)
    else:
        form = DocumentUploadForm()
    have = set(booking.documents.values_list("document_type", flat=True))
    return render(request, "verification.html", {"booking": booking, "form": form, "have": have, "doc_types": Document.DocType})


@customer_required
def delivery(request, booking_id):
    booking = owned_booking(request, booking_id)
    if booking is None:
        return redirect("my_bookings")
    if request.method == "POST":
        form = DeliveryForm(request.POST)
        if form.is_valid():
            booking.delivery_method = form.cleaned_data["delivery_method"]
            booking.delivery_address = form.cleaned_data["delivery_address"]
            booking.delivery_city = form.cleaned_data["delivery_city"]
            booking.delivery_pincode = form.cleaned_data["delivery_pincode"]
            booking.delivery_instructions = form.cleaned_data["delivery_instructions"]
            booking.apply_price()
            booking.save()
            return redirect("summary", booking_id=booking.booking_id)
    else:
        form = DeliveryForm(initial={
            "delivery_method": booking.delivery_method,
            "delivery_address": booking.delivery_address,
            "delivery_city": booking.delivery_city,
            "delivery_pincode": booking.delivery_pincode,
            "delivery_instructions": booking.delivery_instructions,
        })
    return render(request, "delivery.html", {"booking": booking, "form": form})


@customer_required
def summary(request, booking_id):
    booking = owned_booking(request, booking_id)
    if booking is None:
        return redirect("my_bookings")
    return render(request, "summary.html", {"booking": booking})


@customer_required
def payment(request, booking_id):
    from django.conf import settings as dj_settings

    booking = owned_booking(request, booking_id)
    if booking is None:
        return redirect("my_bookings")
    if booking.payment_status == Booking.PaymentStatus.PAID:
        return redirect("confirmation", booking_id=booking.booking_id)
    if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED):
        messages.error(request, "This booking was cancelled and can no longer be paid.")
        return redirect("my_bookings")
    demo_mode = SiteSetting.get("demo_mode", "off") == "on"
    if not booking.razorpay_order_id:
        order_data = create_razorpay_order(booking, force_mock=demo_mode)
        booking.razorpay_order_id = order_data["id"]
        booking.save(update_fields=["razorpay_order_id"])
    return render(request, "payment.html", {
        "booking": booking, "razorpay_key_id": dj_settings.RAZORPAY_KEY_ID,
        "is_mock": (booking.razorpay_order_id or "").startswith("order_mock_"),
    })


@customer_required
@require_POST
def payment_verify(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)
    booking = owned_booking(request, payload.get("booking_id", ""))
    if booking is None:
        return JsonResponse({"ok": False, "error": "Booking not found."}, status=404)
    if booking.payment_status == Booking.PaymentStatus.PAID:
        return JsonResponse({"ok": True, "redirect": reverse("confirmation", kwargs={"booking_id": booking.booking_id})})
    if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED):
        return JsonResponse({"ok": False, "error": "This booking was cancelled."}, status=409)
    order_id, payment_id, signature = payload.get("razorpay_order_id", ""), payload.get("razorpay_payment_id", ""), payload.get("razorpay_signature", "")
    if not order_id or order_id != booking.razorpay_order_id:
        return JsonResponse({"ok": False, "error": "Order not found."}, status=404)
    if not verify_razorpay_signature(order_id, payment_id, signature or f"pay_mock_{booking.booking_id}"):
        booking.payment_status = Booking.PaymentStatus.FAILED
        booking.save(update_fields=["payment_status"])
        return JsonResponse({"ok": False, "error": "Payment verification failed."}, status=400)
    if not order_id.startswith("order_mock_"):
        from .utils import razorpay_client

        remote = razorpay_client().order.fetch(order_id)
        if int(remote.get("amount", 0)) != int(booking.total_amount * 100):
            booking.payment_status = Booking.PaymentStatus.FAILED
            booking.save(update_fields=["payment_status"])
            return JsonResponse({"ok": False, "error": "Amount mismatch."}, status=400)
    with transaction.atomic():
        car_locked = Car.objects.select_for_update().get(pk=booking.car_id)
        clash = Booking.overlaps(car_locked, booking.pickup_datetime, booking.dropoff_datetime, exclude_id=booking.pk)
        if clash.exists() or car_locked.status != "AVAILABLE":
            booking.payment_status = Booking.PaymentStatus.FAILED
            booking.save(update_fields=["payment_status"])
            return JsonResponse({"ok": False, "error": "Car is no longer available for these dates. Refund initiated."}, status=409)
        booking.payment_status = Booking.PaymentStatus.PAID
        booking.razorpay_payment_id = payment_id
        booking.payment_method = payload.get("method", "")
        booking.status = Booking.Status.PENDING_VERIFICATION
        booking.save(update_fields=["payment_status", "razorpay_payment_id", "payment_method", "status"])
    send_booking_confirmation_email(booking)
    return JsonResponse({"ok": True, "redirect": reverse("confirmation", kwargs={"booking_id": booking.booking_id})})


@customer_required
def payment_failure(request, booking_id):
    booking = owned_booking(request, booking_id)
    if booking is None:
        return redirect("my_bookings")
    if booking.payment_status != Booking.PaymentStatus.PAID:
        booking.payment_status = Booking.PaymentStatus.FAILED
        booking.save(update_fields=["payment_status"])
    messages.error(request, "Payment failed. Your booking is still reserved as unpaid - please try again.")
    return redirect("payment", booking_id=booking.booking_id)


@customer_required
def confirmation(request, booking_id):
    booking = owned_booking(request, booking_id)
    if booking is None:
        return redirect("my_bookings")
    if booking.payment_status != Booking.PaymentStatus.PAID:
        if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED):
            messages.error(request, "This booking was cancelled.")
            return redirect("my_bookings")
        messages.info(request, "Complete the payment to confirm this booking.")
        return redirect("payment", booking_id=booking.booking_id)
    rejected_docs = [d for d in booking.documents.all() if d.verification_status == Document.VerificationStatus.REJECTED]
    return render(request, "confirmation.html", {"booking": booking, "rejected_docs": rejected_docs})


@customer_required
@require_POST
def cancel_booking(request, booking_id):
    booking = owned_booking(request, booking_id)
    if booking is None:
        return redirect("my_bookings")
    if booking.status not in (Booking.Status.PENDING, Booking.Status.PENDING_VERIFICATION, Booking.Status.CONFIRMED, Booking.Status.ACTIVE):
        messages.error(request, "This booking can no longer be cancelled.")
        return redirect("my_bookings")
    booking.status = Booking.Status.CANCELLED
    refunded = booking.payment_status == Booking.PaymentStatus.PAID
    if refunded:
        booking.payment_status = Booking.PaymentStatus.REFUNDED
    booking.save(update_fields=["status", "payment_status"])
    messages.success(request, "Booking cancelled." + (" Refund initiated for the paid amount." if refunded else ""))
    return redirect("my_bookings")


@customer_required
def my_bookings(request):
    customer = get_customer(request)
    tab = request.GET.get("tab", "upcoming")
    base = Booking.objects.filter(user=customer).select_related("car", "pickup_location")
    groups = {
        "upcoming": base.filter(status__in=[Booking.Status.PENDING, Booking.Status.PENDING_VERIFICATION, Booking.Status.CONFIRMED, Booking.Status.ACTIVE]),
        "completed": base.filter(status=Booking.Status.COMPLETED),
        "cancelled": base.filter(status__in=[Booking.Status.CANCELLED, Booking.Status.REJECTED]),
    }
    counts = {key: qs.count() for key, qs in groups.items()}
    bookings = groups.get(tab, groups["upcoming"])
    return render(request, "my-bookings.html", {
        "bookings": bookings,
        "tab": tab,
        "counts": counts,
        "customer": customer,
        "total_trips": customer.bookings.count(),
    })


@customer_required
def profile(request):
    return redirect("my_bookings")


def _safe_next(request, candidate):
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return ""


@ensure_csrf_cookie
def login_view(request):
    from django.conf import settings as dj_settings

    next_url = _safe_next(request, request.GET.get("next", ""))
    if get_customer(request):
        return redirect(next_url or "my_bookings")
    return render(request, "login.html", {
        "clerk_key": dj_settings.CLERK_PUBLISHABLE_KEY,
        "next": next_url,
    })


@ensure_csrf_cookie
def signup_view(request):
    from django.conf import settings as dj_settings

    return render(request, "signup.html", {
        "clerk_key": dj_settings.CLERK_PUBLISHABLE_KEY,
    })


@require_POST
def clerk_callback(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)
    clerk_user_id = (payload.get("clerk_user_id") or "").strip()
    if not clerk_user_id:
        return JsonResponse({"ok": False, "error": "Missing user id."}, status=400)
    profile = verify_clerk_user(clerk_user_id)
    if profile is None:
        return JsonResponse({"ok": False, "error": "Could not verify Clerk session."}, status=401)
    customer, _ = Customer.objects.update_or_create(
        clerk_user_id=profile["clerk_user_id"],
        defaults={"email": profile.get("email", ""), "full_name": profile.get("full_name", ""),
                  "profile_image_url": profile.get("profile_image_url", "")},
    )
    role = get_user_role(customer.clerk_user_id)
    if role == "ADMIN" and customer.role != "ADMIN":
        customer.role = "ADMIN"
        customer.save(update_fields=["role"])
    request.session["clerk_user_id"] = customer.clerk_user_id
    request.session["role"] = role
    default_next = reverse("admin_dashboard") if role == "ADMIN" else reverse("my_bookings")
    next_url = _safe_next(request, request.POST.get("next") or payload.get("next") or "")
    return JsonResponse({"ok": True, "redirect": next_url or default_next})


def logout_view(request):
    from django.conf import settings as dj_settings

    request.session.flush()
    return render(request, "logged_out.html", {"clerk_key": dj_settings.CLERK_PUBLISHABLE_KEY})


def _admin_verification_count():
    return Booking.objects.filter(status__in=[Booking.Status.PENDING, Booking.Status.PENDING_VERIFICATION]).count()


@clerk_admin_required
def admin_dashboard(request):
    from django.conf import settings as dj_settings

    total_cars = Car.objects.count()
    available_cars = Car.objects.filter(status="AVAILABLE").count()
    active_bookings = Booking.objects.filter(status__in=[Booking.Status.PENDING, Booking.Status.PENDING_VERIFICATION, Booking.Status.CONFIRMED, Booking.Status.ACTIVE]).count()
    revenue = Booking.objects.filter(payment_status=Booking.PaymentStatus.PAID).aggregate(total=Sum("total_amount"))["total"] or 0
    recent = Booking.objects.select_related("user", "car", "pickup_location").order_by("-created_at")[:8]
    availability = {
        "Available": Car.objects.filter(status="AVAILABLE").count(),
        "Maintenance": Car.objects.filter(status="MAINTENANCE").count(),
        "Inactive": Car.objects.filter(status="INACTIVE").count(),
    }
    return render(request, "admin_dashboard.html", {
        "active": "overview",
        "total_cars": total_cars, "available_cars": available_cars, "active_bookings": active_bookings,
        "revenue": revenue, "recent": recent, "availability": availability,
        "demo_mode": SiteSetting.get("demo_mode", "off") == "on",
        "razorpay_configured": bool(dj_settings.RAZORPAY_KEY_ID and dj_settings.RAZORPAY_KEY_SECRET),
        "verification_count": _admin_verification_count(),
    })


@clerk_admin_required
def admin_verifications(request):
    verification_queue = (
        Booking.objects.filter(status__in=[Booking.Status.PENDING, Booking.Status.PENDING_VERIFICATION])
        .select_related("user", "car", "pickup_location")
        .prefetch_related("documents")
        .order_by("-created_at")[:12]
    )
    return render(request, "admin_verifications.html", {
        "active": "verifications",
        "verification_queue": verification_queue,
        "verification_count": _admin_verification_count(),
    })


@clerk_admin_required
def admin_bookings(request):
    bookings = (
        Booking.objects.select_related("user", "car", "pickup_location")
        .order_by("-created_at")
    )
    return render(request, "admin_bookings.html", {
        "active": "bookings",
        "bookings_list": bookings,
        "verification_count": _admin_verification_count(),
    })


@clerk_admin_required
def admin_customers(request):
    customers = []
    for customer in Customer.objects.order_by("-created_at"):
        customers.append({
            "customer": customer,
            "trips": Booking.objects.filter(user=customer, status=Booking.Status.COMPLETED).count(),
            "bookings_total": customer.bookings.count(),
        })
    return render(request, "admin_customers.html", {
        "active": "customers",
        "customers": customers,
        "verification_count": _admin_verification_count(),
    })


@clerk_admin_required
def admin_cars(request):
    from django.conf import settings as dj_settings

    availability = {
        "Available": Car.objects.filter(status="AVAILABLE").count(),
        "Maintenance": Car.objects.filter(status="MAINTENANCE").count(),
        "Inactive": Car.objects.filter(status="INACTIVE").count(),
    }
    all_cars = Car.objects.select_related("location").order_by("brand", "model")
    all_locations = Location.objects.all().order_by("name")
    return render(request, "admin_cars.html", {
        "active": "cars",
        "availability": availability,
        "all_cars": all_cars,
        "all_locations": all_locations,
        "verification_count": _admin_verification_count(),
    })


@clerk_admin_required
def admin_locations(request):
    now = timezone.now()
    locations = []
    for loc in Location.objects.filter(is_active=True).order_by("name"):
        cars = loc.cars.count()
        booked = Booking.objects.filter(pickup_location=loc, status__in=[Booking.Status.PENDING_VERIFICATION, Booking.Status.CONFIRMED, Booking.Status.ACTIVE], pickup_datetime__lte=now, dropoff_datetime__gt=now).count()
        locations.append({"location": loc, "cars": cars, "booked": booked, "bookings": Booking.objects.filter(pickup_location=loc).count()})
    all_locations = Location.objects.all().order_by("name")
    return render(request, "admin_locations.html", {
        "active": "locations",
        "locations": locations,
        "all_locations": all_locations,
        "verification_count": _admin_verification_count(),
    })


@clerk_admin_required
def admin_blocks(request):
    recent_blocks = CarBlock.objects.select_related("car").order_by("-created_at")[:8]
    all_cars = Car.objects.all().order_by("brand", "model")
    return render(request, "admin_blocks.html", {
        "active": "blocks",
        "recent_blocks": recent_blocks,
        "all_cars": all_cars,
        "verification_count": _admin_verification_count(),
    })


@clerk_admin_required
@require_POST
def admin_demo_toggle(request):
    currently_on = SiteSetting.get("demo_mode", "off") == "on"
    SiteSetting.set("demo_mode", "off" if currently_on else "on")
    messages.success(request, "Demo mode turned OFF. Real payments required." if currently_on else "Demo mode turned ON. Payments will be simulated.")
    return redirect("admin_dashboard")


def _admin_booking(booking_id):
    return Booking.objects.select_related("user", "car", "pickup_location").filter(booking_id=booking_id).first()


@clerk_admin_required
@require_POST
def admin_cancel_booking(request, booking_id):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return redirect("admin_dashboard")
    if booking.status in (Booking.Status.COMPLETED, Booking.Status.CANCELLED, Booking.Status.REJECTED):
        messages.error(request, "This booking can no longer be cancelled.")
        return redirect("admin_dashboard")
    booking.status = Booking.Status.CANCELLED
    refunded = booking.payment_status == Booking.PaymentStatus.PAID
    if refunded:
        booking.payment_status = Booking.PaymentStatus.REFUNDED
    booking.save(update_fields=["status", "payment_status"])
    messages.success(request, f"Booking {booking_id} cancelled." + (" Full refund marked." if refunded else ""))
    return redirect("admin_dashboard")


@clerk_admin_required
@require_POST
def admin_approve_docs(request, booking_id):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return redirect("admin_dashboard")
    if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED, Booking.Status.COMPLETED):
        messages.error(request, "This booking cannot be approved.")
        return redirect("admin_dashboard")
    docs = booking.documents.exclude(verification_status=Document.VerificationStatus.VERIFIED)
    if docs.exists():
        from django.db.models import F

        docs.update(verification_status=Document.VerificationStatus.VERIFIED, verified_at=timezone.now(), rejection_reason="")
    was_confirmed_on_approve = False
    if booking.payment_status == Booking.PaymentStatus.PAID and booking.status != Booking.Status.CONFIRMED:
        booking.status = Booking.Status.CONFIRMED
        booking.save(update_fields=["status"])
        was_confirmed_on_approve = True
        send_booking_confirmation_email(booking)
    messages.success(
        request,
        "Documents verified. Booking confirmed." if was_confirmed_on_approve
        else "Documents verified. Will confirm once payment is received.",
    )
    return redirect("admin_dashboard")


@clerk_admin_required
@require_POST
def admin_reject_docs(request, booking_id):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return redirect("admin_dashboard")
    if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED, Booking.Status.COMPLETED):
        messages.error(request, "This booking cannot be rejected.")
        return redirect("admin_dashboard")
    reason = (request.POST.get("reason") or "").strip()
    if not reason:
        messages.error(request, "Please provide a reason for rejection.")
        return redirect("admin_dashboard")
    booking.documents.exclude(verification_status=Document.VerificationStatus.VERIFIED).update(
        verification_status=Document.VerificationStatus.REJECTED,
        rejection_reason=reason,
        verified_at=None,
    )
    messages.success(request, "Documents rejected. Customer will be asked to re-upload.")
    return redirect("admin_dashboard")


@clerk_admin_required
@require_POST
def admin_trip_action(request, booking_id, action):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return redirect("admin_dashboard")
    if action == "start":
        if booking.status != Booking.Status.CONFIRMED:
            messages.error(request, "Only confirmed bookings can start a trip.")
        else:
            booking.status = Booking.Status.ACTIVE
            booking.save(update_fields=["status"])
            messages.success(request, "Trip started.")
    elif action == "complete":
        if booking.status not in (Booking.Status.CONFIRMED, Booking.Status.ACTIVE):
            messages.error(request, "Only active or confirmed bookings can be completed.")
        else:
            booking.status = Booking.Status.COMPLETED
            booking.save(update_fields=["status"])
            messages.success(request, "Trip completed.")
    else:
        messages.error(request, "Unknown action.")
    return redirect("admin_dashboard")


@clerk_admin_required
@require_POST
def admin_set_car_status(request, car_id):
    car = Car.objects.filter(pk=car_id).first()
    status = request.POST.get("status", "")
    valid = dict(Car.STATUS_CHOICES)
    if car is None:
        messages.error(request, "Car not found.")
    elif status not in valid:
        messages.error(request, "Invalid status.")
    else:
        car.status = status
        car.save(update_fields=["status"])
        messages.success(request, f"{car} is now {valid[status]}.")
    return redirect("admin_dashboard")


@clerk_admin_required
@require_POST
def admin_add_car(request):
    def _val(key, default=""):
        return (request.POST.get(key) or default).strip()

    brand, model = _val("brand"), _val("model")
    registration = _val("registration_number")
    location = Location.objects.filter(pk=request.POST.get("location") or 0).first()
    if not brand or not model or not registration or location is None:
        messages.error(request, "Brand, model, registration number and location are required.")
        return redirect("admin_dashboard")
    try:
        price = int(_val("price_per_day", "1500"))
        if price < 1:
            raise ValueError
    except ValueError:
        messages.error(request, "Invalid daily price.")
        return redirect("admin_dashboard")
    try:
        seats = max(int(_val("seats", "5")), 1)
    except ValueError:
        seats = 5
    car = Car.objects.create(
        location=location, category=_val("category", "SUV"), brand=brand, model=model,
        registration_number=registration, seats=seats,
        transmission=_val("transmission", "Manual"), fuel_type=_val("fuel_type", "Petrol"),
        price_per_day=price, image_url=_val("image_url"),
        features=_val("features", "AC, 5 Doors"), status=_val("status", "AVAILABLE"),
    )
    messages.success(request, f"Car {car} added.")
    return redirect("admin_dashboard")


@clerk_admin_required
@require_POST
def admin_add_location(request):
    def _val(key, default=""):
        return (request.POST.get(key) or default).strip()

    name = _val("name")
    if not name:
        messages.error(request, "Location name is required.")
        return redirect("admin_dashboard")
    try:
        loc = Location.objects.create(
            name=name, address=_val("address"), city=_val("city", "Kolkata"),
            phone=_val("phone"), image_url=_val("image_url"),
        )
    except IntegrityError:
        messages.error(request, "A location with that name already exists.")
        return redirect("admin_dashboard")
    messages.success(request, f"Location {loc.name} added.")
    return redirect("admin_dashboard")


def _parse_dt(value):
    if not value:
        return None
    value = value.strip().replace("T", " ")
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return timezone.make_aware(dt)


@clerk_admin_required
@require_POST
def admin_block_dates(request):
    car = Car.objects.filter(pk=request.POST.get("car_id") or 0).first()
    start = _parse_dt(request.POST.get("start_datetime"))
    end = _parse_dt(request.POST.get("end_datetime"))
    if car is None or start is None or end is None or end <= start:
        messages.error(request, "Pick a car and a valid start / end period.")
        return redirect("admin_dashboard")
    CarBlock.objects.create(car=car, start_datetime=start, end_datetime=end, note=(request.POST.get("note") or "").strip())
    messages.success(request, f"{car} blocked {start:%d %b %H:%M} - {end:%d %b %H:%M}.")
    return redirect("admin_dashboard")


@clerk_admin_required
@require_POST
def admin_set_note(request, booking_id):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return redirect("admin_dashboard")
    booking.admin_note = (request.POST.get("note") or "").strip()
    booking.save(update_fields=["admin_note"])
    messages.success(request, f"Note saved for {booking_id}.")
    return redirect("admin_dashboard")
