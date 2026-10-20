import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
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
        self.status.write_text('{"unter30": false, "letzter_checkin_biertag": "2026-10-19"}', encoding="utf-8")
        self.data.write_text('{"fettleber_total": 100}', encoding="utf-8")
        self.patches = [
            patch.object(abruf, "STATUS_FILE", self.status),
            patch.object(abruf, "DATA_FILE", self.data),
            patch.object(abruf, "datetime", Clock),
            patch.dict(os.environ, {"GITHUB_OUTPUT": str(self.outputs), "SCHEDULE": "22,37,52 * * * *", "RESUME_UNTAPPD": "false"}),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        Clock.current = datetime(2026, 10, 20, 12, 22, tzinfo=abruf.BERLIN)

    def status_json(self):
        return json.loads(self.status.read_text(encoding="utf-8"))

    def output(self):
        return dict(line.split("=", 1) for line in self.outputs.read_text(encoding="utf-8").splitlines())

    def test_transient_error_hourly_then_next_day_quarter_hourly(self):
        error = urllib.error.HTTPError("https://untappd.com/user/Karaffenaffe", 503, "", {}, None)
        with patch.object(abruf, "lese_unique", side_effect=error) as fetch:
            abruf.main()
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.status_json()["untappd_abruf"]["modus"], "stuendlich")
        self.assertEqual(self.output(), {"fetch": "false", "notify": "true"})
        self.assertEqual(json.loads(self.data.read_text(encoding="utf-8")), {"fettleber_total": 100})

        self.outputs.write_text("", encoding="utf-8")
        with patch.object(abruf, "lese_unique") as fetch:
            abruf.main()
            fetch.assert_not_called()

        os.environ["SCHEDULE"] = "7 * * * *"
        self.outputs.write_text("", encoding="utf-8")
        with patch.object(abruf, "lese_unique", side_effect=[(140, 200), (120, 105)]):
            abruf.main()
        self.assertEqual(self.output(), {"fetch": "true", "notify": "false"})
        self.assertEqual(self.status_json()["untappd_abruf"]["modus"], "stuendlich")
        self.assertEqual(json.loads(self.data.read_text(encoding="utf-8"))["fettleber_neue_checkins"], 5)

        Clock.current = datetime(2026, 10, 21, 0, 22, tzinfo=abruf.BERLIN)
        os.environ["SCHEDULE"] = "22,37,52 * * * *"
        self.outputs.write_text("", encoding="utf-8")
        with patch.object(abruf, "lese_unique", side_effect=[(141, 201), (121, 106)]):
            abruf.main()
        self.assertNotIn("untappd_abruf", self.status_json())
        self.assertIn("letzter_checkin_biertag", self.status_json())
        self.assertEqual(self.output(), {"fetch": "true", "notify": "false"})

    def test_429_stays_paused_until_manual_resume(self):
        error = urllib.error.HTTPError("https://untappd.com/user/Karaffenaffe", 429, "", {}, None)
        with patch.object(abruf, "lese_unique", side_effect=error):
            abruf.main()
        self.assertEqual(self.status_json()["untappd_abruf"]["modus"], "pausiert")

        Clock.current = datetime(2026, 10, 21, 12, 22, tzinfo=abruf.BERLIN)
        self.outputs.write_text("", encoding="utf-8")
        with patch.object(abruf, "lese_unique") as fetch:
            abruf.main()
            fetch.assert_not_called()
        self.assertEqual(self.output(), {"fetch": "false", "notify": "true"})

        os.environ["RESUME_UNTAPPD"] = "true"
        self.outputs.write_text("", encoding="utf-8")
        with patch.object(abruf, "lese_unique", side_effect=[(140, 200), (120, 105)]):
            abruf.main()
        self.assertNotIn("untappd_abruf", self.status_json())
        self.assertEqual(self.output(), {"fetch": "true", "notify": "false"})

    def test_cache_key_changes_by_quarter_and_stale_page_is_not_saved(self):
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
        request = fetch.call_args.args[0]
        self.assertIn("bierbot_refresh=", request.full_url)
        self.assertEqual(request.get_header("Cache-control"), "no-cache")
        self.assertEqual(self.output(), {"fetch": "false", "notify": "false"})
        self.assertEqual(json.loads(self.data.read_text(encoding="utf-8")), {"fettleber_total": 100})

        first_url = request.full_url
        Clock.current = datetime(2026, 10, 20, 12, 37, tzinfo=abruf.BERLIN)
        with patch.object(abruf.urllib.request, "urlopen", return_value=Response()) as fetch:
            with self.assertRaises(abruf.StaleProfileError):
                abruf.lese_unique("Karaffenaffe")
        self.assertNotEqual(first_url, fetch.call_args.args[0].full_url)


if __name__ == "__main__":
    unittest.main()
