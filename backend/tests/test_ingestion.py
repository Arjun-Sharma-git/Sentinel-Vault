"""The library lifecycle: new, changed, moved and deleted files, and new folders."""

from __future__ import annotations

from conftest import TEST_LIBRARY


def _scan_and_index(api) -> dict:
    result = api.post("alice", "/api/admin/scan").json()
    api.post("alice", "/api/admin/index-now")
    return result


def _documents(api) -> dict[str, dict]:
    return {d["rel_path"]: d for d in api.get("alice", "/api/admin/documents").json()["documents"]}


def test_new_file_is_indexed_and_searchable(api):
    path = TEST_LIBRARY / "general" / "parking.md"
    path.write_text("# Parking\n\n## Garage\nThe staff garage code is PARKING-4471 and opens at 06:00.\n")
    try:
        assert _scan_and_index(api)["queued"] == 1
        assert _documents(api)["general/parking.md"]["status"] == "indexed"
        assert "PARKING-4471" in api.ask("dave", "staff garage code")["raw"]
    finally:
        path.unlink()
        _scan_and_index(api)
    assert "general/parking.md" not in _documents(api)
    assert "PARKING-4471" not in api.ask("dave", "staff garage code")["raw"]


def test_changed_file_is_reindexed(api):
    path = TEST_LIBRARY / "general" / "it-help.md"
    original = path.read_text()
    try:
        path.write_text(original + "\n## Printers\nThe printer queue is called PRINT-ZEBRA-9.\n")
        assert _scan_and_index(api)["queued"] == 1
        assert "PRINT-ZEBRA-9" in api.ask("dave", "printer queue name")["raw"]
    finally:
        path.write_text(original)
        _scan_and_index(api)


def test_moving_a_file_changes_who_can_see_it_without_reembedding(api):
    source = TEST_LIBRARY / "management" / "acquisition-plan.md"
    target = TEST_LIBRARY / "general" / "acquisition-plan.md"
    before = _documents(api)["management/acquisition-plan.md"]
    source.rename(target)
    try:
        result = _scan_and_index(api)
        assert result["moved"] == 1 and result["queued"] == 0  # no re-embedding
        moved = _documents(api)["general/acquisition-plan.md"]
        assert moved["id"] == before["id"]
        assert "VIOLET-CRANE-42" in api.ask("dave", "acquisition plan board code word")["raw"]
    finally:
        target.rename(source)
        _scan_and_index(api)
    assert "VIOLET-CRANE-42" not in api.ask("dave", "acquisition plan board code word")["raw"]


def test_new_subfolder_inherits_its_parents_grants(api):
    folder = TEST_LIBRARY / "engineering" / "design-docs"
    folder.mkdir()
    (folder / "search.md").write_text("# Search design\n\nSearch uses the ORCA-HYBRID-3 ranking.\n")
    try:
        _scan_and_index(api)
        assert "ORCA-HYBRID-3" in api.ask("carol", "search ranking design")["raw"]
        assert "ORCA-HYBRID-3" not in api.ask("dave", "search ranking design")["raw"]
    finally:
        (folder / "search.md").unlink()
        folder.rmdir()
        _scan_and_index(api)


def test_admin_created_folder_exists_on_disk_and_accepts_uploads(api):
    created = api.post("alice", "/api/admin/folders", json={"path": "engineering/specs"})
    assert created.status_code == 201
    assert (TEST_LIBRARY / "engineering" / "specs").is_dir()
    upload = api.post(
        "carol",
        f"/api/folders/{created.json()['id']}/upload",
        files={"file": ("api.md", b"# API spec\n\nVersion SPEC-KITE-5.", "text/markdown")},
    )
    assert upload.status_code == 201
    api.post("alice", "/api/admin/index-now")
    assert "SPEC-KITE-5" in api.ask("carol", "API spec version")["raw"]
    api.delete("carol", f"/api/documents/{upload.json()['id']}")
