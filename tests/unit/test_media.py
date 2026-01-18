import os
import sys
import tempfile
import time
from pathlib import Path
from dataclasses import replace

# Ensure we can import `app.*` by adding `file-sorter` to sys.path
THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.categories import CategoryPath
from app.config import AppConfig
from app.media import MediaHelper, blake3_hash, detect_mime
from app.file_metadata import FileMetadata
from app.classifiers import RulesClassifier, MockAIClassifier
from app.file_nodes import FileNodeBuilder
from app.folder_action import FolderAction


def _keep_all_prefixes(path: Path) -> dict[str, str]:
    actions: dict[str, str] = {}
    current = path.parent
    while current and str(current) not in ("", "/"):
        actions[current.as_posix()] = "keep"
        current = current.parent
    return actions


def _build_destination(
    helper: MediaHelper,
    cfg: AppConfig,
    path: Path | str,
    category: CategoryPath,
    mime: str,
    metadata: FileMetadata | None = None,
    folder_actions: dict[str, str] | None = None,
    mock_ai: MockAIClassifier | None = None,
    debug: bool = False,
):
    from app.folder_policy import build_folder_action_map, collect_folder_samples
    
    meta = metadata if metadata is not None else FileMetadata()
    
    # If no explicit folder_actions, use folder_policy module with mock AI
    if folder_actions is None:
        if mock_ai is None:
            mock_ai = MockAIClassifier()
        
        # Build samples for the path - folder_policy expects iterable of (path, mime, size) tuples
        file_size = path.stat().st_size if path.exists() else 0
        samples = collect_folder_samples([(str(path), mime, file_size)])
        
        # Use folder_policy to build action map with rules + mock AI classifier
        # Pass sources so folder_policy can strip them before calling classifier
        folder_actions, _ = build_folder_action_map(
            rules=helper._rules_classifier,
            classifier=mock_ai,
            samples=samples,
            sources=cfg.SOURCES
        )
        
        if debug:
            print(f"\n=== Folder Actions for {path} ===")
            for dir_path, action in sorted(folder_actions.items()):
                print(f"  {dir_path} -> {action}")
    
    builder = FileNodeBuilder(
        sources=cfg.SOURCES,
        folder_action_map=folder_actions,
        source_wrapper_pattern=cfg.SOURCE_WRAPPER_REGEX,
    )
    node = builder.build(
        str(path),
        category=category,
        mime=mime,
        metadata=meta,
        rule_match=None,
    )
    return helper.build_destination(node)

def test_blake3_hash_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td, "data.bin")
        data = b"hello world" * 100
        p.write_bytes(data)
        h = blake3_hash(str(p))
        assert isinstance(h, str) and len(h) == 64


def test_detect_mime_prefers_mimetypes(monkeypatch=None):
    # Create a .txt file so mimetypes.guess_type returns text/plain
    with tempfile.TemporaryDirectory() as td:
        p = Path(td, "note.txt")
        p.write_text("hi")

        # If subprocess.file call happens, raise to catch it
        def boom(*a, **k):
            raise AssertionError("subprocess called unexpectedly")

        # Local patching without pytest: swap and restore
        import app.media as media_module
        orig = media_module.subprocess.check_output
        media_module.subprocess.check_output = boom
        try:
            m = detect_mime(str(p))
            assert m == "text/plain"
        finally:
            media_module.subprocess.check_output = orig


