import re
from datetime import datetime, timedelta

from django import forms
from django.utils import timezone

from .models import Booking
from .verification import check_licence, normalise

MAX_RENTAL_DAYS = 60


class BookingDatesForm(forms.Form):
    pickup_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    pickup_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    drop_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    drop_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))

    def cleaned_datetimes(self):
        pickup = timezone.make_aware(datetime.combine(self.cleaned_data["pickup_date"], self.cleaned_data["pickup_time"]))
        drop = timezone.make_aware(datetime.combine(self.cleaned_data["drop_date"], self.cleaned_data["drop_time"]))
        return pickup, drop

    def clean(self):
        cleaned = super().clean()
        if all(k in cleaned for k in ("pickup_date", "pickup_time", "drop_date", "drop_time")):
            pickup, drop = self.cleaned_datetimes()
            if drop <= pickup:
                raise forms.ValidationError("Drop-off must be after pickup.")
            if pickup < timezone.now() - timedelta(minutes=10):
                raise forms.ValidationError("Pickup time must be in the future.")
            if drop - pickup > timedelta(days=MAX_RENTAL_DAYS):
                raise forms.ValidationError(f"Trips can be at most {MAX_RENTAL_DAYS} days long.")
        return cleaned


class DeliveryForm(forms.Form):
    delivery_method = forms.ChoiceField(choices=Booking.DELIVERY_CHOICES, widget=forms.RadioSelect)
    delivery_address = forms.CharField(required=False, max_length=255)
    delivery_city = forms.CharField(required=False, max_length=100)
    delivery_pincode = forms.CharField(required=False, max_length=10)
    delivery_instructions = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("delivery_method") == "HOME_DELIVERY":
            for field in ("delivery_address", "delivery_city", "delivery_pincode"):
                if not (cleaned.get(field) or "").strip():
                    self.add_error(field, "This field is required for home delivery.")
            pincode = (cleaned.get("delivery_pincode") or "").strip()
            if pincode and not re.fullmatch(r"\d{6}", pincode):
                self.add_error("delivery_pincode", "Enter a valid 6-digit PIN code.")
        return cleaned


class DriverDetailsForm(forms.Form):
    """Who is driving, and the licence we check them on."""

    driver_choice = forms.ChoiceField(choices=Booking.DRIVER_CHOICES, widget=forms.RadioSelect, initial="SELF")
    driver_name = forms.CharField(max_length=120, label="Name as printed on the licence")
    licence_number = forms.CharField(max_length=20, label="Driving licence number")
    date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    expiry_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), label="Licence valid until")

    def __init__(self, *args, trip_start=None, trip_end=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.trip_start = trip_start
        self.trip_end = trip_end

    def clean_driver_name(self):
        name = " ".join(self.cleaned_data["driver_name"].split())
        if len(name) < 3 or not re.fullmatch(r"[A-Za-z .'-]+", name):
            raise forms.ValidationError("Enter the driver's name the way it is printed on the licence.")
        return name

    def clean_licence_number(self):
        return normalise(self.cleaned_data["licence_number"])

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        result = check_licence(
            cleaned.get("licence_number"), cleaned.get("date_of_birth"), cleaned.get("expiry_date"),
            trip_start=self.trip_start, trip_end=self.trip_end,
        )
        if not result.ok:
            self.add_error(result.field or None, result.message)
        self.result = result
        return cleaned
