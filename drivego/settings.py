import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

try:
    import dj_database_url
except ImportError:  # pragma: no cover
    dj_database_url = None

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-drivego-dev-only-change-me")
_on_vercel = os.environ.get("VERCEL") == "1"
DEBUG = os.environ.get("DEBUG", "0" if _on_vercel else "1") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost,testserver,.vercel.app,.localhost").split(",") if h.strip()]

# Behind a reverse proxy (Vercel/load balancer) the request is already https,
# so let Django trust the X-Forwarded-Proto header for is_secure()/absolute URLs.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rental",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "drivego.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "rental.context_processors.frontend_config",
            ],
        },
    },
]

WSGI_APPLICATION = "drivego.wsgi.application"

_database_url = os.environ.get("DATABASE_URL", "")
if _database_url and dj_database_url:
    DATABASES = {"default": dj_database_url.parse(_database_url, conn_max_age=600)}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/login/"

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

CLERK_PUBLISHABLE_KEY = os.environ.get("CLERK_PUBLISHABLE_KEY", os.environ.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", ""))
CLERK_SECRET_KEY = os.environ.get("CLERK_SECRET_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SUPABASE_DOC_BUCKET = os.environ.get("SUPABASE_DOC_BUCKET", "documents")
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_MOCK = not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)

TAX_RATE = float(os.environ.get("TAX_RATE", "0.18"))
DELIVERY_CHARGE = int(os.environ.get("DELIVERY_CHARGE", "300"))
BOOKING_HOLD_MINUTES = int(os.environ.get("BOOKING_HOLD_MINUTES", "60"))