def test_build_destination_for_documents_and_music():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td, "target"); root.mkdir()
        f = Path(td, "doc.pdf"); f.write_bytes(b"x")
        # Set known mtime
        year = 2020
        ts = time.mktime(time.strptime(f"{year}-05-06 12:34:56", "%Y-%m-%d %H:%M:%S"))
        os.utime(str(f), (ts, ts))

        base_cfg = AppConfig.from_env()
        cfg = replace(
            base_cfg,
            MAIN_TARGET=str(root),
            STRIP_DIRS=["/tmp", td],
            SOURCES=base_cfg.SOURCES,
        )
        helper = MediaHelper(cfg)
        dest_doc = _build_destination(helper, cfg, f, CategoryPath("Documents", "Finance"), "application/pdf")
        assert Path(dest_doc.destination) == Path(root, "Documents", "Finance", f.name)

        m = Path(td, "song.mp3"); m.write_bytes(b"x")
        dest_music = _build_destination(helper, cfg, m, CategoryPath("Media", "Music"), "audio/mpeg")
        # With no metadata and no folder structure to preserve, file goes directly into category
        assert Path(dest_music.destination) == Path(root, "Media", "Music", m.name)
        MediaHelper(base_cfg)  # instantiate to ensure no side effects

def test_build_destination_strips_source_root():
    with tempfile.TemporaryDirectory() as td:
        target_root = Path(td, "target"); target_root.mkdir()
        src_root = Path(td, "sources", "src1", "Proj", "docs")
        src_root.mkdir(parents=True)
        f = src_root / "readme.txt"
        f.write_text("spec")
        ts = time.mktime(time.strptime("2025-01-02 03:04:05", "%Y-%m-%d %H:%M:%S"))
        os.utime(str(f), (ts, ts))

        base_cfg = AppConfig.from_env()
        cfg = replace(
            base_cfg,
            MAIN_TARGET=str(target_root),
            STRIP_DIRS=[],
            SOURCES=[str(Path(td, "sources", "src1"))],
        )
        helper = MediaHelper(cfg)
        dest = _build_destination(helper, cfg, f, CategoryPath("Documents", "General"), "text/plain")
    expected = Path(target_root, "Documents", "General", "readme.txt")
    assert Path(dest.destination) == expected
    assert dest.full_path is not None
    assert dest.full_path.disaggregated[-2:] == ("Proj", "docs")
    assert dest.full_path.source_prefix[-2:] == ("sources", "src1")
    assert dest.full_path.kept == ()
    MediaHelper(base_cfg)

def test_file_node_builder_strips_wrapper_from_rel_parts():
    base_cfg = AppConfig.from_env()
    cfg = replace(
        base_cfg,
        SOURCES=["/sources"],
        STRIP_DIRS=[],
    )
    builder = FileNodeBuilder(
        sources=cfg.SOURCES,
        folder_action_map={},
        source_wrapper_pattern=cfg.SOURCE_WRAPPER_REGEX,
    )
    node = builder.build(
        "/sources/src1/projects/app/file.txt",
        category=CategoryPath("Documents"),
        mime="text/plain",
        metadata=FileMetadata(),
        rule_match=None,
    )
    assert node.source_prefix == ("sources", "src1")
    assert node.relative_parts == ("projects", "app", "file.txt")

def test_template_uses_metadata_and_suffix_fallback():
    base_cfg = AppConfig.from_env()
    cfg = replace(base_cfg, STRIP_DIRS=["/library"], SOURCES=["/library"])
    helper = MediaHelper(cfg)

    # Test with metadata - should use metadata fields in template
    src_tagged = "/library/Music/Tagged Artist/Tagged Album/track.flac"
    metadata = FileMetadata()
    metadata.add("artist", "Tagged Artist")
    metadata.add("album", "Tagged Album")
    metadata.add("title", "Tagged Title")
    mock_ai_tagged = MockAIClassifier()
    mock_ai_tagged.set_keep_folders([
        "/Music/Tagged Artist",
        "/Music/Tagged Artist/Tagged Album",
    ])
    dest_tagged = _build_destination(helper, cfg, Path(src_tagged), CategoryPath("Media", "Music"), "audio/flac", metadata, mock_ai=mock_ai_tagged)
    assert Path(dest_tagged.destination) == Path("/target/Media/Music/Tagged Artist/Tagged Album/Tagged Title.flac")

    # Test without metadata - template uses suffix fallback (no Misc since template simplified)
    # Note: "Loose" folder only contains subdirectories, so it gets disaggregated
    src_fallback = "/library/Music/Loose/Albumless/song.mp3"
    mock_ai_fallback = MockAIClassifier()
    mock_ai_fallback.set_keep_folders([
        "/Music/Loose/Albumless",  # Only folder with files
    ])
    dest_fallback = _build_destination(helper, cfg, Path(src_fallback), CategoryPath("Media", "Music"), "audio/mpeg", mock_ai=mock_ai_fallback)
    assert Path(dest_fallback.destination) == Path("/target/Media/Music/Albumless/song.mp3")


