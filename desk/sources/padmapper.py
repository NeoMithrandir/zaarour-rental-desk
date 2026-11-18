"""PadMapper / Zumper (one company): ads through the feed or API Zumper issues the desk (pages sit behind bot checks; robots.txt disallows /api).

See sources/api_feed.py for how the reader is set up once access is issued.
"""
from . import api_feed
from .http import fetch


def collect(settings, log=print, fetcher=fetch, known=None):
    return api_feed.collect("padmapper", settings, log, fetcher, known)
