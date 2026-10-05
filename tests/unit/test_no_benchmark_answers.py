"""The participant guide disqualifies agents that pattern-match FDB-v3 test items.

Scan what the model sees (instructions and tool descriptions) for the benchmark's
expected argument values. The labels are read here only to look for leaks.
"""
import json
import os
import re
import unittest
from pathlib import Path

from agent.fdb_livekit import INSTRUCTIONS, create_benchmark_tools

V3_ROOT = Path(os.environ.get("INTERRA_FDB_V3_ROOT", ".runtime/Full-Duplex-Bench/v3"))
# Enum spellings and filter keys that come from the backend contract, not from answers.
CONTRACT_VALUES = {
    "driving", "walking", "biking", "transit", "passport", "driver_license", "id_card", "visa",
    "max_price", "bedrooms", "pets_allowed",
    # Parameter vocabulary: "The city or airport as the user said it", "Travel date".
    "airport", "travel",
}


def model_visible_text() -> str:
    tools = create_benchmark_tools(None, lambda **kwargs: lambda fn: fn)
    docs = [getattr(tools, name).__doc__ or "" for name in dir(tools) if not name.startswith("_")]
    return "\n".join([INSTRUCTIONS, *docs])


@unittest.skipUnless((V3_ROOT / "benchmark_data_v2.json").is_file(), "pinned benchmark checkout absent")
class NoBenchmarkAnswersTests(unittest.TestCase):
    def test_prompt_and_tool_docs_hold_no_expected_argument(self):
        data = json.loads((V3_ROOT / "benchmark_data_v2.json").read_text(encoding="utf-8"))
        expected = {
            value
            for scenario in data["scenarios"]
            for call in scenario.get("expected_tool_calls", [])
            for value in call.get("args", {}).values()
            if isinstance(value, str) and len(value) >= 4 and not value.startswith("$")
        } - CONTRACT_VALUES
        text = model_visible_text()
        leaked = sorted(
            value for value in expected
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", text, re.IGNORECASE)
        )
        self.assertEqual(leaked, [])


if __name__ == "__main__":
    unittest.main()