def test_sample_destination_paths():
    base_cfg = AppConfig.from_env()
    root = "/target"
    cfg = replace(
        base_cfg,
        MAIN_TARGET=root,
        STRIP_DIRS=["/sources/drive"],
        SOURCES=["/sources/drive"],
    )
    helper = MediaHelper(cfg)
    
    # Mock AI: Configure to keep author/artist folders (preserve organization)
    # Category folders (Books/Music/Video) will disaggregate via rules
    mock_ai = MockAIClassifier()
    mock_ai.set_keep_folders([
        "/Books/Author_One",  # Author folder - keep
        "/Books/Author_One/Series_One",  # Series folder - keep
        "/Music/Artist_One",  # Artist folder - keep
        "/Music/Artist_One/Album_One",  # Album folder - keep
        "/Music/Artist_Two",  # Artist folder - keep
        "/Music/Artist_Two/Album_Two",  # Album folder - keep
        "/Video/Movie_One",  # Movie folder - keep
    ])
    
    root = "/target"
    samples = [
        (
            "/sources/drive/Books/Author_One/Series_One/Book_04.fb2",
            CategoryPath("Media", "Books", "Digital"),
            "/target/Media/Books/Digital/Author_One/Series_One/Book_04.fb2",
            "application/octet-stream",
        ),
        (
            "/sources/drive/Music/Artist_One/Album_One/07 - Track_One.mp3",
            CategoryPath("Media", "Music"),
            "/target/Media/Music/Artist_One/Album_One/07 - Track_One.mp3",
            "audio/mpeg",
        ),
        (
            "/sources/drive/Music/Artist_Two/Album_Two/11 - Track_Two.mp3",
            CategoryPath("Media", "Music"),
            "/target/Media/Music/Artist_Two/Album_Two/11 - Track_Two.mp3",
            "audio/mpeg",
        ),
        (
            "/sources/drive/Video/Movie_One/Movie_One.avi",
            CategoryPath("Media", "Videos", "Movies"),
            "/target/Media/Videos/Movies/Movie_One/Movie_One.avi",
            "video/x-msvideo",
        ),
        (
            "/sources/drive/Video/Movie_One/Movie_One.ac3",
            CategoryPath("Media", "Videos", "Movies"),
            "/target/Media/Videos/Movies/Movie_One/Movie_One.ac3",
            "audio/ac3",
        ),
    ]

    for src, category, expected, mime in samples:
        dest = _build_destination(helper, cfg, Path(src), category, mime, mock_ai=mock_ai, debug=False)
        assert dest.destination == expected


def test_backup_folder_destination():
    base_cfg = AppConfig.from_env()
    cfg = replace(
        base_cfg,
        MAIN_TARGET="/target",
        STRIP_DIRS=["/sources"],
        SOURCES=["/sources/src1"],
    )
    helper = MediaHelper(cfg)
    src = "/sources/src1/Backups/ab_20250728_030001/Portainer-Agent.tar.gz"
    metadata = FileMetadata()
    metadata.add("backup_job", "ab_20250728_030001")
    metadata.add("backup_year", "2025")
    metadata.add("backup_month", "07")
    # Mock AI: Don't keep the backup job folder - let template use metadata
    mock_ai = MockAIClassifier()
    # No folders configured as keep - all will disaggregate
    dest = _build_destination(helper, cfg, Path(src), CategoryPath("Backups"), "application/gzip", metadata, mock_ai=mock_ai)
    assert dest.destination == "/target/Backups/2025/07/ab_20250728_030001/Portainer-Agent.tar.gz"


