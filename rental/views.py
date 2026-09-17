import json
from datetime import datetime, time, timedelta
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import get_valid_filename
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .forms import MAX_RENTAL_DAYS, BookingDatesForm, DeliveryForm, DocumentUploadForm
from .models import Booking, Car, CarBlock, Customer, Document, Location, SiteSetting
from .utils import (
    create_razorpay_order,
    fetch_document_bytes,
    fetch_razorpay_order,
    get_user_role,
    process_refund,
    send_booking_confirmation_email,
    set_user_role,
    upload_document_file,
    verify_clerk_session_token,
    verify_clerk_user,
    verify_razorpay_signature,
)

DEFAULT_PICKUP_TIME = "10:00"
DEFAULT_DROP_TIME = "18:00"


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_customer(request):
    clerk_user_id = request.session.get("clerk_user_id")
    if not clerk_user_id:
        return None
    return Customer.objects.filter(clerk_user_id=clerk_user_id).first()


def _login_redirect(request):
    return redirect(f"{reverse('login')}?{urlencode({'next': request.get_full_path()})}")


def customer_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if get_customer(request) is None:
            messages.info(request, "Please log in to continue your booking.")
            return _login_redirect(request)
        return view(request, *args, **kwargs)

    return wrapper


def clerk_admin_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        customer = get_customer(request)
        if customer is None:
            messages.info(request, "Please log in to open the admin console.")
            return _login_redirect(request)
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


def generate_booking_id(offset=0):
    """Next ``DG-YYYYMMDD-NNNN`` id for today.

    Based on the highest existing sequence rather than a row count, so deleting
    old holds (see ``cleanup_pending_bookings``) can never hand out a used id.
    """
    prefix = f"DG-{timezone.localdate():%Y%m%d}-"
    last = (
        Booking.objects.filter(booking_id__startswith=prefix)
        .order_by("-booking_id").values_list("booking_id", flat=True).first()
    )
    try:
        seq = int(last.rsplit("-", 1)[1]) if last else 0
    except ValueError:
        seq = Booking.objects.filter(booking_id__startswith=prefix).count()
    return f"{prefix}{seq + 1 + offset:04d}"


def _pending_cutoff():
    return timezone.now() - timedelta(minutes=getattr(settings, "BOOKING_HOLD_MINUTES", 60))


def _no_longer_active_message(booking):
    if booking.status == Booking.Status.REJECTED:
        return "This booking was rejected."
    if booking.status == Booking.Status.COMPLETED:
        return "This trip is already completed."
    return "This booking was cancelled."


def _safe_next(request, candidate):
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return ""


# ---------------------------------------------------------------------------
# Dates & availability
# ---------------------------------------------------------------------------

def parse_search_datetimes(data):
    try:
        pickup_date = datetime.strptime(data.get("pickup_date", ""), "%Y-%m-%d").date()
        drop_date = datetime.strptime(data.get("drop_date", ""), "%Y-%m-%d").date()
        pickup_time = datetime.strptime(data.get("pickup_time") or DEFAULT_PICKUP_TIME, "%H:%M").time()
        drop_time = datetime.strptime(data.get("drop_time") or DEFAULT_DROP_TIME, "%H:%M").time()
    except (ValueError, TypeError):
        return None, None
    return (
        timezone.make_aware(datetime.combine(pickup_date, pickup_time)),
        timezone.make_aware(datetime.combine(drop_date, drop_time)),
    )


def default_dates():
    today = timezone.localdate()
    return (today + timedelta(days=1)).isoformat(), (today + timedelta(days=4)).isoformat()


def default_window():
    pickup_date, drop_date = default_dates()
    return parse_search_datetimes({"pickup_date": pickup_date, "drop_date": drop_date})


def resolve_window(data):
    """(pickup, drop, from_user) for the request, falling back to the default trip."""
    pickup, drop = parse_search_datetimes(data)
    if pickup and drop:
        return pickup, drop, True
    pickup, drop = default_window()
    return pickup, drop, False


def window_params(pickup, drop):
    pickup, drop = timezone.localtime(pickup), timezone.localtime(drop)
    return {
        "pickup_date": pickup.strftime("%Y-%m-%d"), "pickup_time": pickup.strftime("%H:%M"),
        "drop_date": drop.strftime("%Y-%m-%d"), "drop_time": drop.strftime("%H:%M"),
    }


def _fmt(dt):
    return timezone.localtime(dt).strftime("%d %b, %I:%M %p").lstrip("0")


def own_hold_ids(request, car):
    """The visitor's own unpaid holds on ``car``; they must never block the visitor."""
    customer = get_customer(request)
    if customer is None:
        return []
    return list(
        Booking.objects.filter(user=customer, car=car, status=Booking.Status.PENDING)
        .exclude(payment_status=Booking.PaymentStatus.PAID).values_list("pk", flat=True)
    )


def availability_for(car, pickup, drop, exclude_ids=None):
    """Everything the UI needs to explain whether ``car`` can be booked for a window."""
    info = {"available": False, "reason": "", "message": "", "clash": None, "suggestion": None}
    if car.status != "AVAILABLE":
        info.update(reason="out_of_service", message="This car is temporarily out of service.")
        return info
    if drop <= pickup:
        info.update(reason="invalid", message="Drop-off must be after pickup.")
        return info
    if pickup < timezone.now() - timedelta(minutes=10):
        info.update(reason="past", message="Pickup time must be in the future.")
        return info
    if drop - pickup > timedelta(days=MAX_RENTAL_DAYS):
        info.update(reason="too_long", message=f"Trips can be at most {MAX_RENTAL_DAYS} days long.")
        return info
    if car.is_available_for(pickup, drop, exclude_ids):
        info.update(available=True, reason="available", message="Available for your dates")
        return info
    periods = car.busy_periods(pickup, drop, exclude_ids)
    if periods:
        info["clash"] = {"from": periods[0][0], "to": periods[-1][1]}
    info.update(reason="booked", message="Already booked for part of these dates.")
    nxt = car.next_available_start(pickup, drop, exclude_ids)
    if nxt:
        info["suggestion"] = {"pickup": nxt, "drop": nxt + (drop - pickup), "params": window_params(nxt, nxt + (drop - pickup))}
    return info


def price_estimate(car, pickup, drop, delivery_method="STORE_PICKUP"):
    draft = Booking(car=car, pickup_datetime=pickup, dropoff_datetime=drop, delivery_method=delivery_method)
    return {"days": draft.rental_days, **draft.calculate_price()}


