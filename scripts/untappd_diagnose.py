"""Make exactly one profile request without changing the bot's saved state."""

import html
import json
import os
from pathlib import Path

import requests

from untappd_abruf import BROWSER_HEADERS, log_response, profil_url


def main():
    print("Einzel-Diagnose: ein Profil, keine Wiederholung, keine Weiterleitung.")
    print("Gespeicherte Pause und Bierstaende bleiben unveraendert.")
    try:
        with requests.Session() as session:
            session.headers.update(BROWSER_HEADERS)
            with session.get(
                profil_url("Karaffenaffe"),
                timeout=(5, 20),
                allow_redirects=False,
            ) as res:
                diagnose = log_response(res, "Karaffenaffe")
    except requests.RequestException as fehler:
        # Exception messages can contain request details; record only the type.
        diagnose = {"profile": "Karaffenaffe", "error": type(fehler).__name__}
        print("Untappd-Diagnose: " + json.dumps(diagnose, ensure_ascii=True))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("## Untappd: einzelne Diagnose-Anfrage\n\n")
            handle.write("Ein Profil; keine Wiederholung oder Weiterleitung.\n\n")
            handle.write("Die gespeicherte Pause und die Bierstaende wurden nicht geaendert.\n\n")
            handle.write("<pre>" + html.escape(json.dumps(diagnose, ensure_ascii=True, indent=2)) + "</pre>\n")
    return 0 if diagnose.get("status") == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
