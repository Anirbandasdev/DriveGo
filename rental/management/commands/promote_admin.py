from django.core.management.base import BaseCommand, CommandError

from rental.models import Customer


class Command(BaseCommand):
    help = "Grant the ADMIN role to a customer by Clerk user ID or email."

    def add_arguments(self, parser):
        parser.add_argument("identifier", help="Clerk user ID or email address")

    def handle(self, *args, **options):
        identifier = options["identifier"]
        customer = (
            Customer.objects.filter(clerk_user_id=identifier).first()
            or Customer.objects.filter(email__iexact=identifier).first()
        )
        if customer is None:
            raise CommandError(f"No customer found for {identifier!r}. They must sign in once first.")
        customer.role = "ADMIN"
        customer.save(update_fields=["role"])
        self.stdout.write(self.style.SUCCESS(f"{customer} is now an ADMIN."))
