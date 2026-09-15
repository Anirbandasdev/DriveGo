from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


def _blocking_q(car, pickup, drop):
    """Bookings that block ``car`` for the [pickup, drop] window.

    Unpaid PENDING holds only block within ``BOOKING_HOLD_MINUTES`` of creation,
    so abandoned or failed checkouts release the car automatically instead of
    blocking it forever.
    """
    cutoff = timezone.now() - timedelta(minutes=getattr(settings, "BOOKING_HOLD_MINUTES", 60))
    statuses = [Booking.Status.PENDING, Booking.Status.PENDING_VERIFICATION, Booking.Status.CONFIRMED, Booking.Status.ACTIVE]
    return (
        Q(car=car, status__in=statuses, pickup_datetime__lt=drop, dropoff_datetime__gt=pickup)
        & (~Q(status=Booking.Status.PENDING) | Q(created_at__gte=cutoff))
    )


class Location(models.Model):
    name = models.CharField(max_length=100, unique=True)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100, default="Kolkata")
    phone = models.CharField(max_length=20, blank=True)
    image_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "locations"

    def __str__(self):
        return self.name


class Car(models.Model):
    TRANSMISSION_CHOICES = [("Automatic", "Automatic"), ("Manual", "Manual")]
    FUEL_CHOICES = [("Petrol", "Petrol"), ("Diesel", "Diesel")]
    STATUS_CHOICES = [
        ("AVAILABLE", "Available"),
        ("MAINTENANCE", "Maintenance"),
        ("INACTIVE", "Inactive"),
    ]

    CATEGORY_CHOICES = [("Hatchback", "Hatchback"), ("Sedan", "Sedan"), ("SUV", "SUV"), ("Luxury", "Luxury")]

    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="cars")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="SUV")
    brand = models.CharField(max_length=100)
    model = models.CharField(max_length=100)
    registration_number = models.CharField(max_length=20, unique=True)
    seats = models.PositiveIntegerField(default=5)
    transmission = models.CharField(max_length=20, choices=TRANSMISSION_CHOICES, default="Manual")
    fuel_type = models.CharField(max_length=20, choices=FUEL_CHOICES, default="Petrol")
    price_per_day = models.PositiveIntegerField(default=1500)
    image_url = models.URLField(blank=True)
    features = models.CharField(max_length=255, default="AC, 5 Doors", help_text="Comma-separated feature list")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="AVAILABLE")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cars"

    def __str__(self):
        return f"{self.brand} {self.model}"

    @property
    def display_image(self):
        return self.image_url

    @property
    def feature_list(self):
        return [f.strip() for f in self.features.split(",") if f.strip()]

    def blocking_bookings(self, pickup, drop):
        return Booking.objects.filter(_blocking_q(self, pickup, drop))

    def blocked_window(self, pickup, drop):
        return CarBlock.objects.filter(car_id=self.pk, start_datetime__lt=drop, end_datetime__gt=pickup)

    def upcoming_bookings(self):
        """Bookings not yet finished (current or future) that hold this car.

        Used for listing badges: a car with any active upcoming booking is shown
        as "Booked" even if it is technically free for other windows right now.
        """
        now = timezone.now()
        return self.blocking_bookings(now, now + timedelta(days=3650))

    def is_available_for(self, pickup, drop):
        if self.status != "AVAILABLE":
            return False
        if self.blocked_window(pickup, drop).exists():
            return False
        return not self.blocking_bookings(pickup, drop).exists()

    def is_listed_available(self):
        if self.status != "AVAILABLE":
            return False
        if self.blocked_window(timezone.now(), timezone.now() + timedelta(days=3650)).exists():
            return False
        return not self.upcoming_bookings().exists()

    def next_free_after(self, pickup, drop):
        clash = self.blocking_bookings(pickup, drop).order_by("dropoff_datetime").last()
        return clash.dropoff_datetime if clash else None


