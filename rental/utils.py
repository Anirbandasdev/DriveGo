import hashlib
import hmac
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def verify_clerk_user(clerk_user_id):
    if not settings.CLERK_SECRET_KEY:
        return None
    try:
        resp = requests.get(
            f"https://api.clerk.com/v1/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
            timeout=10,
        )
    except requests.RequestException:
        logger.exception("Clerk verification request failed")
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    emails = [e["email_address"] for e in data.get("email_addresses", []) if e.get("email_address")]
    return {
        "clerk_user_id": data.get("id", clerk_user_id),
        "email": emails[0] if emails else "",
        "full_name": f"{data.get('first_name') or ''} {data.get('last_name') or ''}".strip(),
        "profile_image_url": data.get("image_url", ""),
    }


def get_user_role(clerk_user_id):
    if settings.SUPABASE_URL and settings.SUPABASE_KEY:
        try:
            resp = requests.get(
                f"{settings.SUPABASE_URL}/rest/v1/user_roles",
                params={"clerk_user_id": f"eq.{clerk_user_id}", "select": "role"},
                headers={"apikey": settings.SUPABASE_KEY, "Authorization": f"Bearer {settings.SUPABASE_KEY}"},
                timeout=10,
            )
            if resp.status_code == 200 and resp.json():
                role = (resp.json()[0] or {}).get("role", "CUSTOMER")
                if role in ("ADMIN", "CUSTOMER"):
                    return role
        except requests.RequestException:
            logger.exception("Supabase role lookup failed")
    from .models import Customer

    customer = Customer.objects.filter(clerk_user_id=clerk_user_id).first()
    return customer.role if customer else "CUSTOMER"


def razorpay_client():
    if settings.RAZORPAY_MOCK:
        return None
    import razorpay

    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def create_razorpay_order(booking, force_mock=False):
    amount_paise = int(booking.total_amount * 100)
    client = None if force_mock else razorpay_client()
    if client is None:
        return {"id": f"order_mock_{booking.booking_id}", "amount": amount_paise, "currency": "INR", "mock": True}
    return client.order.create({"amount": amount_paise, "currency": "INR", "receipt": booking.booking_id, "notes": {"booking_id": booking.booking_id}})


def verify_razorpay_signature(order_id, payment_id, signature):
    if order_id.startswith("order_mock_"):
        return payment_id.startswith("pay_mock_")
    if settings.RAZORPAY_MOCK:
        return payment_id.startswith("pay_mock_")
    body = f"{order_id}|{payment_id}"
    expected = hmac.new(settings.RAZORPAY_KEY_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def upload_document_file(uploaded_file, remote_path):
    if not (settings.SUPABASE_URL and settings.SUPABASE_KEY):
        return ""
    try:
        resp = requests.post(
            f"{settings.SUPABASE_URL}/storage/v1/object/{settings.SUPABASE_DOC_BUCKET}/{remote_path}",
            headers={"apikey": settings.SUPABASE_KEY, "Authorization": f"Bearer {settings.SUPABASE_KEY}", "Content-Type": uploaded_file.content_type or "application/octet-stream"},
            data=uploaded_file.read(),
            timeout=30,
        )
    except requests.RequestException:
        logger.exception("Supabase upload failed")
        return ""
    if resp.status_code in (200, 201):
        return f"{settings.SUPABASE_DOC_BUCKET}/{remote_path}"
    logger.warning("Supabase upload rejected: %s %s", resp.status_code, resp.text[:200])
    return ""


def send_booking_confirmation_email(booking):
    subject = f"DriveGo Booking Confirmation {booking.booking_id}"
    lines = [
        "DriveGo",
        "Booking Confirmation",
        "",
        f"Booking ID: {booking.booking_id}",
        f"Customer: {booking.user}",
        f"Car: {booking.car}",
        f"Pickup Location: {booking.pickup_location}",
        f"Pickup Date & Time: {booking.pickup_datetime:%d %B, %I:%M %p}",
        f"Drop-off Date & Time: {booking.dropoff_datetime:%d %B, %I:%M %p}",
        f"Delivery Method: {booking.get_delivery_method_display()}",
        f"Rental Amount: Rs.{booking.rental_amount}",
        f"Delivery Charge: Rs.{booking.delivery_charge}",
        f"Tax: Rs.{booking.tax_amount}",
        f"Total Amount: Rs.{booking.total_amount}",
        f"Booking Status: {booking.get_status_display()}",
    ]
    recipient = booking.user.email
    if not recipient:
        logger.info("Skipping confirmation email for %s (no customer email)", booking.booking_id)
        return False
    send_mail(subject, "\n".join(lines), None, [recipient], fail_silently=True)
    return True
