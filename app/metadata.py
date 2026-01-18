from __future__ import annotations

import json
import os
import subprocess
import zipfile
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .file_metadata import FileMetadata

AUDIO_SUFFIXES = {
    ".mp3",
    ".flac",
    ".m4a",
    ".m4b",
    ".aac",
    ".wav",
    ".oga",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
    ".aifc",
    ".ac3",
}

VIDEO_SUFFIXES = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".wmv",
    ".m4v",
    ".flv",
    ".webm",
    ".mpeg",
    ".mpg",
}


def _first_non_empty(value: Any) -> Optional[Any]:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        for item in value:
            normalized = _first_non_empty(item)
            if normalized is not None:
                return normalized
        return None
    if hasattr(value, "text"):
        try:
            return _first_non_empty(value.text)
        except Exception:
            pass
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", "ignore")
        except Exception:
            value = value.decode("latin-1", "ignore")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return text
    if isinstance(value, (int, float)):
        return value
    return value


def _parse_index_pair(value: Any) -> tuple[Optional[int], Optional[int]]:
    text = _first_non_empty(value)
    if text is None:
        return None, None
    if isinstance(text, (int, float)):
        return int(text), None
    tokens = str(text).split("/")
    primary: Optional[int] = None
    total: Optional[int] = None
    try:
        primary = int(tokens[0])
    except (ValueError, TypeError, IndexError):
        pass
    if len(tokens) > 1:
        try:
            total = int(tokens[1])
        except (ValueError, TypeError):
            pass
    return primary, total


def _parse_year_value(value: Any) -> Optional[int]:
    text = _first_non_empty(value)
    if text is None:
        return None
    if isinstance(text, (int, float)):
        year = int(text)
        if 1800 <= year <= 9999:
            return year
        return None
    match = re.search(r"(18|19|20|21)\d{2}", str(text))
    if match:
        try:
            return int(match.group(0))
        except ValueError:
            return None
    return None


def collect_file_metadata(path: str, mime: str) -> FileMetadata:
    meta = FileMetadata()
    p = Path(path)

    try:
        stat = p.stat()
        meta.add("size", stat.st_size)
        modified_dt = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        meta.add("modified", modified_dt.isoformat())
        meta.add_missing("year", modified_dt.year)
        try:
            created_dt = datetime.fromtimestamp(stat.st_ctime, timezone.utc)
            meta.add("created", created_dt.isoformat())
        except (AttributeError, OSError):
            pass
    except FileNotFoundError:
        pass

    if p.suffix.lower() in {".docx", ".pptx", ".xlsx"}:
        meta.merge(_extract_office_metadata(p))

    if mime.startswith("image/") or p.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic", ".tiff"}:
        exif = _extract_exif(path)
        if exif:
            meta.merge(exif)
            if "DateTimeOriginal" in exif:
                try:
                    dt = _parse_exif_datetime(exif["DateTimeOriginal"])
                    meta.add_missing("year", dt.year)
                except Exception:  # pragma: no cover - fallback handled above
                    pass
            if "CreateDate" in exif and not meta.get("year"):
                try:
                    meta.add_missing("year", _parse_exif_datetime(exif["CreateDate"]).year)
                except Exception:
                    pass
    
    if mime.startswith("video/") or p.suffix.lower() in VIDEO_SUFFIXES:
        exif = _extract_exif(path)
        if exif:
            meta.merge(exif)
            if "CreateDate" in exif and not meta.get("year"):
                try:
                    meta.add_missing("year", _parse_exif_datetime(exif["CreateDate"]).year)
                except Exception:
                    pass

    if mime.startswith("audio/") or p.suffix.lower() in AUDIO_SUFFIXES:
        audio_meta = _extract_audio_metadata(p)
        if audio_meta:
            meta.merge(audio_meta)
    
    if mime.startswith("video/") or p.suffix.lower() in VIDEO_SUFFIXES:
        video_meta = _extract_audio_metadata(p)
        if video_meta:
            meta.merge(video_meta)

    meta.add_missing("title", p.stem)
    meta.add_missing("file_stem", p.stem)
    meta.add_missing("extension", p.suffix.lstrip("."))
    meta.add_missing("filename", p.name)
    if p.parent:
        meta.add_missing("parent_folder", p.parent.name)
        if p.parent.parent:
            meta.add_missing("grandparent_folder", p.parent.parent.name)

    return meta


