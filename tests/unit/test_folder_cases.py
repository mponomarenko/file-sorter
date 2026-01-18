import sys
from pathlib import Path, PurePosixPath

import pytest

# Ensure we can import app and test utilities
THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent.parent
TESTS_DIR = THIS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from utils.tree_expectations import FileCase, FolderCase, FolderCaseRunner


def _expected_path(root: str, *parts: str) -> str:
    posix_parts = [part.strip("/") for part in parts if part]
    return str(PurePosixPath(root, *posix_parts))


CASES: list[FolderCase] = [
    FolderCase(
        name="backup_photo_album_kept",
        strip_dirs=["Backups"],
        sources=["/src"],
        files=[
            FileCase(
                path="Backups/July/Vacation Trip/photo1.jpg",
                mime="image/jpeg",
                category="Media/Photos",
                expected=_expected_path("/target", "Media", "Photos", "July", "Vacation Trip", "photo1.jpg"),
                folder_actions={"Backups/July/Vacation Trip": "keep"},
            ),
            FileCase(
                path="Backups/July/random.jpg",
                mime="image/jpeg",
                category="Media/Photos",
                expected=_expected_path("/target", "Media", "Photos", "July", "random.jpg"),
                metadata={"year": "July"},
            ),
        ],
    ),
    FolderCase(
        name="boat_documents_finance_keep",
        sources=["/data"],
        files=[
            FileCase(
                path="Boat Documents/Boat Insurance/Application.pdf",
                mime="application/pdf",
                category="Documents/Finance",
                expected=_expected_path(
                    "/target", "Documents", "Finance", "Boat Documents", "Boat Insurance", "Application.pdf"
                ),
                folder_actions={"Boat Documents": "keep"},
            ),
            FileCase(
                path="Boat Documents/Marina/Agreement.pdf",
                mime="application/pdf",
                category="Documents/Finance",
                expected=_expected_path(
                    "/target", "Documents", "Finance", "Boat Documents", "Marina", "Agreement.pdf"
                ),
                folder_actions={"Boat Documents": "keep"},
            ),
        ],
    ),
    FolderCase(
        name="three_d_print_projects_should_keep",
        sources=["/projects"],
        files=[
            FileCase(
                path="3D Prints/Leo/Leo_Base.stl",
                mime="application/octet-stream",
                category="Software/Source_Code",
                expected=_expected_path(
                    "/target", "Software", "Source_Code", "3D Prints", "Leo", "Leo_Base.stl"
                ),
                folder_actions={"3D Prints": "keep"},
            ),
            FileCase(
                path="3D Prints/hex/Insert-countersunk.stl",
                mime="application/octet-stream",
                category="Software/Source_Code",
                expected=_expected_path(
                    "/target", "Software", "Source_Code", "3D Prints", "hex", "Insert-countersunk.stl"
                ),
                folder_actions={"3D Prints": "keep"},
            ),
        ],
    ),
    FolderCase(
        name="documents_invoices_go_to_finance",
        sources=["/docs"],
        files=[
            FileCase(
                path="Invoices/2023/invoice.pdf",
                mime="application/pdf",
                category="Documents/Finance",
                expected=_expected_path(
                    "/target", "Documents", "Finance", "invoice.pdf"
                ),
            ),
            FileCase(
                path="Invoices/2022/Invoice.pdf",
                mime="application/pdf",
                category="Documents/Finance",
                expected=_expected_path(
                    "/target", "Documents", "Finance", "Invoice.pdf"
                ),
            ),
            FileCase(
                path="Receipts/2024/receipt.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                category="Documents/Finance",
                expected=_expected_path(
                    "/target", "Documents", "Finance", "receipt.docx"
                ),
            ),
        ],
        # Organizational folders should be stripped
        # TODO: this is expectation or mock? rename to be explicit
        folder_actions={
            "Invoices": "disaggregate",
            "Invoices/2023": "disaggregate",
            "Invoices/2022": "disaggregate",
            "Receipts": "disaggregate",
            "Receipts/2024": "disaggregate",
        },
    ),
    FolderCase(
        name="tax_folder_disaggregates_year",
        strip_dirs=["Backup"],
        sources=["/src"],
        files=[
            FileCase(
                path="Backup/Docs/Taxes 2025/w2.pdf",
                mime="application/pdf",
                category="Documents/Taxes/2025",
                expected=_expected_path("/target", "Documents", "Taxes", "2025", "w2.pdf"),
                # Disaggregate Docs and "Taxes 2025" folders
                folder_actions={
                    "Backup/Docs": "disaggregate",
                    "Backup/Docs/Taxes 2025": "disaggregate",
                },
            ),
            FileCase(
                path="Backup/Docs/Firm X Tax Review/w2.pdf",
                mime="application/pdf",
                category="Documents/Taxes/2025",
                # TODO: this expecation should just take one string for
                # whole path, not reconstructing from parts
                expected=_expected_path(
                    "/target", "Documents", "Taxes", "2025", "Firm X Tax Review", "w2.pdf"
                ),
                folder_actions={
                    "Backup/Docs": "disaggregate",
                    "Backup/Docs/Firm X Tax Review": "keep",
                },
            ),
        ],
    ),
    FolderCase(
        name="downloads_disaggregated",
        strip_dirs=["Downloads"],
        sources=["/fixtures/paths/home/user"],
        files=[
            FileCase(
                path="Downloads/mixed/song.mp3",
                mime="audio/mpeg",
                category="Media/Music",
                expected=_expected_path(
                    "/target", "Media", "Music", "mixed", "song.mp3"
                ),
            ),
            FileCase(
                path="Downloads/Projects/app/main.py",
                mime="text/x-python",
                category="Software/Source_Code",
                expected=_expected_path(
                    "/target", "Software", "Source_Code", "app", "main.py"
                ),
            ),
        ],
    ),
    FolderCase(
        name="portable_app_kept_subtree",
        sources=["/apps"],
        files=[
            FileCase(
                path="portable_suite/chat/bin/chat.exe",
                mime="application/octet-stream",
                category="Software/Applications",
                expected=_expected_path(
                    "/target", "Software", "Applications", "portable_suite", "chat", "bin", "chat.exe"
                ),
                folder_actions={"portable_suite": "keep"},
            ),
            FileCase(
                path="portable_suite/chat/readme.txt",
                mime="text/plain",
                category="Software/Applications",
                expected=_expected_path(
                    "/target", "Software", "Applications", "portable_suite", "chat", "readme.txt"
                ),
                folder_actions={"portable_suite": "keep"},
            ),
        ],
    ),
    FolderCase(
        name="iso_like_installer_keep",
        strip_dirs=["Downloads"],
        sources=["/fixtures/paths/home/user"],
        files=[
            FileCase(
                path="Downloads/InstallerDisc/autorun.inf",
                mime="text/plain",
                category="Software/Installers",
                expected=_expected_path(
                    "/target", "Software", "Installers", "InstallerDisc", "autorun.inf"
                ),
                folder_actions={"Downloads/InstallerDisc": "keep"},
            ),
            FileCase(
                path="Downloads/InstallerDisc/setup.exe",
                mime="application/octet-stream",
                category="Software/Installers",
                expected=_expected_path(
                    "/target", "Software", "Installers", "InstallerDisc", "setup.exe"
                ),
                folder_actions={"Downloads/InstallerDisc": "keep"},
            ),
        ],
    ),
    FolderCase(
        name="project_with_dependency_folder",
        sources=["/projects"],
        files=[
            FileCase(
                path="sc2planner/scenario.htp",
                mime="application/octet-stream",
                category="Software/Projects",
                expected=_expected_path(
                    "/target", "Software", "Projects", "sc2planner", "scenario.htp"
                ),
                folder_actions={"sc2planner": "keep"},
            ),
            FileCase(
                path="sc2planner/assets/UI.dat",
                mime="application/octet-stream",
                category="Software/Projects",
                expected=_expected_path(
                    "/target", "Software", "Projects", "sc2planner", "assets", "UI.dat"
                ),
                folder_actions={"sc2planner": "keep"},
            ),
        ],
    ),
    FolderCase(
        name="resumes_folder_should_keep",
        sources=["/docs"],
        files=[
            FileCase(
                path="Resumes/alex.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                category="Documents/Resumes",
                expected=_expected_path(
                    "/target", "Documents", "Resumes", "Resumes", "alex.docx"
                ),
                folder_actions={"Resumes": "keep"},
            ),
            FileCase(
                path="Resumes/jordan.pdf",
                mime="application/pdf",
                category="Documents/Resumes",
                expected=_expected_path(
                    "/target", "Documents", "Resumes", "Resumes", "jordan.pdf"
                ),
                folder_actions={"Resumes": "keep"},
            ),
        ],
    ),
]


def _case_param(case: FolderCase):
    if case.xfail_reason:
        return pytest.param(case, id=case.name, marks=pytest.mark.xfail(reason=case.xfail_reason, strict=False))
    return pytest.param(case, id=case.name)


@pytest.mark.parametrize("case", [_case_param(case) for case in CASES])
def test_folder_cases(case: FolderCase):
    runner = FolderCaseRunner(case)
    runner.run()
