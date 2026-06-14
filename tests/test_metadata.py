import json

import piexif

from lillio_archive.metadata import embed_jpeg_metadata, write_sidecar

MINIMAL_JPEG = bytes.fromhex(
    "FFD8FFE000104A46494600010100000100010000FFDB004300"
    + "00" * 64
    + "FFC0000B080001000101011100FFC400140001000000000000"
    + "00000000000000000000FFC400141001000000000000000000"
    + "00000000000000FFDA0008010100003F00FFD9"
)


def test_write_sidecar_preserves_post_metadata(tmp_path) -> None:
    media = tmp_path / "photo.jpg"
    media.write_bytes(b"photo")
    sidecar = write_sidecar(
        media,
        {"title": "Title", "description": "Description", "activity_id": "123"},
    )
    assert json.loads(sidecar.read_text())["description"] == "Description"
    assert sidecar.stat().st_mode & 0o777 == 0o600


def test_embed_jpeg_metadata_writes_title_description_and_date(tmp_path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(MINIMAL_JPEG)

    assert embed_jpeg_metadata(
        path,
        title="Art Project",
        description="Ring Toss and Limbo",
        activity_date="2026-06-09",
    )

    exif = piexif.load(str(path))
    title = bytes(exif["0th"][piexif.ImageIFD.XPTitle])
    assert title.decode("utf-16le").rstrip("\0") == "Art Project"
    description = exif["0th"][piexif.ImageIFD.ImageDescription]
    assert description.decode("utf-8") == "Ring Toss and Limbo"
    assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:06:09 00:00:00"
