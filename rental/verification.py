"""Driving licence checks.

Everything here runs offline, against the rules printed on an Indian licence:
the number format issued by the RTO, a real state code, an issue year that fits
the driver's age, an expiry date that covers the trip, and a driver who is at
least 18. No paid service is involved.

``check_licence`` is the only entry point, so plugging in a real KYC API later
(Surepass, Cashfree, DigiLocker) means changing this one function.
"""

import re
from dataclasses import dataclass
from datetime import date

MIN_AGE = 18

# The two-letter code the licence starts with — the state or union territory
# whose RTO issued it.
STATE_CODES = {
    "AN": "Andaman and Nicobar Islands", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam",
    "BR": "Bihar", "CG": "Chhattisgarh", "CH": "Chandigarh", "DD": "Daman and Diu", "DL": "Delhi",
    "DN": "Dadra and Nagar Haveli", "GA": "Goa", "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana",
    "JH": "Jharkhand", "JK": "Jammu and Kashmir", "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh",
    "LD": "Lakshadweep", "MH": "Maharashtra", "ML": "Meghalaya", "MN": "Manipur", "MP": "Madhya Pradesh",
    "MZ": "Mizoram", "NL": "Nagaland", "OD": "Odisha", "OR": "Odisha", "PB": "Punjab", "PY": "Puducherry",
    "RJ": "Rajasthan", "SK": "Sikkim", "TN": "Tamil Nadu", "TR": "Tripura", "TS": "Telangana",
    "UA": "Uttarakhand", "UK": "Uttarakhand", "UP": "Uttar Pradesh", "WB": "West Bengal",
}

# SS RR YYYY NNNNNNN — state, RTO office, year of issue, then the serial number.
LICENCE_RE = re.compile(r"^(?P<state>[A-Z]{2})(?P<rto>\d{2})(?P<year>(?:19|20)\d{2})(?P<serial>\d{6,8})$")


@dataclass
class Result:
    """Outcome of a licence check. ``field`` says which input to blame."""

    ok: bool
    message: str = ""
    field: str = ""
    state: str = ""
    issue_year: int = 0

    def __bool__(self):
        return self.ok


def normalise(number):
    """Strip the spaces and dashes people type between the blocks."""
    return re.sub(r"[\s-]", "", str(number or "")).upper()


def years_between(born, on):
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def check_licence(number, dob, expiry, trip_start=None, trip_end=None, today=None):
    """Check one licence. Returns the first problem found, or an ok result."""
    today = today or date.today()
    trip_start = trip_start or today
    trip_end = trip_end or trip_start

    cleaned = normalise(number)
    match = LICENCE_RE.match(cleaned)
    if not match:
        return Result(False, "Enter the licence number as it is printed, for example WB01 20150012345.", "licence_number")

    state = match.group("state")
    if state not in STATE_CODES:
        return Result(False, f"“{state}” is not an Indian state code. The licence number starts with one, like WB or MH.", "licence_number")

    issue_year = int(match.group("year"))
    if issue_year > today.year:
        return Result(False, "The year inside the licence number is in the future. Please check the number.", "licence_number")

    if not dob:
        return Result(False, "Enter the driver's date of birth.", "date_of_birth")
    if dob >= today:
        return Result(False, "The date of birth must be in the past.", "date_of_birth")

    age = years_between(dob, trip_start)
    if age < MIN_AGE:
        return Result(False, f"The driver must be at least {MIN_AGE} years old on the pickup date.", "date_of_birth")
    if age > 100:
        return Result(False, "Please check the date of birth.", "date_of_birth")

    if issue_year < dob.year + MIN_AGE:
        return Result(False, "The licence number and the date of birth do not match — nobody is issued a licence before 18.", "licence_number")

    if not expiry:
        return Result(False, "Enter the date the licence expires.", "expiry_date")
    if expiry < today:
        return Result(False, f"This licence expired on {expiry:%d %b %Y}. Renew it before booking.", "expiry_date")
    if expiry < trip_end:
        return Result(False, f"This licence expires on {expiry:%d %b %Y}, before the trip ends on {trip_end:%d %b %Y}.", "expiry_date")
    if expiry.year < issue_year:
        return Result(False, "The expiry date is older than the year in the licence number.", "expiry_date")

    return Result(True, f"Licence verified · {STATE_CODES[state]} RTO", state=state, issue_year=issue_year)


def format_licence(number):
    """WB0120150012345 → WB01 20150012345, the way it is printed on the card."""
    cleaned = normalise(number)
    match = LICENCE_RE.match(cleaned)
    if not match:
        return cleaned
    return f"{match.group('state')}{match.group('rto')} {match.group('year')}{match.group('serial')}"


def check_report(number, dob, expiry, trip_start=None, trip_end=None, today=None):
    """The same rules as ``check_licence``, but every line shown separately for the admin."""
    today = today or date.today()
    trip_start = trip_start or today
    trip_end = trip_end or trip_start
    cleaned = normalise(number)
    match = LICENCE_RE.match(cleaned)
    rows = []

    def row(label, ok, detail):
        rows.append({"label": label, "ok": ok, "detail": detail})

    row("Number format", bool(match),
        "Matches the RTO layout: state, office, year, serial." if match else "Not the 2-letter + 13-digit layout an RTO issues.")
    state = match.group("state") if match else ""
    row("Issuing state", state in STATE_CODES,
        STATE_CODES.get(state, "Unknown state code" if state else "No state code to read"))
    age = years_between(dob, trip_start) if dob else 0
    row("Driver age", bool(dob) and MIN_AGE <= age <= 100,
        f"{age} years old on the pickup date." if dob else "No date of birth given.")
    issue_year = int(match.group("year")) if match else 0
    row("Issue year", bool(match and dob) and dob.year + MIN_AGE <= issue_year <= today.year,
        f"Issued {issue_year}, when the driver was {issue_year - dob.year}." if match and dob else "Cannot read the issue year.")
    row("Valid for the trip", bool(expiry) and expiry >= trip_end,
        f"Expires {expiry:%d %b %Y}; the trip ends {trip_end:%d %b %Y}." if expiry else "No expiry date given.")
    return rows


def provider_configured():
    """True when a paid verification service has been wired up. Off by default — the rules above are free."""
    from django.conf import settings

    return bool(getattr(settings, "LICENCE_API_URL", "") and getattr(settings, "LICENCE_API_KEY", ""))
