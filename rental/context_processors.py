from django.conf import settings

from .models import Customer


def frontend_config(request):
    customer = None
    clerk_user_id = request.session.get("clerk_user_id")
    if clerk_user_id:
        customer = Customer.objects.filter(clerk_user_id=clerk_user_id).first()
    return {
        "clerk_key": settings.CLERK_PUBLISHABLE_KEY,
        "TAX_PERCENT": int(round(settings.TAX_RATE * 100)),
        "DELIVERY_CHARGE": settings.DELIVERY_CHARGE,
        "current_customer": customer,
    }
