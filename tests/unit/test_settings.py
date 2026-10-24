"""Dashboard settings: what may be overridden, how it is stored, and who may change it."""

import base64
import json
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.settings import (
    LoginThrottle,
    admin_password,
    apply_changes,
    basic_auth_password,
    check_name,
    describe,
    issue_session,
    load_overrides,
    masked,
    save_overrides,
    settings_path,
    valid_session,
)


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "settings" / "overrides.json"

    def test_names_that_control_access_or_start_up_are_refused(self):
        self.assertEqual(check_name("INTERRA_FDB_LLM_MODEL"), "INTERRA_FDB_LLM_MODEL")
        self.assertEqual(check_name("OPENAI_API_KEY"), "OPENAI_API_KEY")
        for name in ("INTERRA_ADMIN_PASSWORD", "INTERRA_SITE_PASSWORD", "INTERRA_SETTINGS_FILE",
                     "INTERRA_SUPERVISOR_PID", "PATH", "PORT", "LD_PRELOAD", "PYTHONPATH",
                     "PYTHONSTARTUP", "RAILWAY_RUN_UID", "lower_case", "1ABC", "A-B", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                check_name(name)

    def test_saved_overrides_round_trip_privately_and_atomically(self):
        save_overrides(self.path, {"INTERRA_FDB_LLM_MODEL": "openai/gpt-4.1", "LIVEKIT_URL": "wss://x"})
        self.assertEqual(load_overrides(self.path), {"INTERRA_FDB_LLM_MODEL": "openai/gpt-4.1", "LIVEKIT_URL": "wss://x"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["overrides.json"])

    def test_missing_file_is_no_overrides_and_a_bad_file_is_an_error(self):
        self.assertEqual(load_overrides(self.path), {})
        self.path.parent.mkdir(parents=True)
        for text in ("not json", "[1]", json.dumps({"PATH": "/tmp"}), json.dumps({"OK_NAME": 3})):
            self.path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(ValueError):
                load_overrides(self.path)

    def test_changes_set_and_unset_and_refuse_bad_values(self):
        current = {"A_ONE": "1", "B_TWO": "2"}
        updated = apply_changes(current, {"set": {"C_THREE": "3", "A_ONE": "one"}, "unset": ["B_TWO", "NOT_SAVED"]})
        self.assertEqual(updated, {"A_ONE": "one", "C_THREE": "3"})
        self.assertEqual(current, {"A_ONE": "1", "B_TWO": "2"})
        for bad in ({"set": {"A_ONE": "two\nlines"}}, {"set": {"A_ONE": 5}}, {"set": {"A_ONE": "x" * 5000}},
                    {"set": {"PYTHONPATH": "/x"}}, {"unset": ["INTERRA_ADMIN_PASSWORD"]}, {"set": []}, []):
            with self.subTest(changes=bad), self.assertRaises(ValueError):
                apply_changes(current, bad)

    def test_relative_settings_file_is_under_the_project(self):
        self.assertTrue(settings_path({}).is_absolute())
        self.assertEqual(settings_path({"INTERRA_SETTINGS_FILE": str(self.path)}), self.path)


class DescribeTests(unittest.TestCase):
    def test_rows_say_where_each_value_comes_from_and_mask_secrets(self):
        environ = {
            "LIVEKIT_URL": "wss://deployed.livekit.cloud",
            "LIVEKIT_API_SECRET": "deployed-secret-value-1234",
            "INTERRA_FDB_LLM_MODEL": "openai/gpt-4.1-mini",
        }
        overrides = {"INTERRA_FDB_LLM_MODEL": "openai/gpt-4.1", "EXTRA_TOKEN": "short"}
        rows = {row["name"]: row for row in describe(environ, overrides)}
        self.assertEqual((rows["LIVEKIT_URL"]["source"], rows["LIVEKIT_URL"]["value"]), ("deployment", "wss://deployed.livekit.cloud"))
        self.assertEqual(rows["LIVEKIT_API_SECRET"]["value"], "••••1234")
        self.assertEqual(rows["LIVEKIT_API_KEY"]["source"], "unset")
        model = rows["INTERRA_FDB_LLM_MODEL"]
        self.assertEqual((model["source"], model["value"], model["pending"]), ("dashboard", "openai/gpt-4.1", True))
        self.assertEqual((rows["EXTRA_TOKEN"]["value"], rows["EXTRA_TOKEN"]["secret"]), ("••••", True))
        text = json.dumps(list(rows.values()))
        self.assertNotIn("deployed-secret-value", text)
        self.assertNotIn("short", text)

    def test_mask_keeps_at_most_four_characters_of_long_secrets(self):
        self.assertEqual(masked(""), "")
        self.assertEqual(masked("abc"), "••••")
        self.assertEqual(masked("abcdefghijkl"), "••••ijkl")


class AccessTests(unittest.TestCase):
    def test_settings_stay_off_without_a_long_enough_password(self):
        self.assertIsNone(admin_password({}))
        self.assertIsNone(admin_password({"INTERRA_ADMIN_PASSWORD": "short"}))
        self.assertEqual(admin_password({"INTERRA_ADMIN_PASSWORD": "long enough"}), "long enough")

    def test_sessions_expire_and_die_with_the_password(self):
        cookie = issue_session("first password", now=1000, ttl=60)
        self.assertTrue(valid_session(cookie, "first password", now=1059))
        self.assertFalse(valid_session(cookie, "first password", now=1061))
        self.assertFalse(valid_session(cookie, "second password", now=1001))
        expires, signature = cookie.split(".")
        self.assertFalse(valid_session(f"{int(expires) + 999}.{signature}", "first password", now=1001))
        for bad in (None, "", "garbage", "abc.def"):
            self.assertFalse(valid_session(bad, "first password", now=1001))

    def test_basic_auth_reads_only_the_password(self):
        header = "Basic " + base64.b64encode(b"anyone:pa:ss").decode("ascii")
        self.assertEqual(basic_auth_password(header), "pa:ss")
        for bad in (None, "", "Bearer x", "Basic !!!", "Basic " + base64.b64encode(b"nocolon").decode("ascii")):
            self.assertIsNone(basic_auth_password(bad))

    def test_throttle_refuses_after_the_limit_until_the_window_passes(self):
        throttle = LoginThrottle(limit=3, window=10)
        for moment in (0, 1, 2):
            self.assertTrue(throttle.allowed(moment))
            throttle.failed(moment)
        self.assertFalse(throttle.allowed(5))
        self.assertTrue(throttle.allowed(10.5))


if __name__ == "__main__":
    unittest.main()