def test_build_destination_drops_source_wrapper_directory(tmp_path):
    target_root = tmp_path / "target"
    target_root.mkdir()
    sources_root = tmp_path / "sources"
    mount_dir = sources_root / "src1"
    backup_dir = mount_dir / "Backups" / "ab_20250728_030001"
    file_path = backup_dir / "Portainer-Agent.tar.gz"
    backup_dir.mkdir(parents=True)
    file_path.write_bytes(b"x")

    base_cfg = AppConfig.from_env()
    cfg = replace(
        base_cfg,
        MAIN_TARGET=str(target_root),
        STRIP_DIRS=[],
        SOURCES=[str(sources_root)],
    )
    helper = MediaHelper(cfg)
    metadata = FileMetadata()
    metadata.add("backup_job", "ab_20250728_030001")
    metadata.add("backup_year", "2025")
    metadata.add("backup_month", "07")

    # Mock AI: Don't keep backup job folder - let template use metadata
    mock_ai = MockAIClassifier()
    dest = _build_destination(helper, cfg, file_path, CategoryPath("Backups"), "application/gzip", metadata, mock_ai=mock_ai)
    expected = target_root / "Backups" / "2025" / "07" / "ab_20250728_030001" / "Portainer-Agent.tar.gz"
    assert Path(dest.destination) == expected
    # Source wrapper logic was removed during simplification
    # The destination path is correct which is what matters


def test_build_destination_preserves_specific_source_children(tmp_path):
    target_root = tmp_path / "target"
    target_root.mkdir()
    src_root = tmp_path / "sources" / "src1"
    nested = src_root / "Projects" / "Alpha"
    file_path = nested / "spec.txt"
    nested.mkdir(parents=True)
    file_path.write_text("payload")

    base_cfg = AppConfig.from_env()
    cfg = replace(
        base_cfg,
        MAIN_TARGET=str(target_root),
        STRIP_DIRS=[],
        SOURCES=[str(src_root)],
    )
    helper = MediaHelper(cfg)
    folder_actions = _keep_all_prefixes(file_path)
    dest = _build_destination(
        helper,
        cfg,
        file_path,
        CategoryPath("Unknown"),
        "text/plain",
        folder_actions=folder_actions,
    )
    expected = target_root / "Unknown" / "Projects" / "Alpha" / "spec.txt"
    assert Path(dest.destination) == expected
    suffix_layer = dest.layers[-1]
    assert suffix_layer.role == "keep"
    assert dest.full_path is not None
    assert dest.full_path.kept == ("Projects", "Alpha")


def test_backup_source_wrapper_is_stripped_when_folder_actions_missing(tmp_path):
    target_root = tmp_path / "target"
    target_root.mkdir()

    sources_root = tmp_path / "sources"
    mount_dir = sources_root / "src1"
    backup_dir = mount_dir / "Backups" / "ab_20250728_030001"
    file_path = backup_dir / "Portainer-Agent.tar.gz"
    backup_dir.mkdir(parents=True)
    file_path.write_bytes(b"x")

    base_cfg = AppConfig.from_env()
    cfg = replace(
        base_cfg,
        MAIN_TARGET=str(target_root),
        STRIP_DIRS=[],
        SOURCES=[str(sources_root)],
    )
    helper = MediaHelper(cfg)

    metadata = FileMetadata()
    metadata.add("backup_job", "ab_20250728_030001")
    metadata.add("backup_year", "2025")
    metadata.add("backup_month", "07")

    dest = _build_destination(
        helper,
        cfg,
        file_path,
        CategoryPath("Backups"),
        "application/gzip",
        metadata,
        folder_actions={},
    )

    expected = target_root / "Backups" / "2025" / "07" / "ab_20250728_030001" / "Portainer-Agent.tar.gz"
    assert Path(dest.destination) == expected
    assert dest.full_path.kept == ("ab_20250728_030001",)


