"""Fetch both profiles and keep the rate-limit state between Actions runs."""

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


STATUS_FILE = Path("alarm-status.json")
DATA_FILE = Path("bierstand.json")
BERLIN = ZoneInfo("Europe/Berlin")
MAX_CACHE_AGE = 60 * 60


class StaleProfileError(Exception):
    pass


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def lese_unique(nutzer):
    # A stable key per hourly slot avoids duplicate cache keys within one
    # scheduled interval while allowing the next hourly run to request a fresh representation.
    slot = int(datetime.now(timezone.utc).timestamp()) // MAX_CACHE_AGE
    url = f"https://untappd.com/user/{nutzer}?" + urllib.parse.urlencode(
        {"bierbot_refresh": slot}
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        if res.status != 200:
            raise urllib.error.HTTPError(req.full_url, res.status, "HTTP error", res.headers, None)
        print(f"=== Diagnose {nutzer} ===")
        for feld in ("Date", "Age", "Cache-Control", "CF-Cache-Status", "ETag"):
            print(f"{feld}: {res.headers.get(feld, 'nicht vorhanden')}")
        age = res.headers.get("Age")
        if age is not None and int(age) > MAX_CACHE_AGE:
            raise StaleProfileError(f"{nutzer}: Cache bereits {age} Sekunden alt")
        seite = res.read().decode("utf-8", "replace")

    seite = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", seite, flags=re.I | re.S)
    seite = re.sub(r"<[^>]+>", " ", seite)
    seite = re.sub(r"\s+", " ", html.unescape(seite))
    treffer = re.findall(r"(?<![\d,])(\d{1,3}(?:,\d{3})+|\d+)\s+Unique\b", seite, flags=re.I)
    if len(treffer) != 1:
        raise ValueError(f"Keine eindeutige Unique-Zahl fuer {nutzer}")
    zahl = int(treffer[0].replace(",", ""))
    if zahl <= 0:
        raise ValueError(f"Ungueltige Unique-Zahl fuer {nutzer}")
    total_treffer = re.findall(r"(?<![\d,])(\d{1,3}(?:,\d{3})+|\d+)\s+Total\b", seite, flags=re.I)
    if len(total_treffer) != 1:
        raise ValueError(f"Keine eindeutige Total-Zahl fuer {nutzer}")
    gesamt = int(total_treffer[0].replace(",", ""))
    return zahl, gesamt


def main():
    zustand = json.loads(STATUS_FILE.read_text(encoding="utf-8")) if STATUS_FILE.exists() else {}
    jetzt = datetime.now(BERLIN)
    heute = jetzt.date().isoformat()
    abruf = zustand.get("untappd_abruf", {})

    if os.environ.get("RESUME_UNTAPPD") == "true":
        abruf = {}
        zustand.pop("untappd_abruf", None)
        save_json(STATUS_FILE, zustand)
        print("Untappd-Abruf manuell freigegeben")
    elif abruf.get("modus") in ("pausiert", "stuendlich"):
        # Legacy hourly throttling is now a permanent stop as well. No date
        # change clears a pause; only the explicit manual resume does that.
        if abruf.get("modus") == "stuendlich":
            abruf["modus"] = "pausiert"
            abruf.setdefault("fehler", "Vorheriger Untappd-Fehler")
            zustand["untappd_abruf"] = abruf
            save_json(STATUS_FILE, zustand)
        print("Untappd-Abruf pausiert; manuelle Freigabe erforderlich")
        output(fetch="false", notify=str(not abruf.get("benachrichtigt", False)).lower())
        return

    try:
        karaffe, _ = lese_unique("Karaffenaffe")
        fettleber, fettleber_total = lese_unique("Fettleber")
    except Exception as fehler:
        code = fehler.code if isinstance(fehler, urllib.error.HTTPError) else None
        beschreibung = f"HTTP {code}" if code is not None else type(fehler).__name__
        abruf = {
            "modus": "pausiert",
            "datum": heute,
            "http_status": code,
            "fehler": beschreibung,
            "benachrichtigt": False,
        }
        zustand["untappd_abruf"] = abruf
        save_json(STATUS_FILE, zustand)
        print(f"Untappd-Abruffehler ({beschreibung}): Abruf dauerhaft pausiert")
        output(fetch="false", notify="true")
        return

    vorherige_daten = json.loads(DATA_FILE.read_text(encoding="utf-8")) if DATA_FILE.exists() else {}
    vorher_total = vorherige_daten.get("fettleber_total")
    neue_checkins = max(0, fettleber_total - int(vorher_total)) if vorher_total is not None else 0
    daten = {
        "karaffenaffe": karaffe,
        "fettleber": fettleber,
        "fettleber_total": fettleber_total,
        "fettleber_neue_checkins": neue_checkins,
        "vorsprung": karaffe - fettleber,
        "status": "ok",
        "messung": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_json(DATA_FILE, daten)
    print("Karaffenaffe:", karaffe)
    print("Fettleber:", fettleber)
    print("Vorsprung:", karaffe - fettleber)
    output(fetch="true", notify="false")


if __name__ == "__main__":
    main()
