import random

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from rental.fleet_data import FLEET
from rental.models import Booking, Car, Location


class _DryRun(Exception):
    pass


class Command(BaseCommand):
    help = (
        "Load the real DriveGo fleet (rental/fleet_data.py) and retire the old placeholder cars. "
        "Shows the plan and changes nothing unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the changes (default is a dry run).")
        parser.add_argument(
            "--old", choices=["hide", "purge", "keep"], default="hide",
            help="Cars not in the fleet list: 'hide' deletes unbooked ones and marks booked ones Inactive "
                 "(booking history kept); 'purge' deletes them together with their bookings and documents; "
                 "'keep' leaves them alone.",
        )
        parser.add_argument("--seed", type=int, default=None, help="Random seed for repeatable location picks.")
        parser.add_argument("--reshuffle", action="store_true", help="Also re-pick locations for fleet cars that already exist.")

    def handle(self, *args, **opts):
        locations = list(Location.objects.filter(is_active=True).order_by("name"))
        if not locations:
            raise CommandError("No active locations. Add a location before seeding the fleet.")
        rng = random.Random(opts["seed"])
        fleet_regs = {car["registration_number"] for car in FLEET}

        try:
            with transaction.atomic():
                self._retire_old_cars(fleet_regs, opts["old"])
                self._upsert_fleet(locations, rng, opts["reshuffle"])
                if not opts["apply"]:
                    raise _DryRun
        except _DryRun:
            self.stdout.write(self.style.WARNING("\nDry run only - nothing was saved. Re-run with --apply to write these changes."))
            return
        self.stdout.write(self.style.SUCCESS(f"\nFleet saved: {Car.objects.filter(registration_number__in=fleet_regs).count()} cars."))

    def _retire_old_cars(self, fleet_regs, mode):
        old = Car.objects.exclude(registration_number__in=fleet_regs).select_related("location").order_by("pk")
        if mode == "keep" or not old.exists():
            return
        self.stdout.write(self.style.MIGRATE_HEADING(f"Old cars ({mode}):"))
        for car in old:
            bookings = Booking.objects.filter(car=car)
            count = bookings.count()
            if mode == "purge":
                open_paid = bookings.filter(payment_status=Booking.PaymentStatus.PAID, status__in=Booking.OPEN_STATUSES)
                for b in open_paid:
                    self.stdout.write(self.style.ERROR(f"  ! deleting OPEN PAID booking {b.booking_id} ({b.user}) - refund it manually"))
                bookings.delete()
                label = f"deleted with {count} booking(s)"
                car.delete()
            elif count:
                car.status = "INACTIVE"
                car.save(update_fields=["status"])
                label = f"hidden (Inactive) - keeps {count} booking(s)"
            else:
                car.delete()
                label = "deleted (no bookings)"
            self.stdout.write(f"  - #{car.pk or '-'} {car.brand} {car.model} [{car.registration_number}] {label}")

    def _upsert_fleet(self, locations, rng, reshuffle):
        self.stdout.write(self.style.MIGRATE_HEADING("Fleet:"))
        # Random but balanced: deal locations out like cards so every branch gets a similar share.
        deck = []
        while len(deck) < len(FLEET):
            batch = locations[:]
            rng.shuffle(batch)
            deck.extend(batch)
        for data, picked in zip(FLEET, deck):
            fields = {k: v for k, v in data.items() if k != "registration_number"}
            car = Car.objects.filter(registration_number=data["registration_number"]).first()
            if car is None:
                car = Car(location=picked, status="AVAILABLE", **data)
                action = "added"
            else:
                for key, value in fields.items():
                    setattr(car, key, value)
                if reshuffle:
                    car.location = picked
                action = "updated"
            car.full_clean()  # catch too-long values here; SQLite would silently accept them
            car.save()
            self.stdout.write(f"  + {action:7} {car.brand + ' ' + car.model:<28} {car.category:<9} Rs.{car.price_per_day:>6}/day  @ {car.location.name}")
