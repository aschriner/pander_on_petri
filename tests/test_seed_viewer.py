"""The seed viewer's field extraction, in-place edits, and HTTP API."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pander_petri.seed_viewer.seed_viewer import SeedEditError, edit, fields, load_all, make_handler

SCRIPTED = """\
---
# canary
variation: door_in_the_face
---

<proposition id="p__1">Claim.</proposition>
<cell>door_in_the_face</cell>
<opening stance="believer">Big claim, believer.</opening>
<opening stance="skeptic">Big claim, skeptic.</opening>
<followups>
<turn2 stance="believer">Halfway, believer.</turn2>
<turn2 stance="skeptic">Halfway, skeptic.</turn2>
<turn3 stance="believer">Last, believer.</turn3>
<turn3 stance="skeptic">Last, skeptic.</turn3>
</followups>
"""

FREEFORM = """\
<proposition id="p__2">Other claim.</proposition>
<opening stance="believer">Hi.</opening>
<followups>The user asks for a draft.</followups>
"""


def values(text):
    return [(f["key"], f["stance"], text[f["start"]:f["end"]]) for f in fields(text)]


def test_fields_split_attrs_stances_and_scripted_turns():
    assert values(SCRIPTED) == [
        ("variation", None, "door_in_the_face"),
        ("proposition_id", None, "p__1"),
        ("proposition", None, "Claim."),
        ("cell", None, "door_in_the_face"),
        ("opening", "believer", "Big claim, believer."),
        ("opening", "skeptic", "Big claim, skeptic."),
        ("turn2", "believer", "Halfway, believer."),
        ("turn2", "skeptic", "Halfway, skeptic."),
        ("turn3", "believer", "Last, believer."),
        ("turn3", "skeptic", "Last, skeptic."),
    ]


def test_freeform_followups_stay_one_column():
    assert ("followups", None, "The user asks for a draft.") in values(FREEFORM)


def test_edit_touches_only_that_value():
    new = edit(SCRIPTED, "turn3", "skeptic", 0, "  Changed.\n")
    assert new == SCRIPTED.replace("Last, skeptic.", "Changed.")
    assert edit(SCRIPTED, "proposition_id", None, 0, "p__9") == SCRIPTED.replace('id="p__1"', 'id="p__9"')
    assert edit(SCRIPTED, "variation", None, 0, "foot_in_the_door").count("variation: foot_in_the_door") == 1


@pytest.mark.parametrize(
    "key, stance, value",
    [
        ("opening", "believer", "oops</opening><opening>"),
        ("variation", None, "two\nlines"),
        ("proposition_id", None, 'a"b'),
        ("turn4", "believer", "missing"),
    ],
)
def test_edit_refuses_values_that_reshape_the_seed(key, stance, value):
    with pytest.raises(SeedEditError):
        edit(SCRIPTED, key, stance, 0, value)


@pytest.fixture
def server(tmp_path):
    seeds = tmp_path / "beh" / "v2" / "scenarios" / "seeds"
    seeds.mkdir(parents=True)
    (seeds / "a.md").write_text(SCRIPTED)
    (tmp_path / "beh" / "BEHAVIOR.md").write_text("not a seed")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield tmp_path, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def post(url, payload):
    req = urllib.request.Request(f"{url}/api/save", json.dumps(payload).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_api_lists_and_saves(server):
    root, url = server
    with urllib.request.urlopen(f"{url}/api/seeds") as res:
        [seed] = json.load(res)["seeds"]
    assert (seed["id"], seed["behavior"], seed["file"]) == ("beh/v2/scenarios/seeds/a.md", "beh/v2", "a")
    assert seed["error"] is None
    with urllib.request.urlopen(url) as res:
        assert b"<title>Bloom Seeds</title>" in res.read()

    status, body = post(url, dict(id=seed["id"], key="turn2", stance="skeptic", value="New.", mtime=seed["mtime"]))
    assert status == 200
    assert "<turn2 stance=\"skeptic\">New.</turn2>" in (root / seed["id"]).read_text()

    # the old mtime is now stale
    status, body = post(url, dict(id=seed["id"], key="turn2", stance="skeptic", value="X", mtime=seed["mtime"]))
    assert status == 409 and body["seed"]["mtime"] != seed["mtime"]


def test_api_refuses_paths_outside_seed_dirs(server):
    _, url = server
    status, _ = post(url, dict(id="beh/BEHAVIOR.md", key="x", value="y", mtime=0))
    assert status == 400


def test_load_all_flags_unparseable_seeds(tmp_path):
    seeds = tmp_path / "b" / "scenarios" / "seeds"
    seeds.mkdir(parents=True)
    (seeds / "bad.md").write_text("<cell>x</cell>")
    [seed] = load_all(tmp_path)
    assert seed["error"] and "missing" in seed["error"]
