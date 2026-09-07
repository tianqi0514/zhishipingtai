from unittest.mock import Mock

from packages.platform.storage import ObjectStorage


def test_delete_skips_external_provenance_uri() -> None:
    storage = ObjectStorage()
    storage._client = Mock()

    storage.delete("fixture://knowledge-analysis/document.md")

    storage._client.remove_object.assert_not_called()


def test_delete_removes_managed_object_key(monkeypatch) -> None:
    storage = ObjectStorage()
    storage._client = Mock()
    monkeypatch.setattr(storage.settings, "use_local_object_store", False)

    storage.delete("tenant/space/document/version/file.md")

    storage._client.remove_object.assert_called_once_with(
        storage.settings.object_store_bucket,
        "tenant/space/document/version/file.md",
    )
