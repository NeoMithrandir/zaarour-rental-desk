"""Fetching public search pages: browser-like headers, curl fallback for Macs without Python root certificates."""
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
LANG = "fr-CA,fr;q=0.9,en;q=0.8"
LD_JSON_RE = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": LANG})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.URLError as e:
        # python.org installs on macOS often lack root certificates; curl uses the system keychain.
        if "CERTIFICATE_VERIFY_FAILED" not in str(e) or not shutil.which("curl"):
            raise
        out = subprocess.run(["curl", "-sSfL", "--max-time", str(timeout), "-A", UA, "-H", f"Accept-Language: {LANG}", url],
                             capture_output=True, check=True)
        return out.stdout.decode("utf-8", "replace")


def ld_json(html):
    """Every JSON-LD block on a page (the structured data sites publish for search engines)."""
    out = []
    for m in LD_JSON_RE.finditer(html):
        try:
            out.append(json.loads(m.group(1)))
        except ValueError:
            continue
    return out


def money(text):
    """'1 195$' / '$1,200' -> 1195 / 1200."""
    m = re.search(r"\d[\d\s,.  ]*", text or "")
    if not m:
        return None
    num = re.sub(r"[.,]\d{2}\s*$", "", m.group(0).strip())  # drop cents
    digits = re.sub(r"\D", "", num)
    return int(digits) if digits else None