class Customer(models.Model):
    ROLE_CHOICES = [("CUSTOMER", "Customer"), ("ADMIN", "Admin")]

    clerk_user_id = models.CharField(max_length=255, unique=True)
    email = models.EmailField(blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    profile_image_url = models.URLField(blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="CUSTOMER")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customers"

    def __str__(self):
        return self.full_name or self.email or self.clerk_user_id

    @property
    def is_admin(self):
        return self.role == "ADMIN"


class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PENDING_VERIFICATION = "PENDING_VERIFICATION", "Pending Verification"
        CONFIRMED = "CONFIRMED", "Confirmed"
        ACTIVE = "ACTIVE", "Active"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        REJECTED = "REJECTED", "Rejected"

    class PaymentStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"

    DELIVERY_CHOICES = [("STORE_PICKUP", "Pickup from Store"), ("HOME_DELIVERY", "Home Delivery")]

    booking_id = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="bookings")
    car = models.ForeignKey(Car, on_delete=models.PROTECT, related_name="bookings")
    pickup_location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="pickup_bookings")
    pickup_datetime = models.DateTimeField()
    dropoff_datetime = models.DateTimeField()
    delivery_method = models.CharField(max_length=20, choices=DELIVERY_CHOICES, default="STORE_PICKUP")
    delivery_address = models.CharField(max_length=255, blank=True)
    delivery_city = models.CharField(max_length=100, blank=True)
    delivery_pincode = models.CharField(max_length=10, blank=True)
    delivery_instructions = models.TextField(blank=True)
    rental_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    delivery_charge = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    razorpay_payment_id = models.CharField(max_length=100, blank=True)
    payment_method = models.CharField(max_length=30, blank=True)
    admin_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bookings"
        ordering = ["-created_at"]

    def __str__(self):
        return self.booking_id

    @property
    def rental_days(self):
        days = (self.dropoff_datetime.date() - self.pickup_datetime.date()).days
        return max(days, 1)

    def calculate_price(self):
        rate = Decimal(self.car.price_per_day)
        rental = rate * self.rental_days
        delivery = Decimal(settings.DELIVERY_CHARGE) if self.delivery_method == "HOME_DELIVERY" else Decimal("0")
        tax = (rental + delivery) * Decimal(str(settings.TAX_RATE))
        return {
            "rental_amount": rental.quantize(Decimal("1")),
            "delivery_charge": delivery.quantize(Decimal("1")),
            "tax_amount": tax.quantize(Decimal("1")),
            "total_amount": (rental + delivery + tax).quantize(Decimal("1")),
        }

    def apply_price(self):
        for key, value in self.calculate_price().items():
            setattr(self, key, value)

    @classmethod
    def overlaps(cls, car, pickup, drop, exclude_id=None):
        qs = cls.objects.filter(_blocking_q(car, pickup, drop))
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        return qs


def booking_document_path(instance, filename):
    return f"documents/{instance.booking.booking_id}/{instance.document_type}_{filename}"


class Document(models.Model):
    class DocType(models.TextChoices):
        LICENSE_FRONT = "LICENSE_FRONT", "Driving License (Front)"
        LICENSE_BACK = "LICENSE_BACK", "Driving License (Back)"
        GOVT_ID = "GOVT_ID", "Government ID"

    class VerificationStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        VERIFIED = "VERIFIED", "Verified"
        REJECTED = "REJECTED", "Rejected"

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=20, choices=DocType.choices)
    file = models.FileField(upload_to=booking_document_path)
    file_reference = models.CharField(max_length=500, blank=True, help_text="Supabase Storage path when remote storage is configured")
    verification_status = models.CharField(max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.PENDING)
    rejection_reason = models.CharField(max_length=300, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "documents"
        unique_together = [("booking", "document_type")]

    def __str__(self):
        return f"{self.booking.booking_id} / {self.get_document_type_display()}"

    def mark_verified(self):
        self.verification_status = self.VerificationStatus.VERIFIED
        self.verified_at = timezone.now()
        self.save(update_fields=["verification_status", "verified_at"])

    @property
    def view_url(self):
        if self.file_reference:
            return f"{settings.SUPABASE_URL}/storage/v1/object/public/{self.file_reference}"
        try:
            return self.file.url
        except Exception:
            return ""


class CarBlock(models.Model):
    car = models.ForeignKey(Car, on_delete=models.CASCADE, related_name="blocks")
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "car_blocks"
        ordering = ["start_datetime"]

    def __str__(self):
        return f"{self.car} blocked {self.start_datetime:%d %b} - {self.end_datetime:%d %b}"


class SiteSetting(models.Model):
    key = models.CharField(max_length=50, unique=True)
    value = models.CharField(max_length=255, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings"

    def __str__(self):
        return f"{self.key}={self.value}"

    @classmethod
    def get(cls, key, default=""):
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        cls.objects.update_or_create(key=key, defaults={"value": value})

