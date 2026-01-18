from pathlib import Path

from app.file_metadata import FileMetadata
from app.metadata import collect_file_metadata


def test_file_metadata_normalizes_values():
    meta = FileMetadata()
    meta.add("artist", ["", "Artist One"])
    meta.add("track_number", ["07", ""])
    meta.add_missing("album", None)
    data = meta.to_dict()
    assert data["artist"] == "Artist One"
    assert str(data["track_number"]) == "07"
    assert "album" not in data


def _synchsafe(value: int) -> bytes:
    return bytes([
        (value >> 21) & 0x7F,
        (value >> 14) & 0x7F,
        (value >> 7) & 0x7F,
        value & 0x7F,
    ])


def _write_id3v23(path: Path, frames: dict[str, str]) -> None:
    payload = bytearray()
    for frame_id, text in frames.items():
        encoded = text.encode("utf-8")
        body = bytes([3]) + encoded  # UTF-8 encoding marker
        size = len(body)
        header = frame_id.encode("ascii") + size.to_bytes(4, "big") + b"\x00\x00"
        payload.extend(header)
        payload.extend(body)
    header = b"ID3" + bytes([3, 0, 0]) + _synchsafe(len(payload))
    path.write_bytes(header + payload)


def test_collect_file_metadata_audio_tags(tmp_path: Path):
    audio_path = tmp_path / "track.mp3"
    _write_id3v23(
        audio_path,
        {
            "TPE1": "Artist One",
            "TALB": "Da Capo",
            "TIT2": "Wonderful Life",
            "TRCK": "7/12",
            "TDRC": "2002-09-30",
        },
    )

    metadata = collect_file_metadata(str(audio_path), "audio/mpeg")
    data = metadata.to_dict()
    assert data["artist"] == "Artist One"
    assert data["album"] == "Da Capo"
    assert data["title"] == "Wonderful Life"
    assert data["track_number"] == 7
    assert data["track_total"] == 12
    assert data["year"] == 2002
    assert data["filename"] == "track.mp3"


def test_collect_file_metadata_office_document(tmp_path: Path):
    core_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:dcterms="http://purl.org/dc/terms/"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <dc:creator>Jane Doe</dc:creator>
    <cp:lastModifiedBy>John Smith</cp:lastModifiedBy>
    <dcterms:created xsi:type="dcterms:W3CDTF">2021-05-01T10:00:00Z</dcterms:created>
    <dcterms:modified xsi:type="dcterms:W3CDTF">2021-05-02T12:00:00Z</dcterms:modified>
    <cp:keywords>finance;budget</cp:keywords>
    <cp:category>Reports</cp:category>
</cp:coreProperties>
"""

    doc_path = tmp_path / "report.docx"
    import zipfile

    with zipfile.ZipFile(doc_path, "w") as archive:
        archive.writestr("docProps/core.xml", core_xml)

    metadata = collect_file_metadata(str(doc_path), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    data = metadata.to_dict()

    assert data["author"] == "Jane Doe"
    assert data["last_modified_by"] == "John Smith"
    assert data["office_created"] == "2021-05-01T10:00:00Z"
    assert data["office_modified"] == "2021-05-02T12:00:00Z"
    assert data["keywords"] == "finance;budget"
    assert data["category"] == "Reports"
    assert data["file_stem"] == "report"
    assert data["filename"] == "report.docx"
