import json
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime
from contextlib import redirect_stdout
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import untappd_abruf as abruf
import untappd_diagnose as diagnose


class Clock:
    current = datetime(2026, 10, 20, 12, 22, tzinfo=abruf.BERLIN)

    @classmethod
    def now(cls, tz):
        return cls.current.astimezone(tz)


def response(body="", status=200, headers=None, cookie=None):
    result = requests.Response()
    result.status_code = status
    result._content = body.encode("utf-8")
    result.headers.update(headers or {})
    message = Message()
    if cookie:
        message["Set-Cookie"] = cookie
    result.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=message))
    return result


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
            patch.object(abruf.time, "sleep"),
            patch.object(abruf.random, "randint", return_value=0),
            patch.object(abruf.random, "uniform", return_value=2.5),
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
        # Any unmocked transport is a test error, never an actual Untappd request.
        transport_patch = patch.object(
            requests.adapters.HTTPAdapter,
            "send",
            side_effect=AssertionError("Unexpected network request"),
        )
        self.transport = transport_patch.start()
        self.addCleanup(transport_patch.stop)
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
        self.transport.side_effect = [response(
            "<title>Just a moment...</title>",
            status=403,
            headers={"Server": "cloudflare", "cf-mitigated": "challenge", "CF-Ray": "test-ray"},
        )]
        output = io.StringIO()
        with redirect_stdout(output):
            abruf.main()
        self.assertEqual(self.transport.call_count, 1)
        log = next(line for line in output.getvalue().splitlines() if line.startswith("Untappd-Diagnose: "))
        logged = json.loads(log.removeprefix("Untappd-Diagnose: "))
        self.assertEqual(logged["status"], 403)
        self.assertEqual(logged["headers"]["cf-mitigated"], "challenge")
        self.assertEqual(logged["title"], "Just a moment...")
        pause = self.status_json()["untappd_abruf"]
        self.assertEqual(pause["modus"], "pausiert")
        self.assertEqual(pause["http_status"], 403)
        self.assertEqual(self.output(), {"fetch": "false", "notify": "true"})
        self.assertEqual(json.loads(self.data.read_text(encoding="utf-8")), {"fettleber_total": 100})

    def test_stale_profile_error_also_pauses(self):
        self.transport.side_effect = [response(headers={"Age": "12224", "CF-Cache-Status": "HIT"})]
        abruf.main()
        self.assertEqual(self.transport.call_count, 1)
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
        with patch.object(abruf.requests, "Session") as session:
            abruf.main()
            session.assert_not_called()
        abruf.time.sleep.assert_not_called()
        self.assertEqual(self.output(), {"fetch": "false", "notify": "false"})

    def test_shared_session_preserves_cookie_and_sends_headers(self):
        self.transport.side_effect = [
            response(
                "<span>140 Unique</span><span>200 Total</span>",
                cookie="demo_session=example; Path=/; Secure",
            ),
            response("<span>120 Unique</span><span>105 Total</span>"),
        ]
        abruf.main()

        self.assertEqual(self.transport.call_count, 2)
        first, second = self.transport.call_args_list
        first_request, second_request = first.args[0], second.args[0]
        self.assertIsNone(first_request.headers.get("Cookie"))
        self.assertEqual(second_request.headers["Cookie"], "demo_session=example")
        for sent in (first, second):
            self.assertEqual(sent.kwargs["timeout"], (5, 20))
            self.assertEqual(sent.args[0].headers["User-Agent"], abruf.BROWSER_HEADERS["User-Agent"])
            self.assertEqual(sent.args[0].headers["Accept-Language"], "en-US,en;q=0.9")
            self.assertIn("*/*;q=0.8", sent.args[0].headers["Accept"])
        self.assertIn("/user/Karaffenaffe?bierbot_refresh=", first_request.url)
        self.assertIn("/user/Fettleber?bierbot_refresh=", second_request.url)
        abruf.random.uniform.assert_called_once_with(1.5, 3.5)
        self.assertEqual(abruf.time.sleep.call_args_list, [call(0), call(2.5)])
        saved = json.loads(self.data.read_text(encoding="utf-8"))
        self.assertEqual(saved["vorsprung"], 20)
        self.assertEqual(saved["fettleber_neue_checkins"], 5)
        self.assertEqual(self.output(), {"fetch": "true", "notify": "false"})

    def test_delay_happens_between_profiles(self):
        events = []

        def fetch(_session, user):
            events.append(user)
            return 140, 200

        os.environ["SCHEDULE"] = ""
        with patch.object(abruf, "lese_unique", side_effect=fetch):
            abruf.time.sleep.side_effect = lambda seconds: events.append(seconds)
            abruf.main()
        self.assertEqual(events, ["Karaffenaffe", 2.5, "Fettleber"])

    def test_connection_and_read_timeouts_pause_without_retry(self):
        for error_type in (requests.ConnectTimeout, requests.ReadTimeout):
            with self.subTest(error=error_type.__name__):
                self.status.write_text('{"unter30": false}', encoding="utf-8")
                self.outputs.write_text("", encoding="utf-8")
                self.transport.reset_mock(side_effect=True)
                self.transport.side_effect = error_type("timed out")
                abruf.main()
                self.assertEqual(self.transport.call_count, 1)
                pause = self.status_json()["untappd_abruf"]
                self.assertEqual(pause["modus"], "pausiert")
                self.assertIsNone(pause["http_status"])
                self.assertEqual(pause["fehler"], error_type.__name__)
                self.assertEqual(self.output(), {"fetch": "false", "notify": "true"})
                self.assertEqual(json.loads(self.data.read_text(encoding="utf-8")), {"fettleber_total": 100})

    def test_second_profile_failure_keeps_previous_data(self):
        self.transport.side_effect = [
            response("140 Unique 200 Total"),
            requests.ReadTimeout("timed out"),
        ]
        abruf.main()
        self.assertEqual(self.transport.call_count, 2)
        self.assertEqual(self.status_json()["untappd_abruf"]["modus"], "pausiert")
        self.assertEqual(json.loads(self.data.read_text(encoding="utf-8")), {"fettleber_total": 100})
        self.assertEqual(self.output(), {"fetch": "false", "notify": "true"})


class DiagnoseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.status = root / "alarm-status.json"
        self.data = root / "bierstand.json"
        self.summary = root / "summary.md"
        self.status.write_text('{"untappd_abruf": {"modus": "pausiert"}}', encoding="utf-8")
        self.data.write_text('{"karaffenaffe": 2383}', encoding="utf-8")
        self.original_status = self.status.read_bytes()
        self.original_data = self.data.read_bytes()
        patches = [
            patch.object(abruf, "STATUS_FILE", self.status),
            patch.object(abruf, "DATA_FILE", self.data),
            patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(self.summary)}),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        transport_patch = patch.object(
            requests.adapters.HTTPAdapter,
            "send",
            side_effect=AssertionError("Unexpected network request"),
        )
        self.transport = transport_patch.start()
        self.addCleanup(transport_patch.stop)

    def test_one_request_preserves_state_and_limits_logged_fields(self):
        for status in (200, 403, 302):
            with self.subTest(status=status):
                self.summary.write_text("", encoding="utf-8")
                self.transport.reset_mock(side_effect=True)
                reply = response(
                    "<title>Test &lt;script&gt; &amp; title</title><p>private-body-value</p>",
                    status=status,
                    headers={
                        "Server": "cloudflare",
                        "cf-mitigated": "challenge",
                        "Set-Cookie": "private-cookie-value",
                        "Authorization": "private-auth-value",
                        "Location": "https://untappd.com/must-not-be-followed",
                    },
                )

                def send(request, **_kwargs):
                    self.assertEqual(self.transport.call_count, 1)
                    reply.request = request
                    reply.url = request.url
                    return reply

                self.transport.side_effect = send
                output = io.StringIO()
                with redirect_stdout(output):
                    result = diagnose.main()
                self.assertEqual(result, 0 if status == 200 else 1)
                self.assertEqual(self.transport.call_count, 1)
                sent = self.transport.call_args
                self.assertEqual(sent.args[0].method, "GET")
                self.assertIn("/user/Karaffenaffe?bierbot_refresh=", sent.args[0].url)
                self.assertEqual(sent.kwargs["timeout"], (5, 20))
                summary = self.summary.read_text(encoding="utf-8")
                self.assertIn("&lt;script&gt;", summary)
                self.assertNotIn("<script>", summary)
                for secret in ("private-cookie-value", "private-auth-value", "private-body-value"):
                    self.assertNotIn(secret, output.getvalue())
                    self.assertNotIn(secret, summary)
                self.assertEqual(self.status.read_bytes(), self.original_status)
                self.assertEqual(self.data.read_bytes(), self.original_data)

    def test_timeout_is_not_retried_or_logged_with_sensitive_details(self):
        self.transport.side_effect = requests.ReadTimeout("private-exception-value")
        output = io.StringIO()
        with redirect_stdout(output):
            result = diagnose.main()
        self.assertEqual(result, 1)
        self.assertEqual(self.transport.call_count, 1)
        self.assertIn("ReadTimeout", output.getvalue())
        self.assertNotIn("private-exception-value", output.getvalue())
        self.assertNotIn("private-exception-value", self.summary.read_text(encoding="utf-8"))
        self.assertEqual(self.status.read_bytes(), self.original_status)
        self.assertEqual(self.data.read_bytes(), self.original_data)


if __name__ == "__main__":
    unittest.main()
