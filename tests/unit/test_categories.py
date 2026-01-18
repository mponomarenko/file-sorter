import json
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.categories import Categories

# TODO:  delete all old style calls and change the callsites
def _render_template_compat(categories, template, context):
    """Helper to convert old-style render_template calls to new signature.
    
    Old style had everything in context dict.
    New style has explicit args for category_path, kept_path, filename.
    """
    metadata = {k: v for k, v in context.items() 
                if k not in ('category_path', 'category', 'filename', 'file_stem', 'extension', 'suffix')}
    
    return categories.render_template(
        template,
        metadata,
        category_path=context.get('category_path') or context.get('category'),
        kept_path=context.get('suffix'),
        filename=context.get('filename')
    )


def test_categories_accepts_list_representation():
    data = {
        "Root": ["Bravo", "Alpha"],
        "Nested": {
            "Leaf": {},
        },
    }

    categories = Categories.from_source(data)

    assert categories.normalize("Root/Alpha") is not None
    assert categories.normalize("Root/Bravo") is not None

    compact = json.loads(categories.to_json(compact=True))
    assert compact["Root"] == ["Alpha", "Bravo"]


def test_compact_json_excludes_templates_and_is_small():
    from app.config import config

    compact_json = config.categories.to_json(compact=True)
    assert "_template" not in compact_json
    assert len(compact_json) < 4000


def test_parse_categories_from_json_file():
    """Test that we can actually parse the real categories.csv file."""
    from app.config import config
    from app.path_models import CategoryPath
    import json
    
    # This test uses the real config to verify the file can be parsed
    categories = config.categories
    
    # Verify basic structure
    assert categories is not None
    tree = categories.tree()
    assert "Media" in tree
    assert "Documents" in tree
    assert "Software" in tree
    
    # Verify __default__ template exists
    default_template = categories.template_for(CategoryPath("NonExistent", "Category"))
    assert default_template is not None
    assert "{ai_category}" in default_template
    
    # Verify specific templates exist
    music_template = categories.template_for(CategoryPath("Media", "Music"))
    assert music_template is not None
    assert "{artist" in music_template
    
    books_template = categories.template_for(CategoryPath("Media", "Books", "Digital"))
    assert books_template is not None
    assert "{author" in books_template
    
    # Verify we can normalize paths
    normalized = categories.normalize("media/music")
    assert normalized is not None
    assert str(normalized) == "Media/Music"
    
    # Verify flattened view works
    flattened = categories.flattened()
    assert "Media" in flattened
    assert "Music" in flattened["Media"]


def test_categories_allow_single_custom_suffix():
    from app.config import config

    path = config.categories.normalize_result("Documents/Other/Academic")
    assert path is not None
    assert str(path) == "Documents/Other/Academic"

    longer = config.categories.normalize_result("Documents/Other/Academic/Exams")
    # TODO: this is wrong. this is longer than one extra segment and should be
    # just Documents/Other/Academic
    assert str(longer) == "Documents/Other/Academic/Exams"


def test_music_template_rendering_with_metadata():
    """Test Music template renders correctly with and without metadata tags."""
    from app.path_models import CategoryPath
    
    # Create test categories with Music template (new format without category prefix)
    data = {
        "Media": {
            "Music": {
                "_template": "{artist|Unknown Artist}/{album}"
            }
        }
    }
    categories = Categories.from_source(data)
    music_category = CategoryPath("Media", "Music")
    template = categories.template_for(music_category)
    
    # Template should be: {artist|Unknown Artist}/{album} (category prepended, filename appended automatically)
    assert template == "{artist|Unknown Artist}/{album}"
    
    # Test with full metadata
    context_full = {
        "artist": "The Beatles",
        "album": "Abbey Road",
        "title": "Come Together",
        "filename": "01 - Come Together.mp3",
        "category_path": "Media/Music",
    }
    rendered_full = _render_template_compat(categories, template, context_full)
    assert rendered_full == "Media/Music/The Beatles/Abbey Road/01 - Come Together.mp3"
    
    # Test with partial metadata (no title)
    context_partial = {
        "artist": "Pink Floyd",
        "album": "The Wall",
        "filename": "track05.mp3",
        "category_path": "Media/Music",
    }
    rendered_partial = _render_template_compat(categories, template, context_partial)
    assert rendered_partial == "Media/Music/Pink Floyd/The Wall/track05.mp3"
    
    # Test with no metadata (falls back to Unknown Artist)
    context_empty = {
        "filename": "unknown_song.mp3",
        "category_path": "Media/Music",
    }
    rendered_empty = _render_template_compat(categories, template, context_empty)
    # No album means that segment becomes empty and gets filtered out
    assert rendered_empty == "Media/Music/Unknown Artist/unknown_song.mp3"


