from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from gramly_welcome import catalog_media
from gramly_welcome.main import app

HEADERS = {"X-authentik-username":"test", "X-authentik-groups":"gramly-owners", "X-Gramly-Admin-Request":"1"}


def test_upload_auth_limits_types_and_immutable_key(monkeypatch: pytest.MonkeyPatch) -> None:
    store = Mock(bucket="private")
    uploaded = []
    store.client.upload_fileobj.side_effect = lambda handle, bucket, key, **kw: uploaded.append((handle.read(), bucket, key))
    monkeypatch.setattr(catalog_media, "ObjectStorage", lambda settings: store)
    with TestClient(app) as client:
        assert client.post("/api/admin/v1/catalog-media", content=b"x").status_code == 403
        assert client.post("/api/admin/v1/catalog-media", headers=HEADERS, content=b"").status_code == 422
        arbitrary = client.post("/api/admin/v1/catalog-media", headers={**HEADERS, "X-File-Name": "lesson.docx"}, content=b"document content")
        assert arbitrary.status_code == 200
        assert arbitrary.json()["media_type"] == "document"
        assert arbitrary.json()["mime_type"] == "application/octet-stream"
        assert arbitrary.json()["storage_key"].endswith(".docx")
        assert client.post("/api/admin/v1/catalog-media?media_type=video", headers=HEADERS, content=b"<svg/>").status_code == 422
        assert client.post("/api/admin/v1/catalog-media", headers={**HEADERS,"Content-Length":str(21*1024*1024)}, content=b"x").status_code == 413
        data = b"\x89PNG\r\n\x1a\nfixture"
        assert client.post("/api/admin/v1/catalog-media?media_type=video", headers=HEADERS, content=data).status_code == 422
        first = client.post("/api/admin/v1/catalog-media", headers=HEADERS, content=data)
        repeat = client.post("/api/admin/v1/catalog-media", headers=HEADERS, content=data)
        assert first.status_code == 200
        assert first.json() == repeat.json()
        assert first.json()["media_type"] == "photo"
        webp = client.post("/api/admin/v1/catalog-media", headers={**HEADERS, "X-File-Name": "animation.webp"}, content=b"RIFF\x00\x00\x00\x00WEBP")
        assert webp.json()["media_type"] == "document"
        assert first.json()["storage_key"].startswith("catalog/uploads/")
        assert any(blob == data and bucket == "private" for blob, bucket, _ in uploaded)
        store.client.upload_fileobj.side_effect = RuntimeError("secret endpoint")
        failure = client.post("/api/admin/v1/catalog-media", headers=HEADERS, content=data)
        assert failure.status_code == 503 and "secret" not in failure.text
        assert client.get("/api/admin/v1/catalog-media/anything").status_code == 403
        assert client.get("/api/admin/v1/catalog-media/anything", headers=HEADERS).status_code == 404
