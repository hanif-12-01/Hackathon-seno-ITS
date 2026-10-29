import json
from pathlib import Path

import pytest

from m7c_evidence import assert_inventory, create_run, finalize, inventory, json_output, output, runtime_metadata, sha256, verify


def test_unique_runs(tmp_path):
    first, second = create_run(tmp_path), create_run(tmp_path)
    assert first != second
    with pytest.raises(FileExistsError):
        create_run(tmp_path, first.name)


@pytest.mark.parametrize("name", ["../escape", "nested/file", str(Path.cwd() / "absolute")])
def test_invalid_output(tmp_path, name):
    with pytest.raises(ValueError):
        output(tmp_path, name, "unsafe")


def test_exclusive_output(tmp_path):
    output(tmp_path, "result", "first")
    with pytest.raises(FileExistsError):
        output(tmp_path, "result", "second")
    assert (tmp_path / "result").read_text() == "first"


def test_manifest_and_sidecar(tmp_path):
    run = create_run(tmp_path)
    output(run, "result", "pass")
    digest = finalize(run, {"commands": [], "counts": {"passed": 1}, "previous": None})
    assert verify(run) == digest == sha256(run / "manifest.json")
    assert set(json.loads((run / "manifest.json").read_text())["files"]) == {"result"}
    with pytest.raises(ValueError):
        output(run, "later", "no")
    with pytest.raises(FileExistsError):
        finalize(run, {})


@pytest.mark.parametrize("mutation", ["change", "add", "delete", "manifest", "sidecar"])
def test_finalized_tampering_detected(tmp_path, mutation):
    run = create_run(tmp_path)
    output(run, "result", "pass")
    finalize(run, {})
    if mutation == "change":
        (run / "result").write_text("changed")
    elif mutation == "add":
        (run / "added").write_text("added")
    elif mutation == "delete":
        (run / "result").unlink()
    elif mutation == "manifest":
        (run / "manifest.json").write_text("{}")
    else:
        (run / "manifest.sha256").write_text("bad")
    with pytest.raises(ValueError):
        verify(run)


def test_partial_finalization_blocks_writes(tmp_path):
    output(tmp_path, "manifest.json", "{}")
    with pytest.raises(ValueError):
        output(tmp_path, "result", "no")


def test_inventory_mutations_and_exclusion(tmp_path):
    history = tmp_path / "frontend" / "evidence"
    history.mkdir(parents=True)
    (history / "old.png").write_bytes(b"old")
    current = create_run(history / "m7c")
    before = inventory(tmp_path, [current])
    json_output(current, "inventory.json", before)
    assert assert_inventory(tmp_path, before, [current]) == 1
    (history / "old.png").write_bytes(b"new")
    with pytest.raises(ValueError):
        assert_inventory(tmp_path, before, [current])


def test_no_repository_and_sanitized_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "must-not-appear")
    metadata = runtime_metadata(tmp_path)
    assert metadata["git"]["root"] == {"available": False, "commit": None, "dirty": None}
    assert "must-not-appear" not in json.dumps(metadata)


@pytest.mark.parametrize("run_id", ["../escape", "RUN-fixed", "RUN-20261029T000000000000Z-notuuid"])
def test_invalid_run_id(tmp_path, run_id):
    with pytest.raises(ValueError):
        create_run(tmp_path, run_id)