def test_default_template_rendering():
    """Test that __default__ template is used for categories without specific templates."""
    from app.path_models import CategoryPath
    
    # Create test categories with __default__ template
    data = {
        "__default__": {
            "_template": "{ai_category|rule_category}"
        },
        "Documents": {
            "General": {},
            "Other": {
                "_template": "{ai_category}"
            }
        },
        "Software": {
            "Dependencies": {}
        }
    }
    categories = Categories.from_source(data)
    
    # Categories without specific templates should get default template
    general_docs = CategoryPath("Documents", "General")
    template = categories.template_for(general_docs)
    
    # Should get default template: {ai_category|rule_category}
    assert template == "{ai_category|rule_category}"
    
    # Test rendering with ai_category (category_path is auto-prepended)
    context_with_ai = {
        "category_path": "Documents/General",
        "ai_category": "Resumes/Engineering",
        "filename": "resume.pdf",
    }
    rendered_ai = _render_template_compat(categories, template, context_with_ai)
    assert rendered_ai == "Documents/General/Resumes/Engineering/resume.pdf"
    
    # Test rendering with rule_category fallback (no ai_category)
    context_with_rule = {
        "category_path": "Software/Dependencies",
        "rule_category": "Projects/Python",
        "filename": "main.py",
    }
    rendered_rule = _render_template_compat(categories, template, context_with_rule)
    assert rendered_rule == "Software/Dependencies/Projects/Python/main.py"
    
    # Test rendering with neither (falls back to literal "rule_category")
    context_no_category = {
        "category": "Unknown",
        "filename": "file.txt",
    }
    rendered_fallback = _render_template_compat(categories, template, context_no_category)
    assert rendered_fallback == "Unknown/rule_category/file.txt"
    
    # Software/Dependencies also has no specific template - should get __default__
    dependencies = CategoryPath("Software", "Dependencies")
    template_deps = categories.template_for(dependencies)
    assert template_deps == "{ai_category|rule_category}"
    
    # Categories WITH specific templates should NOT get default
    docs_other = CategoryPath("Documents", "Other")
    template_other = categories.template_for(docs_other)
    assert template_other != "{ai_category|rule_category}"  # Should have its own template
    assert template_other == "{ai_category}"  # From the data above


def test_ai_long_category_handling():
    """Test rendering when AI provides a long category path."""
    from app.path_models import CategoryPath
    
    # Create test categories with Documents/Other template
    data = {
        "Documents": {
            "Other": {
                "_template": "Documents/Other/{ai_category|rule_category}/{ai_category}/{filename}"
            }
        }
    }
    categories = Categories.from_source(data)
    
    # Documents/Other has template with {ai_category}
    docs_other = CategoryPath("Documents", "Other")
    template = categories.template_for(docs_other)
    
    # Template is: Documents/Other/{ai_category|rule_category}/{ai_category}/{filename}
    assert "{ai_category" in template
    assert template.count("{ai_category") >= 1
    
    # Test with AI providing a long category path
    context_long = {
        "ai_category": "Resumes/Engineering/Academic",
        "filename": "phd_resume.pdf",
    }
    rendered_long = _render_template_compat(categories, template, context_long)
    # The ai_category placeholder gets replaced with the full path
    # Template has DUPLICATE {ai_category}, so it appears twice (this might be intentional?)
    assert "Resumes" in rendered_long and "Engineering" in rendered_long and "Academic" in rendered_long
    # First {ai_category|rule_category} resolves to "Resumes/Engineering/Academic"
    # Second {ai_category} also resolves to "Resumes/Engineering/Academic"
    # Result: Documents/Other/Resumes/Engineering/Academic/Resumes/Engineering/Academic/phd_resume.pdf
    assert rendered_long.startswith("Documents/Other/Resumes")
    
    # Test with short AI category
    context_short = {
        "ai_category": "Personal",
        "filename": "letter.docx",
    }
    rendered_short = _render_template_compat(categories, template, context_short)
    # Both placeholders resolve to "Personal", so we get Personal/Personal
    assert "Personal" in rendered_short
    
    # Test with no ai_category (falls back to rule_category for first, literal for second)
    context_rule = {
        "rule_category": "General",
        "filename": "doc.txt",
    }
    rendered_rule = _render_template_compat(categories, template, context_rule)
    # First {ai_category|rule_category} falls back to rule_category="General"
    # Second {ai_category} has no value and no fallback - the segment becomes empty and is filtered out
    # Result: Documents/Other/General/doc.txt
    assert rendered_rule == "Documents/Other/General/doc.txt"