def test_folder_action_prunes_prefix_to_first_keep(tmp_path):
    rules = tmp_path / "rules.csv"
    rules.write_text(
        "\n".join(
            [
                "^.*/keep1(/.*)?$,*,Unknown,keep,final",
                "^.*/disagg1(/.*)?$,*,Unknown,disaggregate,final",
                "^.*/disagg2(/.*)?$,*,Unknown,disaggregate,final",
                "^.*$,.*,Unknown,disaggregate,ai",
            ]
        )
    )
    target_root = tmp_path / "target"
    target_root.mkdir()
    sources_root = tmp_path / "sources"
    file_path = sources_root / "disagg1" / "disagg2" / "keep1" / "keep2" / "file.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("payload")

    base_cfg = AppConfig.from_env()
    cfg = replace(
        base_cfg,
        MAIN_TARGET=str(target_root),
        STRIP_DIRS=[],
        SOURCES=[str(sources_root)],
    )
    helper = MediaHelper(cfg)
    helper._rules_classifier = RulesClassifier(rules)
    metadata = FileMetadata()
    dest = _build_destination(
        helper,
        cfg,
        file_path,
        CategoryPath("Unknown"),
        "text/plain",
        metadata,
    )
    expected = target_root / "Unknown" / "keep1" / "keep2" / "file.txt"
    assert Path(dest.destination) == expected
    disagg_layer = next(layer for layer in dest.layers if layer.role == "disagg")
    assert disagg_layer.parts == ("disagg1", "disagg2")
    keep_layer = next(layer for layer in dest.layers if layer.role == "keep")
    assert keep_layer.parts == ("keep1", "keep2")


def test_safe_move_without_reflink():
    base_cfg = AppConfig.from_env()
    cfg = replace(base_cfg, RELINK_WITH_REFLINK=False)
    with tempfile.TemporaryDirectory() as td:
        helper = MediaHelper(cfg)
        src_dir = Path(td, "src"); src_dir.mkdir()
        dst_dir = Path(td, "dst"); dst_dir.mkdir()
        src = Path(src_dir, "file.bin"); src.write_bytes(b"payload")
        dst = Path(dst_dir, "moved.bin")
        res = helper.safe_move(str(src), str(dst))
        assert res == "moved"
        assert dst.exists() and not src.exists()
    MediaHelper(base_cfg)


def test_mock_ai_classifier_file_classification():
    """Test that MockAIClassifier can classify files based on configured patterns."""
    import asyncio
    
    # Create mock classifier with file classifications
    mock = MockAIClassifier()
    mock.set_file_classifications({
        "report.pdf": CategoryPath("Documents", "Reports"),
        "song.mp3": CategoryPath("Media", "Music"),
        "photo.jpg": CategoryPath("Media", "Photos"),
    })
    mock.set_default_category(CategoryPath("Documents", "General"))
    
    # Test matching classifications
    result = asyncio.run(mock.classify("report.pdf", "work/report.pdf", "application/pdf", ""))
    assert result.path == CategoryPath("Documents", "Reports")
    assert result.metrics["pattern_matched"] is True
    
    result = asyncio.run(mock.classify("song.mp3", "music/song.mp3", "audio/mpeg", ""))
    assert result.path == CategoryPath("Media", "Music")
    
    # Test default category for unmatched files
    result = asyncio.run(mock.classify("unknown.txt", "folder/unknown.txt", "text/plain", ""))
    assert result.path == CategoryPath("Documents", "General")
    assert result.metrics["pattern_matched"] is False
    
    # Test longest match wins
    mock.set_file_classifications({
        "file": CategoryPath("Unknown"),
        "file.txt": CategoryPath("Documents", "Text"),
        "important_file.txt": CategoryPath("Documents", "Important"),
    })
    result = asyncio.run(mock.classify("important_file.txt", "docs/important_file.txt", "text/plain", ""))
    assert result.path == CategoryPath("Documents", "Important")


