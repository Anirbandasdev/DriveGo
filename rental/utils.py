import hashlib
import hmac
import logging
import time
from decimal import Decimal
from urllib.parse import urlsplit

import jwt
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


_JWKS_CACHE = {"keys": {}, "fetched": 0.0}


def _clerk_signing_key(kid, force=False):
    if force or not _JWKS_CACHE["keys"] or time.time() - _JWKS_CACHE["fetched"] > 3600:
        try:
            resp = requests.get(
                "https://api.clerk.com/v1/jwks",
                headers={"Authorization": f"Bearer {settings.CLERK_SECRET_KEY}"},
                timeout=10,
            )
            resp.raise_for_status()
            _JWKS_CACHE["keys"] = {k["kid"]: k for k in resp.json().get("keys", []) if k.get("kid")}
            _JWKS_CACHE["fetched"] = time.time()
        except (requests.RequestException, ValueError):
            logger.exception("Could not fetch Clerk JWKS")
            return None
    jwk = _JWKS_CACHE["keys"].get(kid)
    if jwk is None and not force:
        return _clerk_signing_key(kid, force=True)
    return jwt.PyJWK(jwk).key if jwk else None


def verify_clerk_session_token(token, request_host=""):
    """Return the Clerk user id for a valid, unexpired session JWT, else ``None``.

    The browser proves who it is with ``Clerk.session.getToken()``; a bare user id
    from the client is never trusted.
    """
    if not token or not settings.CLERK_SECRET_KEY:
        return None
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        key = _clerk_signing_key(kid)
        if key is None:
            logger.warning("Clerk token rejected: no signing key found for kid %s", kid)
            return None
        claims = jwt.decode(token, key, algorithms=["RS256"], options={"require": ["exp", "iat", "sub"]}, leeway=30)
    except jwt.PyJWTError as exc:
        logger.warning("Clerk token rejected: %s: %s", type(exc).__name__, exc)
        return None
    azp = claims.get("azp")
    if azp and request_host and urlsplit(azp).netloc != request_host:
        logger.warning("Clerk token azp %s does not match host %s", azp, request_host)
        return None
    return claims.get("sub")


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


def set_user_role(clerk_user_id, role):
    """Store a role in Supabase ``user_roles``, which takes priority over the local database.

    Returns True when saved (or when Supabase isn't configured, so only the local
    role applies), False when the Supabase write failed.
    """
    if not (settings.SUPABASE_URL and settings.SUPABASE_KEY):
        return True
    try:
        resp = requests.post(
            f"{settings.SUPABASE_URL}/rest/v1/user_roles",
            params={"on_conflict": "clerk_user_id"},
            json={"clerk_user_id": clerk_user_id, "role": role},
            headers={
                "apikey": settings.SUPABASE_KEY,
                "Authorization": f"Bearer {settings.SUPABASE_KEY}",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
            timeout=10,
        )
    except requests.RequestException:
        logger.exception("Supabase role update failed")
        return False
    if resp.status_code in (200, 201, 204):
        return True
    logger.warning("Supabase role update rejected: %s %s", resp.status_code, resp.text[:200])
    return False


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


def fetch_razorpay_payment(payment_id):
    """The payment as Razorpay knows it: amount, order and the method the customer picked."""
    if settings.RAZORPAY_MOCK or (payment_id or "").startswith("pay_mock_"):
        return {}
    client = razorpay_client()
    if client is None:
        return {}
    try:
        return client.payment.fetch(payment_id)
    except Exception:
        logger.exception("Razorpay payment fetch failed for %s", payment_id)
        return {}


def process_refund(booking):
    """Initiate a real Razorpay refund for a captured payment.

    Returns True when the refund was issued (or simulated in mock mode) and
    False when there is no captured payment to refund or the API call failed.
    """
    payment_id = booking.razorpay_payment_id
    if not payment_id:
        return False
    if settings.RAZORPAY_MOCK or payment_id.startswith("pay_mock_"):
        return True
    client = razorpay_client()
    if client is None:
        return False
    try:
        client.payment.refund(payment_id, {"amount": int(Decimal(booking.total_amount) * 100), "currency": "INR"})
        return True
    except Exception:
        logger.exception("Razorpay refund failed for payment %s", payment_id)
        return False


def _read_upload(uploaded_file):
    uploaded_file.seek(0)
    return uploaded_file.read()


def fetch_document_bytes(file_reference):
    """Download a Supabase Storage object (``bucket/path``) with the service key.

    Works for private and public buckets. Returns ``b""`` when storage isn't
    configured, the object is missing, or the request fails.
    """
    if not (settings.SUPABASE_URL and settings.SUPABASE_KEY and file_reference):
        return b""
    try:
        resp = requests.get(
            f"{settings.SUPABASE_URL}/storage/v1/object/{file_reference}",
            headers={"apikey": settings.SUPABASE_KEY, "Authorization": f"Bearer {settings.SUPABASE_KEY}"},
            timeout=20,
        )
    except requests.RequestException:
        logger.exception("Supabase download failed for %s", file_reference)
        return b""
    if resp.status_code != 200:
        logger.warning("Supabase download rejected for %s: %s", file_reference, resp.status_code)
        return b""
    return resp.content


def upload_document_file(uploaded_file, remote_path):
    if not (settings.SUPABASE_URL and settings.SUPABASE_KEY):
        return ""
    try:
        resp = requests.post(
            f"{settings.SUPABASE_URL}/storage/v1/object/{settings.SUPABASE_DOC_BUCKET}/{remote_path}",
            headers={"apikey": settings.SUPABASE_KEY, "Authorization": f"Bearer {settings.SUPABASE_KEY}", "Content-Type": uploaded_file.content_type or "application/octet-stream"},
            data=_read_upload(uploaded_file),
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
