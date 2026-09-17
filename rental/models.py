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
    image_url = models.URLField(max_length=500, blank=True)
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

    def blocking_bookings(self, pickup, drop, exclude_ids=None):
        qs = Booking.objects.filter(_blocking_q(self, pickup, drop))
        if exclude_ids:
            qs = qs.exclude(pk__in=exclude_ids)
        return qs

    def blocked_window(self, pickup, drop):
        return CarBlock.objects.filter(car_id=self.pk, start_datetime__lt=drop, end_datetime__gt=pickup)

    def is_available_for(self, pickup, drop, exclude_ids=None):
        if self.status != "AVAILABLE":
            return False
        if self.blocked_window(pickup, drop).exists():
            return False
        return not self.blocking_bookings(pickup, drop, exclude_ids).exists()

    def busy_periods(self, start, end, exclude_ids=None):
        """Merged, sorted ``[(from, to)]`` intervals inside ``[start, end]`` when the
        car cannot be booked (bookings that hold it plus admin blocks)."""
        intervals = list(self.blocking_bookings(start, end, exclude_ids).values_list("pickup_datetime", "dropoff_datetime"))
        intervals += list(self.blocked_window(start, end).values_list("start_datetime", "end_datetime"))
        merged = []
        for s, e in sorted(intervals):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        return [(s, e) for s, e in merged]

    def next_available_start(self, pickup, drop, exclude_ids=None, horizon_days=180):
        """Earliest pickup at or after ``pickup`` where a trip of the same length fits.

        Walks the merged busy periods, so back-to-back bookings and admin blocks
        are skipped over instead of suggesting a slot that still clashes.
        Returns ``None`` when the car is out of service or nothing fits in the horizon.
        """
        if self.status != "AVAILABLE":
            return None
        duration = drop - pickup
        candidate = max(pickup, timezone.now())
        limit = candidate + timedelta(days=horizon_days)
        for s, e in self.busy_periods(candidate, limit + duration, exclude_ids):
            if s >= candidate + duration:
                break
            candidate = max(candidate, e)
        # Round up to the next half hour so suggestions read like real pickup times.
        extra = (30 - candidate.minute % 30) % 30
        if extra or candidate.second or candidate.microsecond:
            candidate = (candidate + timedelta(minutes=extra or 30)).replace(second=0, microsecond=0)
        if candidate > limit:
            return None
        return candidate if self.is_available_for(candidate, candidate + duration, exclude_ids) else None


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

    OPEN_STATUSES = (Status.PENDING, Status.PENDING_VERIFICATION, Status.CONFIRMED, Status.ACTIVE)
    CLOSED_STATUSES = (Status.COMPLETED, Status.CANCELLED, Status.REJECTED)

    @property
    def is_paid(self):
        return self.payment_status == self.PaymentStatus.PAID

    @property
    def is_closed(self):
        return self.status in self.CLOSED_STATUSES

    @property
    def hold_expired(self):
        """An unpaid PENDING booking whose reservation window has lapsed."""
        if self.status != self.Status.PENDING or self.is_paid or not self.created_at:
            return False
        return self.created_at < timezone.now() - timedelta(minutes=getattr(settings, "BOOKING_HOLD_MINUTES", 60))

    @property
    def hold_expires_at(self):
        if self.status != self.Status.PENDING or not self.created_at:
            return None
        return self.created_at + timedelta(minutes=getattr(settings, "BOOKING_HOLD_MINUTES", 60))

    def document_map(self):
        return {d.document_type: d for d in self.documents.all()}

    def missing_documents(self):
        have = self.document_map()
        return [label for value, label in Document.DocType.choices if value not in have]

    def rejected_documents(self):
        return [d for d in self.documents.all() if d.verification_status == Document.VerificationStatus.REJECTED]

    def documents_verified(self):
        docs = self.document_map()
        return len(docs) == len(Document.DocType.choices) and all(
            d.verification_status == Document.VerificationStatus.VERIFIED for d in docs.values()
        )

    @property
    def customer_can_cancel(self):
        """Customers may cancel until the trip starts; once the car is out, only staff can."""
        return self.status in (self.Status.PENDING, self.Status.PENDING_VERIFICATION, self.Status.CONFIRMED) and (
            self.pickup_datetime > timezone.now() or not self.is_paid
        )

    @property
    def checkout_step(self):
        """Name of the URL the customer should resume the booking flow at."""
        if self.is_closed:
            return None
        if self.is_paid:
            return "verification" if self.rejected_documents() else "confirmation"
        if self.missing_documents() or self.rejected_documents():
            return "verification"
        return "summary"

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
        DRIVING_LICENSE = "DRIVING_LICENSE", "Driving License"
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

    @property
    def storage_name(self):
        return self.file_reference or (self.file.name if self.file else "")

    @property
    def is_pdf(self):
        return self.storage_name.lower().endswith(".pdf")



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