def test_template_rendering_with_suffix():
    """Test that templates can use {suffix} for kept folder paths."""
    # Create test categories with template using suffix
    data = {
        "Media": {
            "Books": {
                "Digital": {
                    "_template": "Media/Books/Digital/{author|Unknown Author}/{suffix}/{filename}"
                }
            }
        }
    }
    categories = Categories.from_source(data)
    
    template = "Media/Books/Digital/{author|Unknown Author}/{suffix}/{filename}"
    
    context_with_suffix = {
        "author": "Author One",
        "suffix": "Foundation/Prelude",
        "filename": "prelude_to_foundation.epub",
    }
    rendered = _render_template_compat(categories, template, context_with_suffix)
    assert rendered == "Media/Books/Digital/Author One/Foundation/Prelude/prelude_to_foundation.epub"
    
    # Test without suffix
    context_no_suffix = {
        "author": "Author Two",
        "filename": "rendezvous_with_rama.epub",
    }
    rendered_no_suffix = _render_template_compat(categories, template, context_no_suffix)
    # No suffix means that segment is empty and gets filtered out
    assert rendered_no_suffix == "Media/Books/Digital/Author Two/rendezvous_with_rama.epub"


def test_template_sanitization():
    """Test that template rendering sanitizes invalid filesystem characters."""
    # Create test categories
    data = {
        "Media": {
            "Videos": {
                "_template": "Media/Videos/{title}/{filename}"
            }
        }
    }
    categories = Categories.from_source(data)
    
    template = "Media/Videos/{title}/{filename}"
    
    # Test with characters that need sanitization
    context = {
        "title": "Example: Title/With*Invalid?Chars",
        "filename": "video<test>.mp4",
    }
    rendered = _render_template_compat(categories, template, context)
    # Invalid chars should be replaced with underscores
    # Note: Colons, slashes, and special chars all become underscores
    assert "Example" in rendered
    assert "Title" in rendered
    # Slashes in title become underscores and create separate segments
    # So "Example: Title/With*Invalid?Chars" becomes multiple segments
    assert "video_test_.mp4" in rendered


def test_template_handles_empty_segments():
    """Test that empty template segments are filtered out."""
    # Create simple test categories
    data = {
        "Media": {}
    }
    categories = Categories.from_source(data)
    
    template = "{category}/{subcategory}/{collection}/{filename}"
    
    # Context with missing values
    context = {
        "category": "Media",
        "subcategory": "",  # Empty
        "collection": None,  # None
        "filename": "file.txt",
    }
    rendered = _render_template_compat(categories, template, context)
    # Empty segments should be filtered out
    assert rendered == "Media/file.txt"


def test_template_extension_handling():
    """Test that templates correctly handle file extensions."""
    # Create test categories with Music template (no category prefix in template)
    data = {
        "Media": {
            "Music": {
                "_template": "{artist}/{title}"
            }
        }
    }
    categories = Categories.from_source(data)
    
    # Template that uses title (filename reference)
    template = "{artist}/{title}"
    
    # Test with title metadata - title is a filename reference so extension added
    context_with_title = {
        "category_path": "Media/Music",
        "artist": "Queen",
        "title": "Bohemian Rhapsody",
        "filename": "01-bohemian_rhapsody.mp3",
    }
    rendered = _render_template_compat(categories, template, context_with_title)
    # Template uses {title}, so it should add extension back
    assert rendered == "Media/Music/Queen/Bohemian Rhapsody.mp3"
    
    # Test without title - should fall back to filename
    context_no_title = {
        "category_path": "Media/Music",
        "artist": "Pink Floyd",
        "filename": "track05.flac",
    }
    rendered_no_title = _render_template_compat(categories, template, context_no_title)
    # No title, so template renders {artist}/ only, then filename is appended
    assert rendered_no_title == "Media/Music/Pink Floyd/track05.flac"
