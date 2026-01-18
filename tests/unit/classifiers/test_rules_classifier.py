import asyncio
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from dataclasses import replace

from app.categories import CategoryPath
from app.config import AppConfig
from app.media import MediaHelper
from app.classifiers import RulesClassifier, ClassifierResponse, MockAIClassifier
from app.file_nodes import FileNodeBuilder
from app.file_metadata import FileMetadata
from app.folder_policy import build_folder_action_map, collect_folder_samples


def classify_sync(clf: RulesClassifier, *args):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(clf.classify(*args))
    finally:
        loop.run_until_complete(clf.close())
        loop.close()


def test_rules_classifier_matches_photo(tmp_path):
    rules = tmp_path / "rules.csv"
    rules.write_text("^.*$,image/.+,Media/Photos,keep,final\n")
    clf = RulesClassifier(rules)

    response = classify_sync(clf, "pic.jpg", "album/pic.jpg", "image/jpeg", "")
    assert isinstance(response, ClassifierResponse)
    assert isinstance(response.path, CategoryPath)
    assert str(response.path) == "Media/Photos"
    match = clf.match("pic.jpg", "album/pic.jpg", "image/jpeg")
    assert match is not None and match.rule.line_number == 1


def test_receipt_rules_are_case_insensitive():
    clf = RulesClassifier()
    samples = [
        "Invoice-2023.pdf",
        "invoice-2024.pdf",
    ]
    for name in samples:
        response = classify_sync(clf, name, f"records/{name}", "application/pdf", "")
        assert isinstance(response.path, CategoryPath)
        assert str(response.path) == "Documents/Finance"
        match = clf.match(name, f"records/{name}", "application/pdf")
        assert match is not None
        assert str(match.rule.category_path) == "Documents/Finance"


def test_generic_pdf_is_not_ebook():
    clf = RulesClassifier()
    response = classify_sync(clf, "foo.pdf", "misc/foo.pdf", "application/pdf", "")
    assert isinstance(response.path, CategoryPath)
    assert str(response.path) == "Documents/Other"
    match = clf.match("foo.pdf", "misc/foo.pdf", "application/pdf")
    assert match is not None
    assert str(match.rule.category_path) == "Documents/Other"


def test_rules_classifier_path_glob(tmp_path):
    rules = tmp_path / "rules.csv"
    rules.write_text("^.*/node_modules/.*,.*,Software/Dependencies,keep,final\n^.*$,.*,Unknown,disaggregate,ai\n")
    clf = RulesClassifier(rules)

    response = classify_sync(
        clf,
        "index.js",
        "packages/node_modules/pkg/index.js",
        "application/javascript",
        "",
    )
    assert isinstance(response, ClassifierResponse)
    assert isinstance(response.path, CategoryPath)
    assert str(response.path) == "Software/Dependencies"


def test_rules_classifier_detects_invalid_rule(tmp_path):
    rules = tmp_path / "rules.csv"
    rules.write_text("^*invalid$,*,Photo,disaggregate,final\n")
    clf = RulesClassifier(rules)
    assert clf.ensure_available() is False


def test_rules_classifier_ai_hint(tmp_path):
    from app.folder_action import RequiresAI
    rules = tmp_path / "rules.csv"
    rules.write_text("^.*/docs/.*,.*,Software/Source_Code,disaggregate,ai\n")
    clf = RulesClassifier(rules)
    rule_match = clf.match("readme.md", "proj/docs/readme.md", "text/markdown")
    assert rule_match is not None and rule_match.rule.requires_ai == RequiresAI.AI
    assert rule_match.rule.line_number == 1


def test_rules_classifier_agents_marker():
    clf = RulesClassifier()
    response = classify_sync(clf, "AGENTS.md", "src/AGENTS.md", "text/markdown", "")
    assert isinstance(response, ClassifierResponse)
    assert str(response.path) == "Software/Source_Code"


def test_rules_classifier_sample_assignments():
    clf = RulesClassifier()
    cfg = replace(AppConfig.from_env(), MAIN_TARGET="/target", SOURCES=["/sources/drive"])
    media = MediaHelper(cfg)

    cases = [
        {
            "source": "/sources/drive/Books/Author_One/Series_One/Book_04.fb2",
            "mime": "application/x-fictionbook",
            "expected_category": "Media/Books/Digital",
            "expected_destination": "/target/Media/Books/Digital/Author_One/Series_One/Book_04.fb2",
        },
        {
            "source": "/sources/drive/Music/Artist_One/Album_One/07 - Track_One.mp3",
            "mime": "audio/mpeg",
            "expected_category": "Media/Music",
            "expected_destination": "/target/Media/Music/Artist_One/Album_One/07 - Track_One.mp3",
        },
        {
            "source": "/sources/drive/Music/Artist_Two/Album_Two/11 - Track_Two.mp3",
            "mime": "audio/mpeg",
            "expected_category": "Media/Music",
            "expected_destination": "/target/Media/Music/Artist_Two/Album_Two/11 - Track_Two.mp3",
        },
        {
            "source": "/sources/drive/Video/Movie_One/Movie_One.avi",
            "mime": "video/x-msvideo",
            "expected_category": "Media/Videos/Movies",
            "expected_destination": "/target/Media/Videos/Movies/Movie_One/Movie_One.avi",
        },
        {
            "source": "/sources/drive/Video/Movie_One/Movie_One.ac3",
            "mime": "audio/ac3",
            "expected_category": "Media/Videos/Movies",
            "expected_destination": "/target/Media/Videos/Movies/Movie_One/Movie_One.ac3",
        },
    ]

    # Set up mock AI to keep author/artist folders and their subfolders
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
    
    # Collect folder samples and build folder action map
    file_samples = [(case["source"], case["mime"], 0) for case in cases]  # Add size=0
    folder_samples = collect_folder_samples(file_samples)
    folder_action_map, _ = build_folder_action_map(clf, mock_ai, folder_samples, cfg.SOURCES, cfg.SOURCE_WRAPPER_REGEX)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        for case in cases:
            name = Path(case["source"]).name
            rel = case["source"]
            response = loop.run_until_complete(clf.classify(name, rel, case["mime"], ""))
            assert isinstance(response, ClassifierResponse)
            assert isinstance(response.path, CategoryPath)
            assert str(response.path) == case["expected_category"]
            match = clf.match(name, rel, case["mime"])
            metadata = FileMetadata()
            if match:
                for key, value in match.named_groups().items():
                    metadata.add(key, value)
            builder = FileNodeBuilder(
                sources=cfg.SOURCES,
                folder_action_map=folder_action_map,
                source_wrapper_pattern=cfg.SOURCE_WRAPPER_REGEX,
            )
            node = builder.build(
                case["source"],
                category=response.path,
                mime=case["mime"],
                metadata=metadata,
                rule_match=match,
            )
            dest = media.build_destination(node)
            assert dest.destination == case["expected_destination"]
            if match:
                assert match.rule.line_number and match.rule.line_number > 0
    finally:
        loop.run_until_complete(clf.close())
        loop.close()


def test_backup_rules_win_over_generic_archives():
    clf = RulesClassifier()
    path = "/sources/src1/Backups/ab_20250728_030001/Portainer-Agent.tar.gz"
    match = clf.match(Path(path).name, path, "application/gzip")
    assert match is not None
    assert str(match.rule.category_path) == "Backups"
