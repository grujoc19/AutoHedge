"""The public package import must not load the model or signing runtime."""

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackageImportTests(unittest.TestCase):
    def test_package_import_is_lazy_without_dependencies(self):
        code = """
import sys
import autohedge
assert 'swarms' not in sys.modules
assert 'autohedge.main' not in sys.modules
assert 'autohedge.workers' not in sys.modules
assert 'solders' not in sys.modules
try:
    autohedge.missing_attribute
except AttributeError:
    pass
else:
    raise AssertionError('Unknown attributes must raise AttributeError')
"""
        result = subprocess.run(
            [sys.executable, "-S", "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
