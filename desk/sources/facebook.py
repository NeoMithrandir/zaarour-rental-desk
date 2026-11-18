"""Facebook Marketplace and groups: scaffold only. Meta offers no API to read Marketplace or group listings (its Marketplace partner APIs post listings; the Groups API is retired), and pages sit behind a sign-in the desk never uses. This reads a feed only if Meta or a licensed partner issues one; until then, the capture button.

See sources/api_feed.py for how the reader is set up once access is issued.
"""
from . import api_feed
from .http import fetch


def collect(settings, log=print, fetcher=fetch, known=None):
    return api_feed.collect("facebook", settings, log, fetcher, known)
