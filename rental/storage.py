import hashlib
import os
from functools import lru_cache

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage


def _file_version(path):
    found = finders.find(path)
    if found:
        with open(found, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()[:10]
    return (os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ.get("VERCEL_DEPLOYMENT_ID") or "")[:10]


_cached_version = lru_cache(maxsize=None)(_file_version)


def _version(path):
    # While developing, read the file every time: a cached hash would keep serving
    # the browser the old CSS after an edit.
    return _file_version(path) if settings.DEBUG else _cached_version(path)


class VersionedStaticFilesStorage(StaticFilesStorage):
    """Static URLs change whenever the file changes, so they are safe to cache for a long time."""

    def url(self, name):
        url = super().url(name)
        version = _version(name)
        return f"{url}?v={version}" if version else url
