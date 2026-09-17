import hashlib
import os
from functools import lru_cache

from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage


@lru_cache(maxsize=None)
def _version(path):
    found = finders.find(path)
    if found:
        with open(found, "rb") as fh:
            return hashlib.md5(fh.read()).hexdigest()[:10]
    return (os.environ.get("VERCEL_GIT_COMMIT_SHA") or os.environ.get("VERCEL_DEPLOYMENT_ID") or "")[:10]


class VersionedStaticFilesStorage(StaticFilesStorage):
    """Static URLs change whenever the file changes, so they are safe to cache for a long time."""

    def url(self, name):
        url = super().url(name)
        version = _version(name)
        return f"{url}?v={version}" if version else url
