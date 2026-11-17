"""The capture button: a bookmark Stefan clicks on an ad he is looking at in his own browser.

It is the legitimate route for boards the desk does not read by itself (Facebook Marketplace and groups,
Rentals.ca, PadMapper/Zumper, Appartogo, LesPAC, Louer.ca): Stefan browses them normally, signed in as
himself, and the button copies what is on his screen (link, title, the text he selected or the page's
own description, and the structured data the page publishes) into a pre-filled form on the desk. He
checks it and clicks Add. Nothing runs unless he clicks, and nothing reads any page but the one open.
The data travels in the address fragment (#...), which stays in the browser and is never sent to a server.
"""
import hashlib
import json
import re
import urllib.parse

from .config import UI_DIR

HOSTS = [  # (host pattern, source key)
    (r"(^|\.)facebook\.com$", "facebook"), (r"(^|\.)kijiji\.ca$", "kijiji"), (r"(^|\.)craigslist\.org$", "craigslist"),
    (r"(^|\.)rentals\.ca$", "rentalsca"), (r"(^|\.)(padmapper|zumper)\.com$", "padmapper"),
    (r"(^|\.)appartogo\.com$", "appartogo"), (r"(^|\.)lespac\.com$", "lespac"), (r"(^|\.)louer\.ca$", "louer"),
    (r"(^|\.)logisquebec\.com$", "logisquebec"), (r"(^|\.)liv\.rent$", "livrent"), (r"(^|\.)duproprio\.com$", "duproprio"),
]


def source_of(url):
    u = urllib.parse.urlsplit(url or "")
    host = u.netloc.lower().split(":")[0]
    for pattern, key in HOSTS:
        if re.search(pattern, host):
            return "group" if key == "facebook" and u.path.startswith("/groups/") else key
    return ""


def canonical_id(url, source=None):
    """The same ad gets the same id however it reached the desk (scan, alert email, capture or by hand),
    so a captured Kijiji ad merges with the scanned one instead of showing twice."""
    source = source or source_of(url)
    path = urllib.parse.urlsplit(url or "").path.rstrip("/")
    if source == "kijiji":
        m = re.search(r"/(\d+)$", path)
        if m:
            return f"kijiji:{m.group(1)}"
    if source == "craigslist" and "/d/" in path:
        return f"craigslist:{path.split('/')[-1]}"
    if source == "logisquebec":
        m = re.search(r"-l(\d+)$", path)
        if m:
            return f"logisquebec:{m.group(1)}"
    if source == "livrent" and "/building/" in path:
        return f"livrent:{path.split('/building/')[-1].split('/')[0]}"
    if source == "appartogo":
        m = re.search(r"/(\d+)$", path)
        if m:
            return f"appartogo:{m.group(1)}"
    if source == "lespac":
        m = re.search(r"_(\d+)D\d+\.jsa$", path)
        if m:
            return f"lespac:{m.group(1)}"
    if source == "facebook":
        m = re.search(r"/marketplace/item/(\d+)", path)
        if m:
            return f"facebook:{m.group(1)}"
    if not url:
        return None
    clean = urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(query="", fragment=""))
    return f"{source or 'other'}:" + hashlib.sha1(clean.encode()).hexdigest()[:12]


def bookmarklet(port=8765, name="capture-bookmarklet.js"):
    js = (UI_DIR / name).read_text(encoding="utf-8")
    js = js.replace("__DESK__", f"http://127.0.0.1:{port}/capture")
    js = re.sub(r"^\s*//.*$", "", js, flags=re.M)
    js = re.sub(r"\s*\n\s*", " ", js).strip()
    return "javascript:" + urllib.parse.quote(js, safe="(){}[]=>;:,.'!*+-/&|?_$")


def capture_page(page):
    body = (UI_DIR / "capture.html").read_text(encoding="utf-8")
    return page("Send to desk", body + '<script src="/ui/capture.js"></script>', "leads")


def install_page(page):
    link = bookmarklet()
    body = f"""<h1>The capture button</h1>
<p class="lede">For boards the desk doesn't read by itself: Facebook Marketplace and groups, Rentals.ca, PadMapper/Zumper,
Appartogo, LesPAC and Louer.ca. Browse them as usual. On an ad worth a message, click the button: the desk opens a small
window with the ad filled in, you check it and click Add. It goes through the same checks as every other ad.</p>
<ol class="steps">
<li><strong>Show the bookmarks bar</strong> in your browser (Chrome: ⌘⇧B. Safari: ⌘⇧B, or View, Show Favourites Bar).</li>
<li><strong>Drag this button onto the bar:</strong> <a class="btn primary" href="{link}" onclick="event.preventDefault(); alert('Drag this button onto your bookmarks bar, then click it on an ad.');">Send to desk</a></li>
<li><strong>On an ad</strong>, optionally select the ad's description with the mouse (it's used as the ad text), then click
<b>Send to desk</b> on the bar. The desk has to be running.</li>
</ol>
<p class="muted">The button reads only the page you are looking at, only when you click it, and only what is on screen. It does not
open other pages, sign in, or read your messages or contacts.</p>

<h2 id="fill">The Fill button</h2>
<p class="lede">For sending. When you approve a message, the desk copies it and opens the ad. Open the board's message box
(Send message, Contact, Reply), click <b>Fill from desk</b> on the bar, read the message, then click the board's own Send.</p>
<ol class="steps">
<li><strong>Drag this button onto the bar:</strong> <a class="btn primary" href="{{fill}}" onclick="event.preventDefault(); alert('Drag this button onto your bookmarks bar, then click it in a board\'s message box.');">Fill from desk</a></li>
<li><strong>On the ad</strong>, signed in to the board as yourself, open its message box and click <b>Fill from desk</b>. It fills
the message, and your name, email and phone where the form asks for them and they're empty.</li>
<li><strong>Read it and click the board's Send.</strong> If the board asks for a CAPTCHA, answer it. Back on the desk, say whether it went.</li>
</ol>
<p class="muted">The Fill button never clicks Send and never signs in. It takes the message from the ad's address (the desk adds it
after #, which stays in your browser), or from your clipboard if the board's sign-in page dropped it.</p>"""
    return page("Browser buttons", body.replace("{fill}", bookmarklet(name="fill-bookmarklet.js")), "leads")


def from_capture(data):
    """Turn the bookmarklet's payload into the add-by-hand form fields (used by tests; the page does the same in JS)."""
    ld = data.get("ld")
    if isinstance(ld, str):
        try:
            ld = json.loads(ld)
        except ValueError:
            ld = []
    found = {}

    def walk(o):
        if isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for k in ("price", "lowPrice"):
                if k in o and "price" not in found:
                    found["price"] = str(o[k])
            a = o.get("address")
            if isinstance(a, dict) and "address" not in found:
                found["address"] = ", ".join(p for p in [a.get("streetAddress"), a.get("addressLocality"),
                                                         ("QC " + a.get("postalCode", "")).strip()] if p)
            for v in o.values():
                walk(v)
    walk(ld or [])
    text = data.get("text", "")
    price = found.get("price")
    if not price:
        m = re.search(r"\$\s?(\d[\d\s,]{2,6})|(\d[\d\s,]{2,6})\s?\$", (data.get("title", "") + " " + text))
        price = (m.group(1) or m.group(2)) if m else ""
    digits = re.sub(r"\D", "", re.sub(r"[.,]\d{2}$", "", price or ""))
    return {"source": source_of(data.get("url")) or "other", "url": data.get("url", ""), "title": data.get("title", ""),
            "price": digits, "address": found.get("address", ""), "text": text, "via": "capture"}
