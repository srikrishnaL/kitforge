"""The README's main example, run as written (with a fake API)."""

import json
import re
from pathlib import Path

from toolcap import Cap

README = (Path(__file__).parent.parent / "README.md").read_text()


class FakeApi:
    def flow_runs(self, state, limit):
        return [{"id": f"run-{i}", "name": f"nightly-etl-{i}", "state": state, "tags": ["etl"] * 60}
                for i in range(limit)]


def test_example_pages_as_described():
    api = FakeApi()
    cap = Cap(max_tokens=20_000)

    @cap.paginate
    def get_flow_runs(state: str = "COMPLETED", limit: int = 200) -> list:
        """List flow runs."""
        return api.flow_runs(state=state, limit=limit)

    doc = json.loads(get_flow_runs())
    page = doc["page"]
    assert set(page) == {"items", "total", "next_cursor", "note"}
    assert page["total"] == 200 and re.fullmatch(r"1-\d+ of 200", page["items"])
    assert f'cursor="{page["next_cursor"]}"' in page["note"]
    nxt = json.loads(get_flow_runs(state="COMPLETED", limit=200, cursor=page["next_cursor"]))
    assert nxt["items"][0]["id"] == f"run-{len(doc['items'])}"


def test_readme_names_the_real_settings():
    for name in ["max_tokens=None", "count=None", "indent=None", 'cursor_param="cursor"', "TOOLCAP_MAX_TOKENS"]:
        assert name in README
