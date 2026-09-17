from django.conf import settings
from django.middleware.csrf import get_token


class EnsureCsrfCookieMiddleware:
    """Always ship the csrftoken cookie on responses.

    The Clerk session-sync fetch posts to the server with X-CSRFToken, so the
    cookie must exist even before the user ever visits /login/ or /signup/.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        name = getattr(settings, "CSRF_COOKIE_NAME", "csrftoken")
        if name not in response.cookies and name not in request.COOKIES:
            response.set_cookie(
                name,
                get_token(request),
                max_age=getattr(settings, "CSRF_COOKIE_AGE", 31449600),
                httponly=False,
                samesite="Lax",
                secure=getattr(settings, "CSRF_COOKIE_SECURE", False),
            )
        return response