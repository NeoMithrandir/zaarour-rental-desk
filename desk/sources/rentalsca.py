"""Rentals.ca: ads through the listings feed or API Rentals.ca issues the desk (every page sits behind a bot check).

See sources/api_feed.py for how the reader is set up once access is issued.
"""
from . import api_feed
from .http import fetch


def collect(settings, log=print, fetcher=fetch, known=None):
    return api_feed.collect("rentalsca", settings, log, fetcher, known)
