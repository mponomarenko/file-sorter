import os
import sys
from types import SimpleNamespace
from pathlib import Path
from dataclasses import replace

# Ensure we can import `app.*`
THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.config import AppConfig
from app.categories import CategoryPath
from app.media import MediaHelper
from app.file_metadata import FileMetadata
from app.file_nodes import FileNodeBuilder
from app.folder_action import FolderAction


CATEGORY_ALIAS = {
    "Video": "Media/Videos/Movies",
    "Photo": "Media/Photos",
    "Document": "Software/Source_Code",
    "Documents": "Software/Source_Code",
    "Installer": "Software/Installers",
    "Iso": "Software/Installers",
    "Music": "Media/Music",
    "Archive": "System",
    "System": "System",
    "Source Code": "Software/Source_Code",
    "Unknown": "Unknown",
}

TUPLE_ALIAS = {
    ("Video", "Tv_Episode"): "Media/Videos/Shows",
}


def _folder_actions_keep_all(path: str) -> dict[str, FolderAction]:
    actions: dict[str, FolderAction] = {}
    current = Path(path).parent
    while current and str(current) not in ("", "/"):
        actions[current.as_posix()] = FolderAction.KEEP
        current = current.parent
    return actions


def _kind_from_mime(mime: str) -> str | None:
    if not mime:
        return None
    if mime.startswith("video/"):
        return "Video"
    if mime.startswith("audio/"):
        return "Music"
    if mime.startswith("image/"):
        return "Photo"
    if mime.startswith("application/zip"):
        return "Archive"
    return None


def _flatten_struct(struct: dict, prefix: str = ""):
    for key, val in struct.items():
        base = Path(prefix, key).as_posix().strip("/")
        if isinstance(val, dict):
            yield from _flatten_struct(val, base)
        elif isinstance(val, tuple):
            if val and isinstance(val[0], str) and (len(val) == 1 or isinstance(val[1], (str, type(None)))):
                yield (base, val)
                continue
            for fn in val:
                rp = Path(base, str(fn)).as_posix()
                yield (rp, "Document")
        elif isinstance(val, (list, set)):
            for fn in val:
                rp = Path(base, str(fn)).as_posix()
                yield (rp, "Document")
        else:
            rp = base
            yield (rp, val if val is not None else "Document")


def simulate_moves(struct: dict, strip_dirs: str, fixed_year: int = 2024):
    base_cfg = AppConfig.from_env()
    sd = [s.strip() for s in (strip_dirs.split(",") if strip_dirs else []) if s.strip()]
    if "src" not in sd:
        sd.append("src")
    cfg = replace(
        base_cfg,
        MAIN_TARGET="/target",
        STRIP_DIRS=sd,
        SOURCES=["/src"],
    )

    helper = MediaHelper(cfg)

    ts = int(__import__("time").mktime(__import__("time").strptime(f"{fixed_year}-06-15 12:00:00", "%Y-%m-%d %H:%M:%S")))
    original_stat = Path.stat  # type: ignore

    def fake_stat(self, *args, **kwargs):  # type: ignore
        try:
            s = str(self)
        except Exception:
            return original_stat(self, *args, **kwargs)
        if s.startswith("/src/"):
            return SimpleNamespace(st_mtime=ts, st_mode=0o100666)
        return original_stat(self, *args, **kwargs)

    Path.stat = fake_stat  # type: ignore

    planned = []
    try:
        flat = list(_flatten_struct(struct))
        for rel, val in flat:
            src = f"/src/{rel.strip('/')}"
            label = "Software/Source_Code"
            mime = "application/octet-stream"
            if isinstance(val, tuple):
                key = tuple(part for part in val if part)
                label = TUPLE_ALIAS.get(key, "/".join(key))
            elif isinstance(val, str) and "/" in val:
                if val.startswith("application/") or val.startswith("video/") or val.startswith("audio/") or val.startswith("image/") or val.startswith("text/"):
                    mime = val
                    mk = _kind_from_mime(mime)
                    if mk == "Video":
                        label = "Media/Videos/Movies"
                    elif mk == "Music":
                        label = "Media/Music"
                    elif mk == "Photo":
                        label = "Media/Photos"
                    elif mk == "Archive":
                        label = "System"
                    else:
                        label = "Software/Source_Code"
                else:
                    label = val
            elif isinstance(val, str):
                label = CATEGORY_ALIAS.get(val, "Software/Source_Code")

            category_path = CategoryPath(label)
            keep_map = {}
            cat_str = str(category_path)
            # Keep folder structure for source code and media files
            if cat_str.startswith("Software/Source_Code") or cat_str.startswith("Media/"):
                keep_map = _folder_actions_keep_all(src)
            builder = FileNodeBuilder(
                sources=cfg.SOURCES,
                folder_action_map=keep_map,
                source_wrapper_pattern=cfg.SOURCE_WRAPPER_REGEX,
            )
            node = builder.build(
                src,
                category=category_path,
                mime=mime,
                metadata=FileMetadata(),
                rule_match=None,
            )
            dst = helper.build_destination(node)
            planned.append((src, dst.destination))

        return planned
    finally:
        Path.stat = original_stat  # type: ignore