def _parse_exif_datetime(value: str) -> datetime:
    value = value.replace(":", "-", 2)
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _extract_exif(path: str) -> Dict[str, Any]:
    cmd = [
        "exiftool",
        "-j",
        "-DateTimeOriginal",
        "-DateTimeDigitized",
        "-CreateDate",
        "-ModifyDate",
        "-Make",
        "-Model",
        "-LensModel",
        "-Artist",
        "-ImageDescription",
        "-GPSLatitude",
        "-GPSLongitude",
        "-GPSAltitude",
        path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=2)
        if result.returncode != 0 or not result.stdout.strip():
            return {}
        data = json.loads(result.stdout)
        if isinstance(data, list) and data:
            entry = data[0]
            out: Dict[str, Any] = {}
            for key in (
                "DateTimeOriginal",
                "DateTimeDigitized",
                "CreateDate",
                "ModifyDate",
                "Make",
                "Model",
                "LensModel",
                "Artist",
                "ImageDescription",
                "GPSLatitude",
                "GPSLongitude",
                "GPSAltitude",
            ):
                val = entry.get(key)
                if isinstance(val, str) and val.strip():
                    out[key] = val.strip()
            return out
    except Exception:
        return {}
    return {}


def _extract_office_metadata(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                with zf.open("docProps/core.xml") as core:
                    from xml.etree import ElementTree as ET

                    tree = ET.parse(core)
                    root = tree.getroot()
                    ns = {
                        "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
                        "dc": "http://purl.org/dc/elements/1.1/",
                        "dcterms": "http://purl.org/dc/terms/",
                    }
                    def text_of(tag: str) -> str | None:
                        node = root.find(tag, ns)
                        if node is not None and node.text:
                            return node.text.strip()
                        return None

                    out["author"] = text_of("dc:creator")
                    out["last_modified_by"] = text_of("cp:lastModifiedBy")
                    for key in ("created", "modified"):
                        value = text_of(f"dcterms:{key}")
                        if value:
                            out[f"office_{key}"] = value
                    out["keywords"] = text_of("cp:keywords")
                    out["category"] = text_of("cp:category")
            except KeyError:
                pass
    except Exception:
        return {}
    return {k: v for k, v in out.items() if v}


def _extract_audio_metadata(path: Path) -> Dict[str, Any]:
    try:
        from mutagen import File as MutagenFile  # type: ignore
    except ImportError:  # pragma: no cover - optional dependency
        return _extract_id3_metadata(path)

    audio = None
    try:
        audio = MutagenFile(path, easy=True)
    except Exception:
        audio = None
    if audio is None:
        try:
            audio = MutagenFile(path)
        except Exception:
            audio = None
    if audio is None:
        return _extract_id3_metadata(path)

    tags = getattr(audio, "tags", None)
    out: Dict[str, Any] = {}
    tag_lookup: Dict[str, Any] = {}

    if tags:
        try:
            items = tags.items()
        except Exception:
            items = []
        for key, value in items:
            key_str = str(key)
            tag_lookup.setdefault(key_str, value)
            tag_lookup.setdefault(key_str.lower(), value)
        if hasattr(tags, "keys"):
            for key in tags.keys():
                key_str = str(key)
                if key_str in tag_lookup:
                    continue
                try:
                    value = tags[key]
                except Exception:
                    continue
                tag_lookup[key_str] = value
                tag_lookup[key_str.lower()] = value

    def lookup(keys: Iterable[str]) -> Optional[Any]:
        for key in keys:
            candidate = tag_lookup.get(key)
            if candidate is None:
                candidate = tag_lookup.get(str(key).lower())
            value = _first_non_empty(candidate)
            if value is not None:
                return value
        return None

    artist = lookup(["artist", "albumartist", "performer", "tpe1", "tpe2"])
    if artist:
        out["artist"] = artist

    album_artist = lookup(["albumartist", "tpe2"])
    if album_artist:
        out["album_artist"] = album_artist
        out.setdefault("artist", album_artist)

    album = lookup(["album", "talb"])
    if album:
        out["album"] = album

    title = lookup(["title", "tit2"])
    if title:
        out["title"] = title
        out.setdefault("track", title)

    genre = lookup(["genre", "tcon"])
    if genre:
        out["genre"] = genre

    composer = lookup(["composer", "tcom"])
    if composer:
        out["composer"] = composer

    track_value = lookup(["tracknumber", "trck"])
    if track_value:
        primary, total = _parse_index_pair(track_value)
        if primary is not None:
            out["track_number"] = primary
        if total is not None:
            out["track_total"] = total

    disc_value = lookup(["discnumber", "tpos"])
    if disc_value:
        primary, total = _parse_index_pair(disc_value)
        if primary is not None:
            out["disc_number"] = primary
        if total is not None:
            out["disc_total"] = total

    date_value = lookup(["originaldate", "date", "year", "tdrc", "tyer"])
    if date_value:
        out["date"] = date_value
        parsed_year = _parse_year_value(date_value)
        if parsed_year:
            out["year"] = parsed_year

    isrc = lookup(["isrc", "tsrc"])
    if isrc:
        out["isrc"] = isrc

    comment = lookup(["comment", "comments", "©cmt"])
    if comment:
        out["comment"] = comment

    lyrics = lookup(["lyrics", "unsyncedlyrics"])
    if lyrics:
        out["lyrics"] = lyrics

    info = getattr(audio, "info", None)
    if info is not None:
        length = getattr(info, "length", None)
        if length:
            try:
                out["duration_seconds"] = round(float(length), 3)
            except Exception:
                pass
        bitrate = getattr(info, "bitrate", None)
        if bitrate:
            try:
                out["bitrate"] = int(bitrate)
            except Exception:
                pass
        sample_rate = getattr(info, "sample_rate", None)
        if sample_rate:
            out["sample_rate"] = sample_rate
        channels = getattr(info, "channels", None)
        if channels:
            out["channels"] = channels

    normalized = {k: v for k, v in out.items() if v not in (None, "", [])}
    if normalized:
        return normalized
    return _extract_id3_metadata(path)


def _synchsafe_to_int(raw: bytes) -> int:
    value = 0
    for byte in raw:
        value = (value << 7) | (byte & 0x7F)
    return value


def _decode_id3_text(payload: bytes) -> Optional[str]:
    if not payload:
        return None
    encoding = payload[0]
    data = payload[1:]
    if encoding == 0:
        text = data.decode("latin-1", "ignore")
    elif encoding in (1, 2):
        text = data.decode("utf-16", "ignore")
    elif encoding == 3:
        text = data.decode("utf-8", "ignore")
    else:
        text = data.decode("utf-8", "ignore")
    return text.replace("\x00", "").strip()


def _extract_id3_metadata(path: Path) -> Dict[str, Any]:
    try:
        data = path.read_bytes()
    except Exception:
        return {}
    if len(data) < 10 or data[:3] != b"ID3":
        return {}

    header_size = _synchsafe_to_int(data[6:10])
    end = min(len(data), 10 + header_size)
    pos = 10
    out: Dict[str, Any] = {}

    while pos + 10 <= end:
        frame_id_bytes = data[pos : pos + 4]
        frame_id = frame_id_bytes.decode("latin-1", "ignore")
        if not frame_id.strip("\x00"):
            break
        frame_size = int.from_bytes(data[pos + 4 : pos + 8], "big")
        if frame_size <= 0:
            break
        frame_end = pos + 10 + frame_size
        if frame_end > len(data):
            break
        frame_payload = data[pos + 10 : frame_end]
        pos = frame_end

        if frame_id.startswith("T") and frame_id not in {"TXXX"}:
            text = _decode_id3_text(frame_payload)
            if not text:
                continue
            if frame_id == "TPE1":
                out.setdefault("artist", text)
            elif frame_id == "TPE2":
                out.setdefault("album_artist", text)
                out.setdefault("artist", text)
            elif frame_id == "TALB":
                out.setdefault("album", text)
            elif frame_id == "TIT2":
                out.setdefault("title", text)
                out.setdefault("track", text)
            elif frame_id == "TRCK":
                primary, total = _parse_index_pair(text)
                if primary is not None:
                    out["track_number"] = primary
                if total is not None:
                    out["track_total"] = total
            elif frame_id in {"TDRC", "TYER"}:
                out.setdefault("date", text)
                year = _parse_year_value(text)
                if year:
                    out.setdefault("year", year)
            elif frame_id == "TCON":
                out.setdefault("genre", text)
        elif frame_id == "COMM":
            # Skip comment frames for now
            continue

    return {k: v for k, v in out.items() if v not in (None, "", [])}
