import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent

# Ensure `import app...` works by adding `file-sorter` root to sys.path
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    tests_dir = ROOT / "tests" / "unit"
    suite = unittest.defaultTestLoader.discover(str(tests_dir))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
