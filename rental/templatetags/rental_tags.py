from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

STATUS_TONES = {
    "PENDING": "neutral",
    "PENDING_VERIFICATION": "warn",
    "CONFIRMED": "ok",
    "ACTIVE": "info",
    "COMPLETED": "muted",
    "CANCELLED": "no",
    "REJECTED": "no",
    "PAID": "ok",
    "FAILED": "no",
    "REFUNDED": "muted",
    "VERIFIED": "ok",
    "AVAILABLE": "ok",
    "MAINTENANCE": "warn",
    "INACTIVE": "no",
}

CUSTOMER_STATUS_LABELS = {
    "PENDING": "Awaiting payment",
    "PENDING_VERIFICATION": "Verifying documents",
    "CONFIRMED": "Confirmed",
    "ACTIVE": "On trip",
    "COMPLETED": "Completed",
    "CANCELLED": "Cancelled",
    "REJECTED": "Rejected",
}


@register.filter
def tone(status):
    """Colour family for a booking / payment / document / car status."""
    return STATUS_TONES.get(str(status), "neutral")


@register.filter
def customer_status(status):
    return CUSTOMER_STATUS_LABELS.get(str(status), str(status).replace("_", " ").title())


@register.filter
def inr(value):
    """Format a number the Indian way: 1234567 -> 12,34,567."""
    try:
        amount = Decimal(str(value)).quantize(Decimal("1"))
    except (InvalidOperation, ValueError, TypeError):
        return value
    sign, digits = ("-", str(-amount)) if amount < 0 else ("", str(amount))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"{sign}{digits}"


@register.filter
def split_csv(value):
    return [part.strip() for part in str(value).split(",") if part.strip()]
