from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.device_registry import DeviceRegistry
from backend.app.storage_layout import new_layout, initialize_empty_workspace, legacy_identity


def test_device_identity_persisted_and_separate_devices_differ(tmp_path):
    first = DeviceRegistry(tmp_path / "device1")
    device = first.device_id()
    assert DeviceRegistry(tmp_path / "device1").device_id() == device
    assert DeviceRegistry(tmp_path / "device2").device_id() != device


def test_concurrent_initialization_does_not_generate_two_identities(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: DeviceRegistry(tmp_path / "device").device_id(), range(2)))
    assert ids[0] == ids[1]


def test_move_preserves_old_path_alias(tmp_path):
    layout = new_layout(tmp_path / "data")
    initialize_empty_workspace(layout)
    registry = DeviceRegistry(tmp_path / "device")
    registry.bind(layout)
    old = layout.notes
    moved = tmp_path / "moved"
    old.rename(moved)
    updated = replace(layout, notes=moved)
    registry.bind(updated, legacy_paths=[old])
    assert registry.lookup(legacy_identity(old))["roots"]["notes"] == str(moved)
    assert registry.lookup(layout.workspace_id)["workspace_id"] == layout.workspace_id


def test_binding_wrong_vault_and_registry_in_notes_are_refused(tmp_path):
    first, second = new_layout(tmp_path / "first"), new_layout(tmp_path / "second")
    initialize_empty_workspace(first)
    initialize_empty_workspace(second)
    registry = DeviceRegistry(tmp_path / "device")
    with pytest.raises(ValueError, match="其他工作区"):
        registry.bind(second, legacy_paths=[first.notes])
    with pytest.raises(ValueError, match="之外"):
        DeviceRegistry(first.notes / "device").bind(first)
    assert not (first.notes / "device").exists()


def test_deleted_marker_refuses_lookup_and_does_not_recreate(tmp_path):
    layout = new_layout(tmp_path / "data")
    initialize_empty_workspace(layout)
    registry = DeviceRegistry(tmp_path / "device")
    registry.bind(layout)
    (layout.notes / "_Torch/工作区.md").unlink()
    with pytest.raises(ValueError, match="身份变化"):
        registry.lookup(layout.workspace_id)
    assert not (layout.notes / "_Torch/工作区.md").exists()


def test_lookup_unknown_registry_has_no_side_effect(tmp_path):
    root = tmp_path / "missing"
    assert DeviceRegistry(root).lookup("unknown") is None
    assert not root.exists()
