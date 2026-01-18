import json
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
TOP = THIS_DIR.parent.parent
if str(TOP) not in sys.path:
    sys.path.insert(0, str(TOP))

from cli.analyze_full import write_output_json


def test_write_output_json(tmp_path):
    payload = {"path": "/tmp/file", "result": {"category": "Documents/Other"}}
    destination = tmp_path / "out" / "result.json"
    write_output_json(payload, str(destination))
    assert destination.is_file()
    data = json.loads(destination.read_text())
    assert data["path"] == "/tmp/file"
    assert data["result"]["category"] == "Documents/Other"
