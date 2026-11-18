"""Louer.ca (run by Rentals.ca): ads through the access Louer.ca issues the desk. Its site loads ads from a GraphQL API with a token the page itself fetches; the desk does not take that token. Its listing data includes landlords' names, phones and emails, so ask them which fields the issued access returns.

See sources/api_feed.py for how the reader is set up once access is issued.
"""
from . import api_feed
from .http import fetch


def collect(settings, log=print, fetcher=fetch, known=None):
    return api_feed.collect("louer", settings, log, fetcher, known)
