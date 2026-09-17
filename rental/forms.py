import re
from datetime import datetime, timedelta

from django import forms
from django.utils import timezone

from .models import Booking

ALLOWED_DOC_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
MAX_DOC_BYTES = 5 * 1024 * 1024
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


class DocumentUploadForm(forms.Form):
    driving_license = forms.FileField(required=False)
    govt_id = forms.FileField(required=False)

    def clean_file(self, field_name):
        uploaded = self.cleaned_data.get(field_name)
        if not uploaded:
            return None
        if uploaded.content_type not in ALLOWED_DOC_TYPES:
            raise forms.ValidationError("Accepted formats: JPG, PNG, PDF.")
        if uploaded.size > MAX_DOC_BYTES:
            raise forms.ValidationError("Maximum file size is 5 MB.")
        return uploaded

    def clean_driving_license(self):
        return self.clean_file("driving_license")

    def clean_govt_id(self):
        return self.clean_file("govt_id")