def availability_calendar(car, exclude_ids=None, weeks=6):
    """Month-style grid starting this week; each day is free / partial / full / past."""
    today = timezone.localdate()
    first = today - timedelta(days=today.weekday())
    start = timezone.make_aware(datetime.combine(first, time.min))
    end = start + timedelta(days=weeks * 7)
    periods = car.busy_periods(start, end, exclude_ids) if car.status == "AVAILABLE" else []
    cells = []
    for i in range(weeks * 7):
        day = first + timedelta(days=i)
        day_start = timezone.make_aware(datetime.combine(day, time.min))
        day_end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
        busy = sum(
            max((min(e, day_end) - max(s, day_start)).total_seconds(), 0) for s, e in periods
        )
        if day < today:
            state = "past"
        elif car.status != "AVAILABLE":
            state = "off"
        elif busy >= (day_end - day_start).total_seconds() - 60:
            state = "full"
        elif busy > 0:
            state = "partial"
        else:
            state = "free"
        cells.append({"date": day.isoformat(), "day": day.day, "month": day.strftime("%b"),
                      "first_of_month": day.day == 1 or i == 0, "state": state, "today": day == today})
    upcoming = [{"from": s, "to": e} for s, e in periods if e > timezone.now()]
    return {"cells": cells, "busy": upcoming[:6]}


def _availability_payload(car, pickup, drop, info):
    payload = {
        "ok": True,
        "available": info["available"],
        "reason": info["reason"],
        "message": info["message"],
        "pickup_label": _fmt(pickup),
        "drop_label": _fmt(drop),
        "clash": None,
        "suggestion": None,
        "price": None,
    }
    if info["clash"]:
        payload["clash"] = {"from_label": _fmt(info["clash"]["from"]), "to_label": _fmt(info["clash"]["to"])}
    if info["suggestion"]:
        sug = info["suggestion"]
        payload["suggestion"] = {**sug["params"], "label": f"{_fmt(sug['pickup'])} → {_fmt(sug['drop'])}"}
    if info["reason"] in ("available", "booked"):
        price = price_estimate(car, pickup, drop)
        payload["price"] = {k: int(v) for k, v in price.items()}
    return payload


@require_GET
def car_availability(request, car_id):
    car = get_object_or_404(Car, pk=car_id)
    pickup, drop = parse_search_datetimes(request.GET)
    if not (pickup and drop):
        return JsonResponse({"ok": False, "error": "Choose pickup and drop-off dates."}, status=400)
    info = availability_for(car, pickup, drop, own_hold_ids(request, car))
    return JsonResponse(_availability_payload(car, pickup, drop, info))


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

def home(request):
    in_service = Q(cars__status="AVAILABLE")
    locations = Location.objects.filter(is_active=True).annotate(car_count=Count("cars", filter=in_service)).order_by("name")
    pickup, drop = default_window()
    qs = urlencode(window_params(pickup, drop))
    fleet = Car.objects.filter(status="AVAILABLE", location__is_active=True).select_related("location")
    popular_cars = []
    for car in fleet.annotate(trips=Count("bookings")).order_by("-trips", "price_per_day")[:8]:
        popular_cars.append({"car": car, "info": availability_for(car, pickup, drop), "qs": qs,
                             "estimate": price_estimate(car, pickup, drop)})
    car_types = []
    for name, _ in Car.CATEGORY_CHOICES:
        cars = fleet.filter(category=name)
        cheapest = cars.order_by("price_per_day").first()
        if cheapest:
            car_types.append({"name": name, "count": cars.count(), "from_price": cheapest.price_per_day,
                              "image": cheapest.display_image})
    return render(request, "index.html", {
        "locations": locations,
        "popular_cars": popular_cars,
        "car_types": car_types,
        "featured_car": fleet.exclude(image_url="").order_by("-price_per_day").first(),
        "window": window_params(pickup, drop),
        "popular_qs": qs,
        "pickup": pickup, "drop": drop,
        "fleet_count": fleet.count(),
        "location_count": locations.count(),
        "trips_count": Booking.objects.filter(status=Booking.Status.COMPLETED).count(),
    })


