import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from bridge_dealer.templates import HandTemplates, TemplateConflict


def test_templates_survive_reopening_with_exact_hand_conditions(tmp_path):
    path = tmp_path / "user_data/hand-templates.json"
    store = HandTemplates(path)
    assert store.list() == [] and not path.exists()
    raw = {
        "name": "  一盖一应叫  ",
        "condition": {
            "predeal": "Q...",
            "exclude": "A...",
            "hcp": [6, 9],
            "shapes": [[4, 3, 3, 3], [3, 4, 3, 3]],
        },
    }
    entry = store.save(raw)
    assert entry["name"] == "一盖一应叫"
    assert entry["condition"]["shapes"] == [[3, 4, 3, 3], [4, 3, 3, 3]]
    assert raw["name"] == "  一盖一应叫  "
    assert HandTemplates(path).list() == [entry]
    assert json.loads(path.read_text())["templates"] == [entry]
    replacement = {"name": "弱应叫", "condition": {"hcp": 8}}
    renamed = HandTemplates(path).save(replacement, entry["id"])
    assert renamed["id"] == entry["id"] and renamed["condition"] == {"hcp": [8, 8]}
    assert store.list() == [renamed]
    store.delete(entry["id"])
    assert HandTemplates(path).list() == []


def test_duplicate_names_and_unknown_ids_preserve_existing_templates(tmp_path):
    store = HandTemplates(tmp_path / "templates.json")
    first = store.save({"name": "不约束", "condition": {}})
    second = store.save({"name": "点力", "condition": {"hcp": [12, 14]}})
    with pytest.raises(TemplateConflict):
        store.save({"name": " 不约束 ", "condition": {"hcp": 1}})
    with pytest.raises(TemplateConflict):
        store.save({"name": "不约束", "condition": {}}, second["id"])
    with pytest.raises(KeyError):
        store.save({"name": "新模板", "condition": {}}, "missing")
    with pytest.raises(KeyError):
        store.delete("missing")
    assert store.list() == [first, second]


@pytest.mark.parametrize(
    "raw",
    [
        {},
        [],
        {"name": "  ", "condition": {}},
        {"name": "多行\n名称", "condition": {}},
        {"name": "a" * 81, "condition": {}},
        {"name": "错误点力", "condition": {"hcp": [18, 12]}},
        {"name": "四家", "condition": {"N": {"hcp": 12}}},
        {"name": "错误牌型", "condition": {"shape": [4, 4, 4, 4]}},
        {"name": "持有与排除冲突", "condition": {"predeal": "A...", "exclude": "A..."}},
    ],
)
def test_invalid_templates_never_create_a_file(tmp_path, raw):
    store = HandTemplates(tmp_path / "templates.json")
    with pytest.raises(ValueError):
        store.save(raw)
    assert not store.path.exists()


def test_write_failure_preserves_previous_file_and_removes_temporary_file(tmp_path, monkeypatch):
    store = HandTemplates(tmp_path / "templates.json")
    entry = store.save({"name": "原始", "condition": {}})
    original = store.path.read_bytes()

    def fail(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("bridge_dealer.templates.os.replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        store.save({"name": "修改", "condition": {"hcp": 12}}, entry["id"])
    assert store.path.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["templates.json", "templates.json.lock"]


@pytest.mark.parametrize("data", ["{broken", '{"version":2,"templates":[]}', '{"version":1}'])
def test_corrupted_storage_is_not_silently_overwritten(tmp_path, data):
    store = HandTemplates(tmp_path / "templates.json")
    store.path.write_text(data)
    with pytest.raises(OSError):
        store.save({"name": "新的", "condition": {}})
    assert store.path.read_text() == data


def test_concurrent_browser_requests_keep_all_templates(tmp_path):
    stores = [HandTemplates(tmp_path / "templates.json") for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        entries = list(
            pool.map(
                lambda i: stores[i % len(stores)].save({"name": f"模板 {i}", "condition": {}}),
                range(18),
            )
        )
    store = stores[0]
    assert len(store.list()) == 18
    assert {e["id"] for e in HandTemplates(store.path).list()} == {e["id"] for e in entries}
