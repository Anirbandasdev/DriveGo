from django.db import migrations, models


def consolidate_driving_license(apps, schema_editor):
    Document = apps.get_model("rental", "Document")
    rows = Document.objects.filter(document_type__in=["LICENSE_FRONT", "LICENSE_BACK"]).values_list(
        "booking_id", "document_type", "pk"
    )
    by_booking = {}
    for booking_id, doc_type, pk in rows:
        by_booking.setdefault(booking_id, []).append((doc_type, pk))
    for booking_id, items in by_booking.items():
        fronts = [pk for dt, pk in items if dt == "LICENSE_FRONT"]
        backs = [pk for dt, pk in items if dt == "LICENSE_BACK"]
        keep = (fronts or backs)[0]
        Document.objects.filter(pk__in=[pk for dt, pk in items if pk != keep]).delete()
        Document.objects.filter(pk=keep).update(document_type="DRIVING_LICENSE")


class Migration(migrations.Migration):

    dependencies = [
        ("rental", "0005_booking_admin_note_document_rejection_reason_and_more"),
    ]

    operations = [
        migrations.RunPython(consolidate_driving_license, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="document",
            name="document_type",
            field=models.CharField(
                choices=[("DRIVING_LICENSE", "Driving License"), ("GOVT_ID", "Government ID")],
                max_length=20,
            ),
        ),
    ]