def car_list(request):
    params = request.GET.copy()
    cars = Car.objects.select_related("location").exclude(status="INACTIVE").order_by("brand", "model")
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

    pickup, drop, dated = resolve_window(params)
    qs = urlencode(window_params(pickup, drop))
    results = []
    for car in cars:
        info = availability_for(car, pickup, drop, own_hold_ids(request, car))
        results.append({"car": car, "info": info, "qs": qs, "estimate": price_estimate(car, pickup, drop) if drop > pickup else None})
    if params.get("available_only") == "1":
        results = [r for r in results if r["info"]["available"]]
    if sort not in ("Low", "High"):
        results.sort(key=lambda r: not r["info"]["available"])

    locations = Location.objects.filter(is_active=True).order_by("name")
    price_ceiling = Car.objects.aggregate(m=Max("price_per_day"))["m"] or 1500
    max_price_default = int((price_ceiling + 499) // 500 * 500)
    window = window_params(pickup, drop)
    return render(request, "cars.html", {
        "results": results, "locations": locations, "params": params,
        "pickup": pickup, "drop": drop, "dated": dated, "window": window,
        "available_count": sum(1 for r in results if r["info"]["available"]),
        "categories": [c for c, _ in Car.CATEGORY_CHOICES],
        "seat_options": [("Any", "Any"), ("5", "5+"), ("7", "7+")],
        "keep_filters": [(key, params[key]) for key in ("category", "max_price", "seats", "transmission", "fuel_type", "sort", "available_only") if params.get(key)],
        "active_filters": sum(1 for key in ("category", "seats", "transmission", "fuel_type") if params.get(key) not in (None, "", "All", "Any"))
        + (1 if params.get("max_price") and params.get("max_price") != str(max_price_default) else 0)
        + (1 if params.get("available_only") == "1" else 0),
        "max_price_default": max_price_default,
    })


def car_detail(request, car_id):
    car = get_object_or_404(Car.objects.select_related("location"), pk=car_id)
    pickup, drop, dated = resolve_window(request.GET)
    exclude = own_hold_ids(request, car)
    info = availability_for(car, pickup, drop, exclude)
    return render(request, "car-details.html", {
        "car": car,
        "pickup": pickup, "drop": drop, "dated": dated,
        "window": window_params(pickup, drop),
        "info": info,
        "estimate": price_estimate(car, pickup, drop),
        "calendar": availability_calendar(car, exclude),
    })


# ---------------------------------------------------------------------------
# Checkout flow
# ---------------------------------------------------------------------------

def _expire_hold(request, booking):
    """Refresh a lapsed unpaid hold when possible; otherwise release it.

    Returns a redirect when the customer has to pick new dates, else ``None``.
    """
    if booking.status != Booking.Status.PENDING or booking.is_paid:
        return None
    past_pickup = booking.pickup_datetime < timezone.now() - timedelta(minutes=10)
    if not booking.hold_expired and not past_pickup:
        return None
    if not past_pickup:
        with transaction.atomic():
            car = Car.objects.select_for_update().get(pk=booking.car_id)
            if car.is_available_for(booking.pickup_datetime, booking.dropoff_datetime, exclude_ids=[booking.pk]):
                now = timezone.now()
                Booking.objects.filter(pk=booking.pk).update(created_at=now)
                booking.created_at = now
                return None
    Booking.objects.filter(pk=booking.pk).update(status=Booking.Status.CANCELLED)
    if past_pickup:
        messages.error(request, "That reservation's pickup time has passed. Choose new dates to book again.")
        return redirect("car_detail", car_id=booking.car_id)
    messages.error(request, "Your reservation expired and the car was booked by someone else for those dates. Here are the next free dates.")
    return redirect(f"{reverse('car_detail', args=[booking.car_id])}?{urlencode(window_params(booking.pickup_datetime, booking.dropoff_datetime))}")


def _checkout_booking(request, booking_id, step):
    """Load the customer's booking for a checkout step and enforce step order.

    Returns ``(booking, None)`` or ``(None, redirect_response)``.
    """
    booking = owned_booking(request, booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return None, redirect("my_bookings")
    if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED):
        messages.error(request, _no_longer_active_message(booking))
        return None, redirect("my_bookings")
    if booking.is_paid or booking.status == Booking.Status.COMPLETED:
        if step != "verification" or not booking.rejected_documents():
            return None, redirect("confirmation", booking_id=booking.booking_id)
        return booking, None
    expired = _expire_hold(request, booking)
    if expired:
        return None, expired
    if step in ("delivery", "summary", "payment"):
        if booking.missing_documents() or booking.rejected_documents():
            messages.info(request, "Upload your driving license and government ID to continue.")
            return None, redirect("verification", booking_id=booking.booking_id)
    return booking, None


CHECKOUT_STEPS = [("dates", "Dates"), ("verification", "Documents"), ("delivery", "Delivery"), ("summary", "Review"), ("payment", "Payment")]


def checkout_steps(current, car, booking=None):
    keys = [key for key, _ in CHECKOUT_STEPS]
    index = keys.index(current)
    steps = []
    for i, (key, label) in enumerate(CHECKOUT_STEPS):
        url = ""
        if i < index:
            if key == "dates":
                window = window_params(booking.pickup_datetime, booking.dropoff_datetime) if booking else {}
                url = f"{reverse('booking_dates', args=[car.pk])}?{urlencode(window)}"
            elif booking:
                url = reverse(key, args=[booking.booking_id])
        steps.append({"key": key, "label": label, "n": i + 1, "url": url,
                      "state": "done" if i < index else "now" if i == index else "todo"})
    return steps


def _checkout_context(booking, step, **extra):
    return {"booking": booking, "car": booking.car, "step": step,
            "steps": checkout_steps(step, booking.car, booking), **extra}


@customer_required
def booking_dates(request, car_id):
    car = get_object_or_404(Car.objects.select_related("location"), pk=car_id)
    customer = get_customer(request)
    exclude = own_hold_ids(request, car)
    if request.method == "POST":
        form = BookingDatesForm(request.POST)
        if form.is_valid():
            pickup, drop = form.cleaned_datetimes()
            same = Booking.objects.filter(
                pk__in=exclude, pickup_datetime=pickup, dropoff_datetime=drop, created_at__gte=_pending_cutoff()
            ).first()
            if same:
                return redirect(same.checkout_step, booking_id=same.booking_id)
            booking = None
            with transaction.atomic():
                car_locked = Car.objects.select_for_update().get(pk=car.pk)
                info = availability_for(car_locked, pickup, drop, exclude)
                if info["available"]:
                    # Replace the visitor's own overlapping unpaid holds instead of stacking them,
                    # carrying over what they already filled in (documents, delivery choice).
                    replaced = list(Booking.objects.filter(
                        pk__in=exclude, pickup_datetime__lt=drop, dropoff_datetime__gt=pickup
                    ).order_by("-created_at"))
                    previous = replaced[0] if replaced else None
                    booking = Booking(user=customer, car=car_locked, pickup_location=car_locked.location,
                                      pickup_datetime=pickup, dropoff_datetime=drop)
                    if previous:
                        for field in ("delivery_method", "delivery_address", "delivery_city", "delivery_pincode", "delivery_instructions"):
                            setattr(booking, field, getattr(previous, field))
                    booking.apply_price()
                    for attempt in range(5):
                        booking.booking_id = generate_booking_id(offset=attempt)
                        booking.pk = None
                        try:
                            with transaction.atomic():
                                booking.save()
                            break
                        except IntegrityError:
                            continue
                    else:
                        booking = None
                        form.add_error(None, "Could not create a booking right now. Please try again.")
                    if booking is not None and replaced:
                        Booking.objects.filter(pk__in=[b.pk for b in replaced]).update(status=Booking.Status.CANCELLED)
                        Document.objects.filter(booking=previous).update(booking=booking)
            if booking is not None:
                return redirect(booking.checkout_step, booking_id=booking.booking_id)
            if not form.errors:
                form.add_error(None, info["message"] + (" Pick one of the suggested dates below." if info["suggestion"] else ""))
        pickup, drop = parse_search_datetimes(request.POST)
    else:
        pickup, drop, _ = resolve_window(request.GET)
        form = BookingDatesForm(initial=window_params(pickup, drop))
    if not (pickup and drop):
        pickup, drop = default_window()
    return render(request, "booking.html", {
        "car": car, "form": form, "step": "dates", "steps": checkout_steps("dates", car),
        "pickup": pickup, "drop": drop,
        "window": window_params(pickup, drop),
        "info": availability_for(car, pickup, drop, exclude),
        "estimate": price_estimate(car, pickup, drop) if drop > pickup else None,
        "calendar": availability_calendar(car, exclude),
    })


def _store_document(booking, doc_type, uploaded):
    safe_name = get_valid_filename(uploaded.name) or f"{doc_type.lower()}.bin"
    ref = upload_document_file(uploaded, f"{booking.booking_id}/{doc_type}_{safe_name}")
    defaults = {"verification_status": Document.VerificationStatus.PENDING, "verified_at": None, "rejection_reason": ""}
    if ref:
        defaults.update(file="", file_reference=ref)
    else:
        uploaded.seek(0)
        uploaded.name = safe_name
        defaults.update(file=uploaded, file_reference="")
    Document.objects.update_or_create(booking=booking, document_type=doc_type, defaults=defaults)


@customer_required
def verification(request, booking_id):
    booking, response = _checkout_booking(request, booking_id, "verification")
    if response:
        return response
    field_map = {"driving_license": Document.DocType.DRIVING_LICENSE, "govt_id": Document.DocType.GOVT_ID}
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_any = False
            try:
                for field, doc_type in field_map.items():
                    uploaded = form.cleaned_data.get(field)
                    if uploaded:
                        _store_document(booking, doc_type, uploaded)
                        uploaded_any = True
            except OSError:
                form.add_error(None, "We couldn't store your file. Please try again in a moment.")
            if not form.errors:
                still_needed = booking.missing_documents() + [f"a new {d.get_document_type_display()}" for d in booking.rejected_documents()]
                if still_needed:
                    form.add_error(None, ("Saved. " if uploaded_any else "") + "Still required: " + ", ".join(still_needed) + ".")
                elif booking.is_paid:
                    messages.success(request, "Documents re-submitted. Our team will review them shortly.")
                    return redirect("confirmation", booking_id=booking.booking_id)
                else:
                    if uploaded_any:
                        messages.success(request, "Documents uploaded.")
                    return redirect("delivery", booking_id=booking.booking_id)
    else:
        form = DocumentUploadForm()
    docs = booking.document_map()
    return render(request, "verification.html", _checkout_context(
        booking, "verification", form=form, docs=docs,
        doc_rows=[
            {"field": "driving_license", "type": Document.DocType.DRIVING_LICENSE, "label": "Driving License",
             "hint": "A clear photo or scan of the front of your license.", "doc": docs.get(Document.DocType.DRIVING_LICENSE),
             "errors": form["driving_license"].errors},
            {"field": "govt_id", "type": Document.DocType.GOVT_ID, "label": "Government ID",
             "hint": "Aadhaar, PAN or passport — your name and photo must be readable.", "doc": docs.get(Document.DocType.GOVT_ID),
             "errors": form["govt_id"].errors},
        ],
    ))


@customer_required
def delivery(request, booking_id):
    booking, response = _checkout_booking(request, booking_id, "delivery")
    if response:
        return response
    if request.method == "POST":
        form = DeliveryForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            booking.delivery_method = data["delivery_method"]
            home = booking.delivery_method == "HOME_DELIVERY"
            booking.delivery_address = data["delivery_address"].strip() if home else ""
            booking.delivery_city = data["delivery_city"].strip() if home else ""
            booking.delivery_pincode = data["delivery_pincode"].strip() if home else ""
            booking.delivery_instructions = data["delivery_instructions"].strip() if home else ""
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
    method = form["delivery_method"].value() or booking.delivery_method
    return render(request, "delivery.html", _checkout_context(booking, "delivery", form=form, method=method))


@customer_required
def summary(request, booking_id):
    booking, response = _checkout_booking(request, booking_id, "summary")
    if response:
        return response
    return render(request, "summary.html", _checkout_context(booking, "summary"))


def _demo_mode_on():
    return SiteSetting.get("demo_mode", "off") == "on"


@customer_required
def payment(request, booking_id):
    booking, response = _checkout_booking(request, booking_id, "payment")
    if response:
        return response
    try:
        order_data = create_razorpay_order(booking, force_mock=_demo_mode_on())
    except Exception:
        messages.error(request, "Could not create a payment link. Please try again.")
        return redirect("summary", booking_id=booking.booking_id)
    booking.razorpay_order_id = order_data["id"]
    booking.save(update_fields=["razorpay_order_id"])
    return render(request, "payment.html", _checkout_context(
        booking, "payment",
        razorpay_key_id=settings.RAZORPAY_KEY_ID,
        is_mock=(booking.razorpay_order_id or "").startswith("order_mock_"),
    ))


def _confirmation_json(booking):
    return JsonResponse({"ok": True, "redirect": reverse("confirmation", kwargs={"booking_id": booking.booking_id})})


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
    if booking.is_paid:
        return _confirmation_json(booking)
    if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED, Booking.Status.COMPLETED):
        return JsonResponse({"ok": False, "error": _no_longer_active_message(booking)}, status=409)
    order_id = payload.get("razorpay_order_id", "")
    payment_id = payload.get("razorpay_payment_id", "")
    signature = payload.get("razorpay_signature", "")
    if not order_id or order_id != booking.razorpay_order_id:
        return JsonResponse({"ok": False, "error": "Order not found."}, status=404)
    if order_id.startswith("order_mock_") and not (settings.RAZORPAY_MOCK or _demo_mode_on()):
        return JsonResponse({"ok": False, "error": "Simulated payments are turned off. Reload the page to pay."}, status=400)
    if not verify_razorpay_signature(order_id, payment_id, signature or f"pay_mock_{booking.booking_id}"):
        booking.payment_status = Booking.PaymentStatus.FAILED
        booking.razorpay_payment_id = payment_id
        booking.save(update_fields=["payment_status", "razorpay_payment_id"])
        return JsonResponse({"ok": False, "error": "Payment verification failed."}, status=400)
    remote = fetch_razorpay_order(order_id)
    if remote and int(remote.get("amount", 0)) != int(booking.total_amount * 100):
        booking.payment_status = Booking.PaymentStatus.FAILED
        booking.razorpay_payment_id = payment_id
        booking.save(update_fields=["payment_status", "razorpay_payment_id"])
        return JsonResponse({"ok": False, "error": "Amount mismatch."}, status=400)
    with transaction.atomic():
        booking = Booking.objects.select_for_update().select_related("car", "user", "pickup_location").get(pk=booking.pk)
        if booking.is_paid:
            return _confirmation_json(booking)
        if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED):
            return JsonResponse({"ok": False, "error": _no_longer_active_message(booking)}, status=409)
        car_locked = Car.objects.select_for_update().get(pk=booking.car_id)
        if not car_locked.is_available_for(booking.pickup_datetime, booking.dropoff_datetime, exclude_ids=[booking.pk]):
            booking.razorpay_payment_id = payment_id
            refund_ok = process_refund(booking)
            booking.payment_status = Booking.PaymentStatus.REFUNDED if refund_ok else Booking.PaymentStatus.FAILED
            booking.status = Booking.Status.CANCELLED
            booking.admin_note = booking.admin_note or "Auto-cancelled: the car was no longer available when payment completed."
            booking.save(update_fields=["payment_status", "razorpay_payment_id", "status", "admin_note"])
            error = "Sorry — this car was booked by someone else before your payment completed." + (
                " A full refund has been initiated." if refund_ok else " Contact support and we'll refund you right away.")
            return JsonResponse({"ok": False, "error": error}, status=409)
        booking.payment_status = Booking.PaymentStatus.PAID
        booking.razorpay_payment_id = payment_id
        booking.payment_method = (payload.get("method") or "")[:30]
        booking.status = Booking.Status.CONFIRMED if booking.documents_verified() else Booking.Status.PENDING_VERIFICATION
        booking.save(update_fields=["payment_status", "razorpay_payment_id", "payment_method", "status"])
    send_booking_confirmation_email(booking)
    return _confirmation_json(booking)


