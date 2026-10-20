"""Fetch both profiles and keep the rate-limit state between Actions runs."""

import html
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


STATUS_FILE = Path("alarm-status.json")
DATA_FILE = Path("bierstand.json")
BERLIN = ZoneInfo("Europe/Berlin")


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def output(**values):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def lese_unique(nutzer):
    req = urllib.request.Request(
        f"https://untappd.com/user/{nutzer}",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        if res.status != 200:
            raise urllib.error.HTTPError(req.full_url, res.status, "HTTP error", res.headers, None)
        print(f"=== Diagnose {nutzer} ===")
        for feld in ("Date", "Age", "Cache-Control", "CF-Cache-Status", "ETag"):
            print(f"{feld}: {res.headers.get(feld, 'nicht vorhanden')}")
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
    elif abruf.get("modus") == "stuendlich" and abruf.get("datum") != heute:
        abruf = {}
        zustand.pop("untappd_abruf", None)
        save_json(STATUS_FILE, zustand)
        print("Neuer Berliner Kalendertag: wieder viertelstuendlich")

    if abruf.get("modus") == "pausiert":
        print("Untappd-Abruf wegen HTTP 403/429 pausiert")
        output(fetch="false", notify=str(not abruf.get("benachrichtigt", False)).lower())
        return
    if abruf.get("modus") == "stuendlich" and os.environ.get("SCHEDULE") != "7 * * * *":
        print("Viertelstundenlauf im Stundenmodus uebersprungen")
        output(fetch="false", notify=str(not abruf.get("benachrichtigt", False)).lower())
        return

    try:
        karaffe, _ = lese_unique("Karaffenaffe")
        fettleber, fettleber_total = lese_unique("Fettleber")
    except urllib.error.HTTPError as fehler:
        code = fehler.code
        if code in (403, 429):
            modus = "pausiert"
        elif code == 408 or 500 <= code <= 599:
            modus = "stuendlich"
        else:
            raise
        # Keep the first incident's notification state during this Berlin day.
        if not (abruf.get("modus") == modus and abruf.get("datum") == heute):
            abruf = {"modus": modus, "datum": heute, "http_status": code, "benachrichtigt": False}
        zustand["untappd_abruf"] = abruf
        save_json(STATUS_FILE, zustand)
        print(f"Untappd HTTP {code}: Modus {modus}")
        output(fetch="false", notify=str(not abruf["benachrichtigt"]).lower())
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
