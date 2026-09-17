from django.conf import settings


def frontend_config(request):
    from .views import get_customer  # the view module caches the customer on the request

    customer = get_customer(request)
    return {
        "clerk_key": settings.CLERK_PUBLISHABLE_KEY,
        "TAX_PERCENT": int(round(settings.TAX_RATE * 100)),
        "DELIVERY_CHARGE": settings.DELIVERY_CHARGE,
        "current_customer": customer,
    }