def norm(p: str):
    return "/" + "/".join(Path(p).parts[1:])


def test_video_movie_and_unsorted_json_based():
    struct = {
        "ExDropbox/Video/Movie1": {
            "foo.mp4": "video/mp4",
        },
        "Videos/unsorted": {
            "bar.mov": ("Video", "Tv_Episode"),
        },
    }
    planned = simulate_moves(struct, strip_dirs="ExDropbox,Video,Videos")
    exp1 = "/target/Media/Videos/Movies/Movie1/foo.mp4"
    exp2 = "/target/Media/Videos/Shows/unsorted/bar.mov"
    norm_planned = [norm(dst) for (_, dst) in planned]
    assert any(val == exp1 for val in norm_planned), f"planned={norm_planned}"
    assert any(val == exp2 for val in norm_planned), f"planned={norm_planned}"


def test_folder_and_backup_folder_json_based():
    struct = {
        "ProjectA/docs": {"readme.txt": "Software/Source_Code"},
        "Backup/ProjectA/docs": {"readme.txt": "Software/Source_Code"},
    }
    planned = simulate_moves(struct, strip_dirs="Backup")
    exp = "/target/Software/Source_Code/ProjectA/docs/readme.txt"
    dsts = [norm(d) for (_, d) in planned]
    assert dsts.count(exp) == 2


def test_python_project_and_backup_json_based():
    struct = {
        "ProjectX/pkg/mod": {"a.py": "Software/Source_Code", "b.py": "Software/Source_Code"},
        "Source Backup/ProjectX/pkg/mod": {"a.py": "Software/Source_Code"},
    }
    planned = simulate_moves(struct, strip_dirs="Source Backup,Backup")
    base = "/target/Software/Source_Code/ProjectX/pkg/mod"
    exp = {f"{base}/a.py", f"{base}/b.py"}
    got = {norm(d) for (_, d) in planned}
    assert exp.issubset(got)


def test_music_various_album_sorted_json_based():
    struct = {
        "Music/Album1": {"track1.mp3": "Media/Music"},
        "Downloads": {"song2.mp3": "Media/Music"},
    }
    planned = simulate_moves(struct, strip_dirs="Music,Downloads")
    print(f"Planned moves: {[norm(d) for (_, d) in planned]}")
    assert any(norm(d) == "/target/Media/Music/Album1/track1.mp3" for (_, d) in planned)
    # Without metadata, file goes directly into category (no "Unknown Artist" added)
    assert any(norm(d) == "/target/Media/Music/song2.mp3" for (_, d) in planned)


def test_docs_with_backups_and_odd_names_json_based():
    struct = {
        "Work/Reports/Q4": {
            "plan.docx": "Software/Source_Code",
            "plan - copy (1).docx": "Software/Source_Code",
            "spec.doc": "Software/Source_Code",
        },
        "Backup/Work/Reports/Q4": {
            "plan.docx": "Software/Source_Code",
        },
    }
    planned = simulate_moves(struct, strip_dirs="Backup")
    base = "/target/Software/Source_Code/Work/Reports/Q4"
    expect = {f"{base}/plan.docx", f"{base}/plan - copy (1).docx", f"{base}/spec.doc"}
    got = {norm(d) for (_, d) in planned}
    assert expect.issubset(got)
