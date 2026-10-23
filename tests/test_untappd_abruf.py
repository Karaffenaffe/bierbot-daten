import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import untappd_abruf as abruf


class Clock:
    current = datetime(2026, 10, 20, 12, 22, tzinfo=abruf.BERLIN)

    @classmethod
    def now(cls, tz):
        return cls.current.astimezone(tz)


class AbrufTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.status = Path(self.temp.name) / "alarm-status.json"
        self.data = Path(self.temp.name) / "bierstand.json"
        self.outputs = Path(self.temp.name) / "outputs"
        self.status.write_text('{"unter30": false}', encoding="utf-8")
        self.data.write_text('{"fettleber_total": 100}', encoding="utf-8")
        self.patches = [
            patch.object(abruf, "STATUS_FILE", self.status),
            patch.object(abruf, "DATA_FILE", self.data),
            patch.object(abruf, "datetime", Clock),
            patch.dict(
                os.environ,
                {
                    "GITHUB_OUTPUT": str(self.outputs),
                    "SCHEDULE": "7 * * * *",
                    "RESUME_UNTAPPD": "false",
                },
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        Clock.current = datetime(2026, 10, 20, 12, 22, tzinfo=abruf.BERLIN)

    def status_json(self):
        return json.loads(self.status.read_text(encoding="utf-8"))

    def output(self):
        return dict(
            line.split("=", 1)
            for line in self.outputs.read_text(encoding="utf-8").splitlines()
        )

    def test_any_fetch_error_pauses_until_manual_resume(self):
        with patch.object(abruf, "lese_unique", side_effect=ValueError("bad page")) as fetch:
            abruf.main()
            self.assertEqual(fetch.call_count, 1)
        pause = self.status_json()["untappd_abruf"]
        self.assertEqual(pause["modus"], "pausiert")
        self.assertEqual(pause["fehler"], "ValueError")
        self.assertEqual(self.output(), {"fetch": "false", "notify": "true"})
        self.assertEqual(json.loads(self.data.read_text(encoding="utf-8")), {"fettleber_total": 100})

        state = self.status_json()
        state["untappd_abruf"]["benachrichtigt"] = True
        self.status.write_text(json.dumps(state), encoding="utf-8")
        Clock.current = datetime(2026, 10, 21, 12, 22, tzinfo=abruf.BERLIN)
        self.outputs.write_text("", encoding="utf-8")
        with patch.object(abruf, "lese_unique") as fetch:
            abruf.main()
            fetch.assert_not_called()
        self.assertEqual(self.output(), {"fetch": "false", "notify": "false"})

        os.environ["RESUME_UNTAPPD"] = "true"
        self.outputs.write_text("", encoding="utf-8")
        with patch.object(abruf, "lese_unique", side_effect=[(140, 200), (120, 105)]) as fetch:
            abruf.main()
            self.assertEqual(fetch.call_count, 2)
        self.assertNotIn("untappd_abruf", self.status_json())
        self.assertEqual(self.output(), {"fetch": "true", "notify": "false"})

    def test_http_403_stays_paused(self):
        error = urllib.error.HTTPError("https://untappd.com/user/Karaffenaffe", 403, "", {}, None)
        with patch.object(abruf, "lese_unique", side_effect=error):
            abruf.main()
        pause = self.status_json()["untappd_abruf"]
        self.assertEqual(pause["modus"], "pausiert")
        self.assertEqual(pause["http_status"], 403)
        self.assertEqual(self.output(), {"fetch": "false", "notify": "true"})

    def test_stale_profile_error_also_pauses(self):
        class Response:
            status = 200
            headers = {"Age": "12224", "CF-Cache-Status": "HIT"}

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

        with patch.object(abruf.urllib.request, "urlopen", return_value=Response()) as fetch:
            abruf.main()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.status_json()["untappd_abruf"]["fehler"], "StaleProfileError")
        self.assertEqual(self.output(), {"fetch": "false", "notify": "true"})
        self.assertEqual(json.loads(self.data.read_text(encoding="utf-8")), {"fettleber_total": 100})

    def test_existing_pause_is_not_cleared_on_next_day(self):
        self.status.write_text(
            json.dumps({
                "untappd_abruf": {
                    "modus": "pausiert",
                    "datum": "2026-10-20",
                    "http_status": 403,
                    "benachrichtigt": True,
                }
            }),
            encoding="utf-8",
        )
        Clock.current = datetime(2026, 10, 21, 12, 22, tzinfo=abruf.BERLIN)
        with patch.object(abruf, "lese_unique") as fetch:
            abruf.main()
            fetch.assert_not_called()
        self.assertEqual(self.output(), {"fetch": "false", "notify": "false"})


if __name__ == "__main__":
    unittest.main()
