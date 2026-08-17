from __future__ import annotations

from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from ops.welcome.copy_media import copy_one


class Source:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.downloads = 0

    def download_file(self, _bucket: str, _key: str, target: str) -> None:
        self.downloads += 1
        Path(target).write_bytes(self.body)


class Destination:
    def __init__(self, head: dict[str, Any] | None = None) -> None:
        self.head = head
        self.uploads = 0

    def head_object(self, **_kwargs: str) -> dict[str, Any]:
        if self.head is None:
            raise ClientError(
                {
                    "Error": {"Code": "404", "Message": "Not Found"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return self.head

    def upload_file(
        self,
        filename: str,
        _bucket: str,
        _key: str,
        *,
        ExtraArgs: dict[str, dict[str, str]],
    ) -> None:
        self.uploads += 1
        self.head = {
            "ContentLength": Path(filename).stat().st_size,
            "Metadata": ExtraArgs["Metadata"],
        }


def test_copy_uploads_and_verifies_checksum_metadata() -> None:
    source = Source(b"current-source")
    destination = Destination()

    assert copy_one(source, destination, "old", "new", "welcome/file") == "copied"
    assert source.downloads == 1
    assert destination.uploads == 1
    assert len(destination.head["Metadata"]["gramly-sha256"]) == 64  # type: ignore[index]


def test_copy_skips_only_after_comparing_current_source() -> None:
    source = Source(b"current-source")
    first = Destination()
    assert copy_one(source, first, "old", "new", "welcome/file") == "copied"

    resumed_source = Source(b"current-source")
    resumed = Destination(first.head)
    assert copy_one(resumed_source, resumed, "old", "new", "welcome/file") == "skipped"
    assert resumed_source.downloads == 1
    assert resumed.uploads == 0


def test_copy_replaces_stale_destination_metadata() -> None:
    source = Source(b"new-source")
    destination = Destination(
        {"ContentLength": len(b"old-source"), "Metadata": {"gramly-sha256": "stale"}}
    )

    assert copy_one(source, destination, "old", "new", "welcome/file") == "copied"
    assert destination.uploads == 1
