from django import forms
from django.utils import timezone

from .models import Booking, Document, Location

ALLOWED_DOC_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}
MAX_DOC_BYTES = 5 * 1024 * 1024


class SearchForm(forms.Form):
    SORT_CHOICES = [("Recommended", "Recommended"), ("Low", "Price Low to High"), ("High", "Price High to Low")]

    location = forms.CharField(required=False)
    pickup_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    pickup_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    drop_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    drop_time = forms.TimeField(required=False, widget=forms.TimeInput(attrs={"type": "time"}))
    category = forms.CharField(required=False)
    max_price = forms.IntegerField(required=False, min_value=0)
    seats = forms.IntegerField(required=False, min_value=1)
    transmission = forms.CharField(required=False)
    fuel_type = forms.CharField(required=False)
    sort = forms.ChoiceField(required=False, choices=SORT_CHOICES)


class BookingDatesForm(forms.Form):
    pickup_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    pickup_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    drop_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    drop_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))

    def cleaned_datetimes(self):
        pickup = timezone.make_aware(timezone.datetime.combine(self.cleaned_data["pickup_date"], self.cleaned_data["pickup_time"]))
        drop = timezone.make_aware(timezone.datetime.combine(self.cleaned_data["drop_date"], self.cleaned_data["drop_time"]))
        return pickup, drop

    def clean(self):
        cleaned = super().clean()
        if all(k in cleaned for k in ("pickup_date", "pickup_time", "drop_date", "drop_time")):
            pickup, drop = self.cleaned_datetimes()
            if drop <= pickup:
                raise forms.ValidationError("Drop-off must be after pickup.")
            if pickup < timezone.now() - timezone.timedelta(hours=1):
                raise forms.ValidationError("Pickup time must be in the future.")
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
                if not cleaned.get(field):
                    self.add_error(field, "This field is required for home delivery.")
        return cleaned


class DocumentUploadForm(forms.Form):
    dl_front = forms.FileField(required=False)
    dl_back = forms.FileField(required=False)
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

    def clean_dl_front(self):
        return self.clean_file("dl_front")

    def clean_dl_back(self):
        return self.clean_file("dl_back")

    def clean_govt_id(self):
        return self.clean_file("govt_id")

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("dl_front") or cleaned.get("dl_back") or cleaned.get("govt_id")):
            raise forms.ValidationError("Please choose at least one document to upload.")
        return cleaned

    def required_missing(self, booking):
        have = set(booking.documents.values_list("document_type", flat=True))
        missing = []
        if Document.DocType.LICENSE_FRONT not in have and not self.cleaned_data.get("dl_front"):
            missing.append("Driving License (Front)")
        if Document.DocType.LICENSE_BACK not in have and not self.cleaned_data.get("dl_back"):
            missing.append("Driving License (Back)")
        if Document.DocType.GOVT_ID not in have and not self.cleaned_data.get("govt_id"):
            missing.append("Government ID")
        return missing
