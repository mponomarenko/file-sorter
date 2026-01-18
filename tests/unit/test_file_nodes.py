import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from app.categories import CategoryPath
from app.file_metadata import FileMetadata
from app.file_nodes import FileNodeBuilder
from app.folder_action import FolderAction


def test_folder_actions_keep_wrapper_directories():
    builder = FileNodeBuilder(
        sources=["/sources"],
        folder_action_map={"/sources/src1/Dropbox": FolderAction.KEEP},
        source_wrapper_pattern="src\\d+",
    )

    node = builder.build(
        "/sources/src1/Dropbox/project/file.txt",
        category=CategoryPath("Documents"),
        mime="text/plain",
        metadata=FileMetadata(),
        rule_match=None,
    )

    assert "/sources/src1/Dropbox" in node.folder_actions
    assert node.folder_actions["/sources/src1/Dropbox"] == FolderAction.KEEP