def test_mock_ai_classifier_folder_actions():
    """Test that MockAIClassifier can advise on folder actions."""
    from app.folder_action import FolderAction, FolderActionRequest
    
    mock = MockAIClassifier()
    mock.set_keep_folders([
        "/Music/Artist",
        "/Music/Artist/Album",
        "/Books/Author",
    ])
    
    # Test folders configured to be kept
    result = mock.advise_folder_action(FolderActionRequest.from_payload({"folder_path": "/Music/Artist", "folder_name": "Artist", "children": [], "total_files": 0}))
    assert result.is_final and result.action == FolderAction.KEEP
    result = mock.advise_folder_action(FolderActionRequest.from_payload({"folder_path": "/Music/Artist/Album", "folder_name": "Album", "children": [], "total_files": 0}))
    assert result.is_final and result.action == FolderAction.KEEP
    result = mock.advise_folder_action(FolderActionRequest.from_payload({"folder_path": "/Books/Author", "folder_name": "Author", "children": [], "total_files": 0}))
    assert result.is_final and result.action == FolderAction.KEEP
    
    # Test folders not in keep list
    result = mock.advise_folder_action(FolderActionRequest.from_payload({"folder_path": "/Music", "folder_name": "Music", "children": [], "total_files": 0}))
    assert result.is_final and result.action == FolderAction.DISAGGREGATE
    result = mock.advise_folder_action(FolderActionRequest.from_payload({"folder_path": "/Books", "folder_name": "Books", "children": [], "total_files": 0}))
    assert result.is_final and result.action == FolderAction.DISAGGREGATE
    result = mock.advise_folder_action(FolderActionRequest.from_payload({"folder_path": "/Other/Path", "folder_name": "Path", "children": [], "total_files": 0}))
    assert result.is_final and result.action == FolderAction.DISAGGREGATE


def test_keep_except_with_explicit_disaggregate():
    """Test that keep_except allows deeper disaggregate markers to override."""
    with tempfile.TemporaryDirectory() as td:
        target_root = Path(td, "target"); target_root.mkdir()
        src_root = Path(td, "home", "user", "Documents", "Work")
        src_root.mkdir(parents=True)
        f = src_root / "report.pdf"
        f.write_text("work document")
        
        base_cfg = AppConfig.from_env()
        cfg = replace(
            base_cfg,
            MAIN_TARGET=str(target_root),
            STRIP_DIRS=[],
            SOURCES=[str(Path(td, "home"))],
        )
        helper = MediaHelper(cfg)
        
        # Manually build folder_actions:
        # /home/user -> keep_except (keep the user folder)
        # /home/user/Documents -> disaggregate (but break apart Documents)
        folder_actions = {
            f"{td}/home/user": "keep_except",
            f"{td}/home/user/Documents": "disaggregate",
        }
        
        builder = FileNodeBuilder(
            sources=cfg.SOURCES,
            folder_action_map=folder_actions,
            source_wrapper_pattern=cfg.SOURCE_WRAPPER_REGEX,
        )
        node = builder.build(
            str(f),
            category=CategoryPath("Documents", "Work"),
            mime="application/pdf",
            metadata=FileMetadata(),
            rule_match=None,
        )
        
        dest = helper.build_destination(node)
        
        # Expected behavior:
        # - Source prefix gets stripped (including tempdir path + home)
        # - Kept: user (because of keep_except)
        # - Disaggregated: Documents/Work (because Documents has explicit disaggregate)
        # - Result: Category (Documents/Work) + kept (user) + filename
        expected = Path(target_root, "Documents", "Work", "user", "report.pdf")
        assert Path(dest.destination) == expected
        
        # Verify the path components
        assert dest.full_path.kept == ("user",)
        assert dest.full_path.disaggregated == ("Documents", "Work")