@customer_required
def confirmation(request, booking_id):
    booking = owned_booking(request, booking_id)
    if booking is None:
        return redirect("my_bookings")
    if booking.status in (Booking.Status.CANCELLED, Booking.Status.REJECTED):
        messages.error(request, _no_longer_active_message(booking))
        return redirect("my_bookings")
    if not booking.is_paid and booking.status != Booking.Status.COMPLETED:
        messages.info(request, "Complete the payment to confirm this booking.")
        return redirect("payment", booking_id=booking.booking_id)
    return render(request, "confirmation.html", {
        "booking": booking, "car": booking.car,
        "docs": list(booking.documents.all()),
        "rejected_docs": booking.rejected_documents(),
    })


@customer_required
@require_POST
def cancel_booking(request, booking_id):
    booking = owned_booking(request, booking_id)
    tab = request.POST.get("tab") or request.GET.get("tab")
    back = redirect(f"{reverse('my_bookings')}?{urlencode({'tab': tab})}") if tab else redirect("my_bookings")
    if booking is None:
        return back
    refunded = refund_ok = False
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        if not booking.customer_can_cancel:
            if booking.status == Booking.Status.ACTIVE or (booking.is_paid and booking.status in Booking.OPEN_STATUSES):
                messages.error(request, "Your trip has already started. Please contact support to make changes.")
            else:
                messages.error(request, "This booking can no longer be cancelled.")
            return back
        booking.status = Booking.Status.CANCELLED
        refunded = booking.is_paid
        if refunded:
            refund_ok = process_refund(booking)
            booking.payment_status = Booking.PaymentStatus.REFUNDED if refund_ok else Booking.PaymentStatus.FAILED
        booking.save(update_fields=["status", "payment_status"])
    if refunded and refund_ok:
        messages.success(request, "Booking cancelled. A refund for the paid amount has been initiated.")
    elif refunded:
        messages.success(request, "Booking cancelled. Your refund couldn't be started automatically — our team will reach out.")
    else:
        messages.success(request, "Booking cancelled.")
    return back


