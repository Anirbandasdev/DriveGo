from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


def _blocking_q(car, pickup, drop):
    """Bookings that block ``car`` (a car or an iterable of car ids) for the [pickup, drop] window.

    Unpaid PENDING holds only block within ``BOOKING_HOLD_MINUTES`` of creation,
    so abandoned or failed checkouts release the car automatically instead of
    blocking it forever.
    """
    cutoff = timezone.now() - timedelta(minutes=getattr(settings, "BOOKING_HOLD_MINUTES", 60))
    statuses = [Booking.Status.PENDING, Booking.Status.PENDING_VERIFICATION, Booking.Status.CONFIRMED, Booking.Status.ACTIVE]
    return (
        (Q(car=car) if isinstance(car, models.Model) else Q(car_id__in=list(car)))
        & Q(status__in=statuses, pickup_datetime__lt=drop, dropoff_datetime__gt=pickup)
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
        return Car.busy_periods_for([self], start, end, exclude_ids)[self.pk]

    @staticmethod
    def busy_periods_for(cars, start, end, exclude_ids=None):
        """``{car_id: merged busy periods}`` for many cars using two queries in total."""
        car_ids = [car.pk for car in cars]
        by_car = {car_id: [] for car_id in car_ids}
        bookings = Booking.objects.filter(_blocking_q(car_ids, start, end))
        if exclude_ids:
            bookings = bookings.exclude(pk__in=exclude_ids)
        for car_id, s, e in bookings.values_list("car_id", "pickup_datetime", "dropoff_datetime"):
            by_car[car_id].append((s, e))
        blocks = CarBlock.objects.filter(car_id__in=car_ids, start_datetime__lt=end, end_datetime__gt=start)
        for car_id, s, e in blocks.values_list("car_id", "start_datetime", "end_datetime"):
            by_car[car_id].append((s, e))
        return {car_id: _merge_periods(periods) for car_id, periods in by_car.items()}

    @staticmethod
    def search_window(pickup, drop, horizon_days=180):
        """The period to load busy times for, so clashes and the next free slot can both be found."""
        start = min(pickup, timezone.now())
        return start, max(pickup, timezone.now()) + timedelta(days=horizon_days) + (drop - pickup)

    @staticmethod
    def next_start_in(periods, pickup, drop, horizon_days=180):
        """Walk merged busy ``periods`` to find where a trip of the same length fits.

        Back-to-back bookings and admin blocks are skipped over instead of suggesting
        a slot that still clashes.
        """
        duration = drop - pickup
        candidate = max(pickup, timezone.now())
        limit = candidate + timedelta(days=horizon_days)
        for s, e in periods:
            if s >= candidate + duration:
                break
            candidate = max(candidate, e)
        # Round up to the next half hour so suggestions read like real pickup times.
        extra = (30 - candidate.minute % 30) % 30
        if extra or candidate.second or candidate.microsecond:
            candidate = (candidate + timedelta(minutes=extra or 30)).replace(second=0, microsecond=0)
        if candidate > limit or overlapping(periods, candidate, candidate + duration):
            return None
        return candidate


def _merge_periods(intervals):
    merged = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def overlapping(periods, start, end):
    """The busy periods that overlap ``[start, end]``."""
    return [(s, e) for s, e in periods if s < end and e > start]


class Customer(models.Model):
    ROLE_CHOICES = [("CUSTOMER", "Customer"), ("ADMIN", "Admin")]

    clerk_user_id = models.CharField(max_length=255, unique=True)
    email = models.EmailField(blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    profile_image_url = models.URLField(blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="CUSTOMER")
    # Licence details checked once and reused on later bookings.
    licence_number = models.CharField(max_length=20, blank=True)
    licence_name = models.CharField(max_length=120, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    licence_expiry = models.DateField(null=True, blank=True)
    licence_checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "customers"

    def __str__(self):
        return self.full_name or self.email or self.clerk_user_id

    @property
    def is_admin(self):
        return self.role == "ADMIN"

    def licence_valid_until(self, end):
        """True when we hold a checked licence that is still valid on ``end``."""
        return bool(self.licence_checked_at and self.licence_expiry and self.licence_expiry >= end)


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
    DRIVER_CHOICES = [("SELF", "I will drive"), ("OTHER", "Someone else will drive")]

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
    driver_choice = models.CharField(max_length=10, choices=DRIVER_CHOICES, default="SELF")
    driver_name = models.CharField(max_length=120, blank=True)
    driver_licence_number = models.CharField(max_length=20, blank=True)
    driver_date_of_birth = models.DateField(null=True, blank=True)
    driver_licence_expiry = models.DateField(null=True, blank=True)
    driver_checked_at = models.DateTimeField(null=True, blank=True)
    # The licence carries over between bookings; how to collect the car does not,
    # so every booking asks once and this records that it was answered.
    delivery_chosen = models.BooleanField(default=False)
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

    @property
    def driver_checked(self):
        return bool(self.driver_checked_at and self.driver_licence_number)

    @property
    def driver_is_self(self):
        return self.driver_choice == "SELF"

    def set_driver(self, choice, name, licence_number, date_of_birth, licence_expiry):
        """Record the licence this trip was booked on. A snapshot: later profile edits leave it alone."""
        self.driver_choice = choice
        self.driver_name = name
        self.driver_licence_number = licence_number
        self.driver_date_of_birth = date_of_birth
        self.driver_licence_expiry = licence_expiry
        self.driver_checked_at = timezone.now()

    def copy_driver_from(self, other):
        """Carry driver details over to a replacement hold, or from the customer's profile."""
        if isinstance(other, Customer):
            if not other.licence_valid_until(timezone.localdate(self.dropoff_datetime)):
                return False
            self.set_driver("SELF", other.licence_name or other.full_name, other.licence_number,
                            other.date_of_birth, other.licence_expiry)
            return True
        if not other.driver_checked:
            return False
        self.set_driver(other.driver_choice, other.driver_name, other.driver_licence_number,
                        other.driver_date_of_birth, other.driver_licence_expiry)
        self.delivery_chosen = other.delivery_chosen
        return True

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
            return "confirmation"
        if not self.driver_checked:
            return "driver"
        if not self.delivery_chosen:
            return "delivery"
        return "summary"

    @classmethod
    def overlaps(cls, car, pickup, drop, exclude_id=None):
        qs = cls.objects.filter(_blocking_q(car, pickup, drop))
        if exclude_id:
            qs = qs.exclude(pk=exclude_id)
        return qs


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

