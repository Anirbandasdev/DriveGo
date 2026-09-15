from django.conf import settings

from .models import Customer


def frontend_config(request):
    customer = None
    clerk_user_id = request.session.get("clerk_user_id")
    if clerk_user_id:
        customer = Customer.objects.filter(clerk_user_id=clerk_user_id).first()
    return {
        "CLERK_PUBLISHABLE_KEY": settings.CLERK_PUBLISHABLE_KEY,
        "clerk_key": settings.CLERK_PUBLISHABLE_KEY,
        "RAZORPAY_KEY_ID": settings.RAZORPAY_KEY_ID,
        "TAX_PERCENT": int(round(settings.TAX_RATE * 100)),
        "DELIVERY_CHARGE": settings.DELIVERY_CHARGE,
        "current_customer": customer,
    }