@customer_required
def my_bookings(request):
    customer = get_customer(request)
    tab = request.GET.get("tab", "upcoming")
    base = Booking.objects.filter(user=customer).select_related("car", "pickup_location").prefetch_related("documents")
    groups = {
        "upcoming": base.filter(status__in=Booking.OPEN_STATUSES)
        .exclude(status=Booking.Status.PENDING, created_at__lt=_pending_cutoff()).order_by("pickup_datetime"),
        "completed": base.filter(status=Booking.Status.COMPLETED).order_by("-dropoff_datetime"),
        "cancelled": base.filter(status__in=[Booking.Status.CANCELLED, Booking.Status.REJECTED]),
    }
    if tab not in groups:
        tab = "upcoming"
    counts = {key: qs.count() for key, qs in groups.items()}
    bookings = list(groups[tab])
    for b in bookings:
        step = b.checkout_step
        b.resume_url = reverse(step, args=[b.booking_id]) if step else ""
    return render(request, "my-bookings.html", {
        "bookings": bookings,
        "tab": tab,
        "counts": counts,
        "customer": customer,
        "total_trips": counts["upcoming"] + counts["completed"],
    })


@customer_required
def profile(request):
    return redirect("my_bookings")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@ensure_csrf_cookie
def login_view(request):
    next_url = _safe_next(request, request.GET.get("next", ""))
    customer = get_customer(request)
    if customer:
        return redirect(next_url or ("admin_dashboard" if customer.is_admin else "my_bookings"))
    return render(request, "login.html", {"next": next_url})


@ensure_csrf_cookie
def signup_view(request):
    next_url = _safe_next(request, request.GET.get("next", ""))
    if get_customer(request):
        return redirect(next_url or "my_bookings")
    return render(request, "signup.html", {"next": next_url})


@require_POST
def clerk_callback(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=400)
    clerk_user_id = verify_clerk_session_token(payload.get("session_token") or "", request.get_host())
    if not clerk_user_id:
        return JsonResponse({"ok": False, "error": "Could not verify your sign-in. Please try again."}, status=401)
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
    request.session.cycle_key()
    request.session["clerk_user_id"] = customer.clerk_user_id
    request.session["role"] = role
    default_next = reverse("admin_dashboard") if role == "ADMIN" else reverse("my_bookings")
    next_url = _safe_next(request, payload.get("next") or "")
    return JsonResponse({"ok": True, "redirect": next_url or default_next})


def logout_view(request):
    request.session.flush()
    return render(request, "logged_out.html")


# ---------------------------------------------------------------------------
# Admin console
# ---------------------------------------------------------------------------

def _review_queue():
    """Paid bookings whose documents still need a decision."""
    return Booking.objects.filter(
        Q(status=Booking.Status.PENDING_VERIFICATION)
        | Q(status__in=[Booking.Status.CONFIRMED, Booking.Status.ACTIVE],
            documents__verification_status=Document.VerificationStatus.PENDING)
    ).distinct()


def _admin_verification_count():
    return _review_queue().count()


def _admin_ctx(active, **extra):
    return {"active": active, "verification_count": _admin_verification_count(), **extra}


def _admin_back(request, fallback="admin_dashboard"):
    for candidate in (request.POST.get("next"), request.META.get("HTTP_REFERER")):
        url = _safe_next(request, candidate or "")
        if url:
            return redirect(url)
    return redirect(fallback)


def _open_bookings():
    return Booking.objects.filter(status__in=Booking.OPEN_STATUSES).exclude(
        status=Booking.Status.PENDING, created_at__lt=_pending_cutoff()
    )


@clerk_admin_required
def admin_dashboard(request):
    now = timezone.now()
    today = timezone.localdate()
    day_start = timezone.make_aware(datetime.combine(today, time.min))
    day_end = day_start + timedelta(days=1)
    paid = Booking.objects.filter(payment_status=Booking.PaymentStatus.PAID)
    related = ("user", "car", "pickup_location")

    week = []
    for offset in range(6, -1, -1):
        day = today - timedelta(days=offset)
        start = timezone.make_aware(datetime.combine(day, time.min))
        total = paid.filter(created_at__gte=start, created_at__lt=start + timedelta(days=1)).aggregate(t=Sum("total_amount"))["t"] or 0
        week.append({"label": day.strftime("%a"), "date": day, "total": int(total)})
    week_max = max([d["total"] for d in week] + [1])
    for d in week:
        d["pct"] = round(d["total"] * 100 / week_max)

    cars_total = Car.objects.count()
    availability = {
        "Available": Car.objects.filter(status="AVAILABLE").count(),
        "Maintenance": Car.objects.filter(status="MAINTENANCE").count(),
        "Inactive": Car.objects.filter(status="INACTIVE").count(),
    }
    on_trip = Booking.objects.filter(status=Booking.Status.ACTIVE).count()
    return render(request, "admin_dashboard.html", _admin_ctx(
        "overview",
        revenue=paid.aggregate(t=Sum("total_amount"))["t"] or 0,
        revenue_30d=paid.filter(created_at__gte=now - timedelta(days=30)).aggregate(t=Sum("total_amount"))["t"] or 0,
        active_bookings=_open_bookings().count(),
        on_trip=on_trip,
        total_cars=cars_total,
        available_cars=availability["Available"],
        utilization=round(on_trip * 100 / availability["Available"]) if availability["Available"] else 0,
        availability=availability,
        pickups=Booking.objects.filter(
            status__in=[Booking.Status.CONFIRMED, Booking.Status.PENDING_VERIFICATION], pickup_datetime__lt=day_end
        ).select_related(*related).order_by("pickup_datetime")[:8],
        returns=Booking.objects.filter(status=Booking.Status.ACTIVE, dropoff_datetime__lt=day_end)
        .select_related(*related).order_by("dropoff_datetime")[:8],
        now=now,
        recent=Booking.objects.select_related(*related).exclude(
            status__in=[Booking.Status.CANCELLED, Booking.Status.REJECTED]
        ).order_by("-created_at")[:8],
        week=week,
        demo_mode=_demo_mode_on(),
        razorpay_configured=bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET),
    ))


