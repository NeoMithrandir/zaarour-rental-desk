"""How an approved message reaches the landlord on each board.

No board lets the desk submit its contact form by itself: every one asks for a sign-in, a CAPTCHA or bot
check, or both, and the desk never gets around those (BOARDS.md). So sending is assisted, and always
Stefan's click:

1. Approve (or "Open to send" in To send) copies the message and opens the ad, with the message and
   Stefan's contact details tucked in the address after "#zdesk=" (the part after # never leaves the browser).
2. On the ad, Stefan opens the board's message box (signed in as himself) and clicks the "Fill from desk"
   bookmark: it puts the message in the box, and his name, email and phone in empty contact fields.
3. He reads it, clicks the board's own Send button (and answers the board's CAPTCHA if it asks), and
   the desk asks him whether it went.

If the bookmark can't find the box, the message is still on the clipboard: ⌘V.
"""
from .capture import bookmarklet

# needs: what the board asks before a message goes. checked: seen on the board (date), or "" = to confirm.
ROUTES = {
    "kijiji": {"name": "Kijiji", "where": "the Send message box on the ad",
               "needs": ["Kijiji account, signed in"], "checked": ""},
    "craigslist": {"name": "Craigslist", "where": "Reply, then email: Craigslist gives a relay address, the message goes from your mailbox",
                   "needs": ["a CAPTCHA before the reply address shows", "your mail app or webmail"], "checked": ""},
    "logisquebec": {"name": "LogisQuébec", "where": "the contact form on the ad (name, email, phone, message)",
                    "needs": ["to confirm: possible CAPTCHA"], "checked": ""},
    "livrent": {"name": "liv.rent", "where": "the in-app chat with the landlord",
                "needs": ["liv.rent account, signed in"], "checked": ""},
    "appartogo": {"name": "Appartogo", "where": "the contact form on the ad",
                  "needs": ["to confirm: account or CAPTCHA"], "checked": ""},
    "lespac": {"name": "LesPAC", "where": "Contacter l'annonceur on the ad",
               "needs": ["LesPAC account, signed in"], "checked": ""},
    "facebook": {"name": "Facebook Marketplace", "where": "Message seller (Messenger)",
                 "needs": ["Facebook account, signed in"], "checked": ""},
    "group": {"name": "Facebook group", "where": "a Messenger message to the poster",
              "needs": ["Facebook account, signed in"], "checked": ""},
    "rentalsca": {"name": "Rentals.ca", "where": "the contact form on the ad", "needs": ["bot check on every page"], "checked": ""},
    "padmapper": {"name": "PadMapper / Zumper", "where": "the contact form on the ad", "needs": ["account and bot check"], "checked": ""},
    "louer": {"name": "Louer.ca", "where": "the contact form on the ad", "needs": ["to confirm"], "checked": ""},
    "duproprio": {"name": "DuProprio", "where": "the contact form on the ad", "needs": ["to confirm"], "checked": ""},
}
OTHER = {"name": "the board", "where": "the board's message box", "needs": [], "checked": ""}


def route(source):
    return ROUTES.get(source, OTHER)


def fill_bookmarklet(port=8765):
    return bookmarklet(port, "fill-bookmarklet.js")
