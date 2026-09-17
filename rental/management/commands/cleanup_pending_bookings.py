from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from rental.models import Booking


class Command(BaseCommand):
    help = (
        "Deletes PENDING bookings older than twice the BOOKING_HOLD_MINUTES "
        "threshold so expired checkouts do not accumulate indefinitely."
    )

    def handle(self, *args, **options):
        hold_minutes = getattr(settings, "BOOKING_HOLD_MINUTES", 60)
        cutoff = timezone.now() - timedelta(minutes=hold_minutes * 2)
        # Never delete a hold that ever saw a payment attempt: it is the only
        # record linking a Razorpay payment (or refund) back to a customer.
        stale = Booking.objects.filter(
            status=Booking.Status.PENDING, created_at__lt=cutoff
        ).exclude(payment_status=Booking.PaymentStatus.PAID).filter(razorpay_payment_id="")
        count = stale.count()
        if count:
            stale.delete()
            self.stdout.write(
                self.style.SUCCESS(f"Deleted {count} expired PENDING booking(s).")
            )
        else:
            self.stdout.write("No expired PENDING bookings to clean up.")