@clerk_admin_required
def admin_verifications(request):
    related = ("user", "car", "pickup_location")
    queue = _review_queue().select_related(*related).prefetch_related("documents").order_by("created_at")
    awaiting_payment = (
        Booking.objects.filter(status=Booking.Status.PENDING, created_at__gte=_pending_cutoff(), documents__isnull=False)
        .exclude(payment_status=Booking.PaymentStatus.PAID).distinct()
        .select_related(*related).prefetch_related("documents").order_by("-created_at")[:20]
    )
    return render(request, "admin_verifications.html", _admin_ctx(
        "verifications", verification_queue=queue, awaiting_payment=awaiting_payment,
    ))


BOOKING_FILTERS = {
    "active": ("Active", lambda qs: qs.filter(pk__in=_open_bookings().values("pk"))),
    "verification": ("Needs review", lambda qs: qs.filter(pk__in=_review_queue().values("pk"))),
    "confirmed": ("Confirmed", lambda qs: qs.filter(status=Booking.Status.CONFIRMED)),
    "on_trip": ("On trip", lambda qs: qs.filter(status=Booking.Status.ACTIVE)),
    "completed": ("Completed", lambda qs: qs.filter(status=Booking.Status.COMPLETED)),
    "cancelled": ("Cancelled", lambda qs: qs.filter(status__in=[Booking.Status.CANCELLED, Booking.Status.REJECTED])),
    "all": ("All", lambda qs: qs),
}


@clerk_admin_required
def admin_bookings(request):
    status_filter = request.GET.get("status", "active")
    if status_filter not in BOOKING_FILTERS:
        status_filter = "active"
    base = Booking.objects.all()
    q = (request.GET.get("q") or "").strip()
    if q:
        base = base.filter(
            Q(booking_id__icontains=q) | Q(user__full_name__icontains=q) | Q(user__email__icontains=q)
            | Q(car__brand__icontains=q) | Q(car__model__icontains=q) | Q(car__registration_number__icontains=q)
        )
    tabs = [{"key": key, "label": label, "count": fn(base).count()} for key, (label, fn) in BOOKING_FILTERS.items()]
    bookings = BOOKING_FILTERS[status_filter][1](base).select_related("user", "car", "pickup_location").order_by("-created_at")
    page = Paginator(bookings, 20).get_page(request.GET.get("page"))
    return render(request, "admin_bookings.html", _admin_ctx(
        "bookings", bookings_list=page, page=page, tabs=tabs, status_filter=status_filter, q=q,
    ))


@clerk_admin_required
def admin_booking_detail(request, booking_id):
    booking = get_object_or_404(Booking.objects.select_related("user", "car", "car__location", "pickup_location"), booking_id=booking_id)
    docs = booking.document_map()
    doc_rows = [{"type": value, "label": label, "doc": docs.get(value)} for value, label in Document.DocType.choices]
    clashes = Booking.overlaps(booking.car, booking.pickup_datetime, booking.dropoff_datetime, exclude_id=booking.pk) \
        if booking.status in Booking.OPEN_STATUSES else Booking.objects.none()
    return render(request, "admin_booking_detail.html", _admin_ctx(
        "bookings", booking=booking, doc_rows=doc_rows,
        clashes=clashes.select_related("user"),
        blocks=booking.car.blocked_window(booking.pickup_datetime, booking.dropoff_datetime)
        if booking.status in Booking.OPEN_STATUSES else [],
        other_bookings=Booking.objects.filter(user=booking.user).exclude(pk=booking.pk).select_related("car").order_by("-created_at")[:5],
    ))


# Only these types are ever rendered inline; anything else downloads, so a
# renamed upload can never execute as HTML inside the admin console.
INLINE_DOCUMENT_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _document_bytes(doc):
    data = fetch_document_bytes(doc.file_reference) if doc.file_reference else b""
    if not data and doc.file:
        # Remote copy missing or empty (older uploads sent 0 bytes) — use the local copy.
        try:
            with doc.file.open("rb") as fh:
                data = fh.read()
        except (OSError, ValueError):
            data = b""
    return data


@xframe_options_sameorigin
@clerk_admin_required
def admin_document(request, doc_id):
    """Stream a KYC document to the admin, framable only by our own pages."""
    doc = get_object_or_404(Document, pk=doc_id)
    data = _document_bytes(doc)
    if not data:
        return HttpResponse(
            "<!doctype html><meta charset='utf-8'><body style='font:15px system-ui;color:#475467;display:grid;"
            "place-items:center;height:100vh;margin:0;text-align:center;padding:24px'>"
            "<div><b style='color:#101828'>This file is missing or empty.</b><br>"
            "Reject the document with a note asking the customer to upload it again.</div>",
            status=404,
        )
    name = get_valid_filename(doc.storage_name.rsplit("/", 1)[-1]) or f"document-{doc.pk}"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    content_type = INLINE_DOCUMENT_TYPES.get(ext)
    response = HttpResponse(data, content_type=content_type or "application/octet-stream")
    response["Content-Disposition"] = f'{"inline" if content_type else "attachment"}; filename="{name}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@clerk_admin_required
def admin_customers(request):
    q = (request.GET.get("q") or "").strip()
    customers = Customer.objects.annotate(
        bookings_total=Count("bookings", distinct=True),
        trips=Count("bookings", filter=Q(bookings__status=Booking.Status.COMPLETED), distinct=True),
        spent=Sum("bookings__total_amount", filter=Q(bookings__payment_status=Booking.PaymentStatus.PAID)),
        last_booking=Max("bookings__created_at"),
    ).order_by("-created_at")
    if q:
        customers = customers.filter(Q(full_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))
    page = Paginator(customers, 25).get_page(request.GET.get("page"))
    return render(request, "admin_customers.html", _admin_ctx(
        "customers", customers=page, page=page, q=q, me=get_customer(request),
    ))


