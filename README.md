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
