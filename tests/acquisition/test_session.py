"""Session folders: <date>_<animal-id>/ with session.json (ADR 0007)."""

import json
from datetime import datetime, timedelta, timezone

from wormspy.acquisition.session import create_session

NOW = datetime(2026, 9, 27, 14, 30, 5, tzinfo=timezone(timedelta(hours=-4)))


def test_session_folder_is_named_by_date_and_animal_id(tmp_path):
    path = create_session(tmp_path, {"animal_id": "w12"}, config={}, now=NOW)
    assert path == tmp_path / "2026-09-27_w12"
    assert path.is_dir()


def test_session_json_holds_metadata_config_and_provenance(tmp_path):
    path = create_session(tmp_path, {"animal_id": "w12", "operator": "SW", "notes": "on food"},
                          config={"fps": 10.0, "preset": "calcium-10Hz"}, now=NOW)
    data = json.loads((path / "session.json").read_text())
    assert data["metadata"] == {"animal_id": "w12", "operator": "SW", "notes": "on food"}
    assert data["config"] == {"fps": 10.0, "preset": "calcium-10Hz"}
    assert data["created"] == "2026-09-27T14:30:05-04:00"   # local time with UTC offset
    assert data["software"]["name"] == "wormspy" and data["software"]["version"]


def test_a_second_session_for_the_same_animal_and_day_gets_a_suffix(tmp_path):
    first = create_session(tmp_path, {"animal_id": "w12"}, config={}, now=NOW)
    second = create_session(tmp_path, {"animal_id": "w12"}, config={}, now=NOW)
    assert first.name == "2026-09-27_w12" and second.name == "2026-09-27_w12_2"


def test_unsafe_characters_in_the_animal_id_are_replaced(tmp_path):
    path = create_session(tmp_path, {"animal_id": "gar-3 #4/a:b"}, config={}, now=NOW)
    assert path.name == "2026-09-27_gar-3_4_a_b"


def test_missing_animal_id_still_creates_a_session(tmp_path):
    path = create_session(tmp_path, {}, config={}, now=NOW)
    assert path.name == "2026-09-27_unnamed"


def test_default_timestamp_is_timezone_aware(tmp_path):
    path = create_session(tmp_path, {"animal_id": "w1"}, config={})
    created = json.loads((path / "session.json").read_text())["created"]
    assert datetime.fromisoformat(created).utcoffset() is not None