@clerk_admin_required
@require_POST
def admin_set_role(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    role = request.POST.get("role", "")
    if role not in dict(Customer.ROLE_CHOICES):
        messages.error(request, "Invalid role.")
        return _admin_back(request, "admin_customers")
    if role != "ADMIN" and customer == get_customer(request):
        messages.error(request, "You can't remove your own admin access.")
        return _admin_back(request, "admin_customers")
    if role != "ADMIN" and not Customer.objects.filter(role="ADMIN").exclude(pk=customer.pk).exists():
        messages.error(request, "At least one admin must remain.")
        return _admin_back(request, "admin_customers")

    customer.role = role
    customer.save(update_fields=["role"])
    name = customer.full_name or customer.email or customer.clerk_user_id
    if not set_user_role(customer.clerk_user_id, role):
        messages.error(request, f"Saved locally, but Supabase user_roles could not be updated, so {name}'s old role may still apply.")
    elif role == "ADMIN":
        messages.success(request, f"{name} is now an admin.")
    else:
        messages.success(request, f"{name} is no longer an admin.")
    return _admin_back(request, "admin_customers")


@clerk_admin_required
def admin_cars(request):
    now = timezone.now()
    availability = {
        "Available": Car.objects.filter(status="AVAILABLE").count(),
        "Maintenance": Car.objects.filter(status="MAINTENANCE").count(),
        "Inactive": Car.objects.filter(status="INACTIVE").count(),
    }
    rows = []
    for car in Car.objects.select_related("location").order_by("brand", "model"):
        upcoming = Booking.objects.filter(
            car=car, status__in=[Booking.Status.PENDING_VERIFICATION, Booking.Status.CONFIRMED, Booking.Status.ACTIVE],
            dropoff_datetime__gt=now,
        ).order_by("pickup_datetime")
        current = upcoming.filter(pickup_datetime__lte=now).first()
        rows.append({"car": car, "current": current, "next": upcoming.exclude(pk=getattr(current, "pk", None)).first(),
                     "upcoming_count": upcoming.count()})
    return render(request, "admin_cars.html", _admin_ctx(
        "cars", availability=availability, rows=rows, all_cars=[r["car"] for r in rows],
        all_locations=Location.objects.all().order_by("name"),
        choices={"category": Car.CATEGORY_CHOICES, "transmission": Car.TRANSMISSION_CHOICES,
                 "fuel_type": Car.FUEL_CHOICES, "status": Car.STATUS_CHOICES},
    ))


@clerk_admin_required
def admin_locations(request):
    now = timezone.now()
    locations = []
    for loc in Location.objects.annotate(car_count=Count("cars", distinct=True), booking_count=Count("pickup_bookings", distinct=True)).order_by("-is_active", "name"):
        on_trip = Booking.objects.filter(pickup_location=loc, status=Booking.Status.ACTIVE).count()
        upcoming = Booking.objects.filter(pickup_location=loc, status=Booking.Status.CONFIRMED, pickup_datetime__gte=now).count()
        locations.append({"location": loc, "cars": loc.car_count, "booked": on_trip, "upcoming": upcoming, "bookings": loc.booking_count})
    return render(request, "admin_locations.html", _admin_ctx("locations", locations=locations))


@clerk_admin_required
def admin_blocks(request):
    now = timezone.now()
    blocks = CarBlock.objects.select_related("car", "car__location")
    return render(request, "admin_blocks.html", _admin_ctx(
        "blocks",
        current_blocks=blocks.filter(end_datetime__gt=now).order_by("start_datetime"),
        recent_blocks=blocks.filter(end_datetime__lte=now).order_by("-end_datetime")[:10],
        all_cars=Car.objects.select_related("location").order_by("brand", "model"),
        now=now,
    ))


@clerk_admin_required
@require_POST
def admin_demo_toggle(request):
    currently_on = _demo_mode_on()
    SiteSetting.set("demo_mode", "off" if currently_on else "on")
    messages.success(request, "Demo mode turned OFF. Real payments required." if currently_on else "Demo mode turned ON. Payments will be simulated.")
    return _admin_back(request)


def _admin_booking(booking_id):
    return Booking.objects.select_related("user", "car", "pickup_location").filter(booking_id=booking_id).first()


@clerk_admin_required
@require_POST
def admin_cancel_booking(request, booking_id):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return _admin_back(request)
    refunded = refund_ok = False
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        if booking.is_closed:
            messages.error(request, "This booking can no longer be cancelled.")
            return _admin_back(request)
        booking.status = Booking.Status.CANCELLED
        refunded = booking.is_paid
        if refunded:
            refund_ok = process_refund(booking)
            booking.payment_status = Booking.PaymentStatus.REFUNDED if refund_ok else Booking.PaymentStatus.FAILED
        booking.save(update_fields=["status", "payment_status"])
    if refunded and refund_ok:
        messages.success(request, f"Booking {booking_id} cancelled. Full refund initiated.")
    elif refunded:
        messages.error(request, f"Booking {booking_id} cancelled, but the refund failed — process it manually in Razorpay.")
    else:
        messages.success(request, f"Booking {booking_id} cancelled.")
    return _admin_back(request)


def _selected_documents(request, booking):
    docs = booking.documents.all()
    doc_type = request.POST.get("doc_type")
    return docs.filter(document_type=doc_type) if doc_type else docs


@clerk_admin_required
@require_POST
def admin_approve_docs(request, booking_id):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return _admin_back(request)
    if booking.is_closed:
        messages.error(request, "This booking is closed and can't be approved.")
        return _admin_back(request)
    targets = _selected_documents(request, booking)
    if not request.POST.get("doc_type") and booking.missing_documents():
        messages.error(request, f"Can't approve yet — missing: {', '.join(booking.missing_documents())}.")
        return _admin_back(request)
    if not targets.exists():
        messages.error(request, "No documents to approve.")
        return _admin_back(request)
    targets.exclude(verification_status=Document.VerificationStatus.VERIFIED).update(
        verification_status=Document.VerificationStatus.VERIFIED, verified_at=timezone.now(), rejection_reason="",
    )
    confirmed = False
    if booking.documents_verified() and booking.is_paid and booking.status == Booking.Status.PENDING_VERIFICATION:
        booking.status = Booking.Status.CONFIRMED
        booking.save(update_fields=["status"])
        send_booking_confirmation_email(booking)
        confirmed = True
    if confirmed:
        messages.success(request, f"Documents verified. {booking_id} is confirmed and the customer has been emailed.")
    elif booking.documents_verified() and not booking.is_paid:
        messages.success(request, "Documents verified. The booking confirms automatically once payment is received.")
    else:
        messages.success(request, "Document approved.")
    return _admin_back(request)


@clerk_admin_required
@require_POST
def admin_reject_docs(request, booking_id):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return _admin_back(request)
    if booking.is_closed:
        messages.error(request, "This booking is closed and can't be rejected.")
        return _admin_back(request)
    reason = (request.POST.get("reason") or "").strip()[:300]
    if not reason:
        messages.error(request, "Please provide a reason so the customer knows what to fix.")
        return _admin_back(request)
    targets = _selected_documents(request, booking)
    if not request.POST.get("doc_type"):
        targets = targets.exclude(verification_status=Document.VerificationStatus.VERIFIED)
    updated = targets.update(verification_status=Document.VerificationStatus.REJECTED, rejection_reason=reason, verified_at=None)
    if not updated:
        messages.error(request, "No documents to reject.")
    else:
        if booking.status == Booking.Status.CONFIRMED:
            booking.status = Booking.Status.PENDING_VERIFICATION
            booking.save(update_fields=["status"])
        messages.success(request, "Documents rejected. The customer will be asked to re-upload.")
    return _admin_back(request)


@clerk_admin_required
@require_POST
def admin_trip_action(request, booking_id, action):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return _admin_back(request)
    if action == "start":
        if booking.status != Booking.Status.CONFIRMED:
            messages.error(request, "Only confirmed bookings can start a trip.")
        else:
            booking.status = Booking.Status.ACTIVE
            booking.save(update_fields=["status"])
            messages.success(request, f"Trip {booking_id} started.")
    elif action == "complete":
        if booking.status not in (Booking.Status.CONFIRMED, Booking.Status.ACTIVE):
            messages.error(request, "Only active or confirmed bookings can be completed.")
        else:
            booking.status = Booking.Status.COMPLETED
            booking.save(update_fields=["status"])
            messages.success(request, f"Trip {booking_id} completed.")
    else:
        messages.error(request, "Unknown action.")
    return _admin_back(request)


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
        if status != "AVAILABLE":
            upcoming = Booking.objects.filter(
                car=car, status__in=[Booking.Status.PENDING_VERIFICATION, Booking.Status.CONFIRMED], dropoff_datetime__gt=timezone.now()
            ).count()
            if upcoming:
                messages.warning(request, f"{car} still has {upcoming} upcoming booking(s) — reassign or cancel them.")
    return _admin_back(request, "admin_cars")


def _car_fields(request):
    """Validated car attributes from an admin form, or ``(None, error)``."""
    def val(key, default=""):
        return (request.POST.get(key) or default).strip()

    data = {
        "brand": val("brand"), "model": val("model"), "registration_number": val("registration_number").upper(),
        "category": val("category", "SUV"), "transmission": val("transmission", "Manual"),
        "fuel_type": val("fuel_type", "Petrol"), "status": val("status", "AVAILABLE"),
        "image_url": val("image_url"), "features": val("features", "AC, 5 Doors"),
        "location": Location.objects.filter(pk=request.POST.get("location") or 0).first(),
    }
    if not data["brand"] or not data["model"] or not data["registration_number"] or data["location"] is None:
        return None, "Brand, model, registration number and location are required."
    for field, choices in (("category", Car.CATEGORY_CHOICES), ("transmission", Car.TRANSMISSION_CHOICES),
                           ("fuel_type", Car.FUEL_CHOICES), ("status", Car.STATUS_CHOICES)):
        if data[field] not in dict(choices):
            return None, f"Invalid {field.replace('_', ' ')}."
    try:
        data["price_per_day"] = int(val("price_per_day", "1500"))
        data["seats"] = int(val("seats", "5"))
        if data["price_per_day"] < 1 or not 1 <= data["seats"] <= 20:
            raise ValueError
    except ValueError:
        return None, "Enter a valid daily price and seat count."
    if data["image_url"] and not data["image_url"].startswith(("http://", "https://")):
        return None, "Image URL must start with http:// or https://."
    return data, None


@clerk_admin_required
@require_POST
def admin_add_car(request):
    data, error = _car_fields(request)
    if error:
        messages.error(request, error)
        return _admin_back(request, "admin_cars")
    try:
        with transaction.atomic():
            car = Car.objects.create(**data)
    except IntegrityError:
        messages.error(request, f"A car with registration {data['registration_number']} already exists.")
        return _admin_back(request, "admin_cars")
    messages.success(request, f"Car {car} added.")
    return _admin_back(request, "admin_cars")


@clerk_admin_required
@require_POST
def admin_edit_car(request, car_id):
    car = get_object_or_404(Car, pk=car_id)
    data, error = _car_fields(request)
    if error:
        messages.error(request, error)
        return _admin_back(request, "admin_cars")
    for key, value in data.items():
        setattr(car, key, value)
    try:
        with transaction.atomic():
            car.save()
    except IntegrityError:
        messages.error(request, f"Another car already uses registration {data['registration_number']}.")
        return _admin_back(request, "admin_cars")
    messages.success(request, f"{car} updated.")
    return _admin_back(request, "admin_cars")


@clerk_admin_required
@require_POST
def admin_add_location(request):
    def val(key, default=""):
        return (request.POST.get(key) or default).strip()

    name = val("name")
    if not name:
        messages.error(request, "Location name is required.")
        return _admin_back(request, "admin_locations")
    try:
        with transaction.atomic():
            loc = Location.objects.create(
                name=name, address=val("address"), city=val("city", "Kolkata"),
                phone=val("phone"), image_url=val("image_url"),
            )
    except IntegrityError:
        messages.error(request, "A location with that name already exists.")
        return _admin_back(request, "admin_locations")
    messages.success(request, f"Location {loc.name} added.")
    return _admin_back(request, "admin_locations")


@clerk_admin_required
@require_POST
def admin_toggle_location(request, location_id):
    loc = get_object_or_404(Location, pk=location_id)
    loc.is_active = not loc.is_active
    loc.save(update_fields=["is_active"])
    messages.success(request, f"{loc.name} is now {'active' if loc.is_active else 'hidden from customers'}.")
    return _admin_back(request, "admin_locations")


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
        return _admin_back(request, "admin_blocks")
    CarBlock.objects.create(car=car, start_datetime=start, end_datetime=end, note=(request.POST.get("note") or "").strip()[:255])
    messages.success(request, f"{car} blocked {timezone.localtime(start):%d %b %H:%M} – {timezone.localtime(end):%d %b %H:%M}.")
    clashes = Booking.overlaps(car, start, end).exclude(status=Booking.Status.PENDING)
    if clashes.exists():
        ids = ", ".join(clashes.values_list("booking_id", flat=True)[:5])
        messages.warning(request, f"Heads up: this block overlaps existing booking(s) {ids}. They were not cancelled.")
    return _admin_back(request, "admin_blocks")


@clerk_admin_required
@require_POST
def admin_delete_block(request, block_id):
    block = get_object_or_404(CarBlock.objects.select_related("car"), pk=block_id)
    label = str(block.car)
    block.delete()
    messages.success(request, f"Block removed — {label} is bookable for that period again.")
    return _admin_back(request, "admin_blocks")


@clerk_admin_required
@require_POST
def admin_set_note(request, booking_id):
    booking = _admin_booking(booking_id)
    if booking is None:
        messages.error(request, "Booking not found.")
        return _admin_back(request)
    booking.admin_note = (request.POST.get("note") or "").strip()
    booking.save(update_fields=["admin_note"])
    messages.success(request, f"Note saved for {booking_id}.")
    return _admin_back(request)
