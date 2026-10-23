# bierbot-daten

Der Abruf verwendet pro Lauf eine gemeinsame `requests.Session()` fuer beide
Untappd-Profile. Die Session uebernimmt Server-Cookies innerhalb des Laufs und
sendet Firefox-Header. Zwischen den Profilen liegen zufaellig 1,5 bis 3,5 Sekunden.
Verbindungsaufbau und Lesen haben getrennte Zeitlimits von 5 bzw. 20 Sekunden.

Abhaengigkeiten installieren und Tests ohne externe HTTP-Anfragen ausfuehren:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Nach jedem Abruffehler bleiben weitere Untappd-Anfragen pausiert, bis der
Workflow manuell mit `resume_untappd` gestartet wird. Cookies werden nicht
zwischen Workflow-Laeufen gespeichert.

Der manuelle Workflow **Untappd Einzel-Diagnose** fuehrt genau eine Anfrage an
das Profil Karaffenaffe aus, ohne Wiederholungen oder Weiterleitungen. Er zeigt
HTTP-Status, ausgewaehlte Antwort-Header und den Seitentitel in Logs und
Workflow-Zusammenfassung. Cookies, Zugangsdaten und der Seiteninhalt werden
nicht protokolliert. Die gespeicherte Pause und Bierstaende bleiben erhalten;
Telegram wird nicht angesprochen. Bei einem anderen HTTP-Status als 200 oder
einem Verbindungsfehler ist der Diagnose-Lauf rot, damit die Ablehnung sichtbar ist.
