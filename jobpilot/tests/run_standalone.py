"""Run the test suite without pytest (air-gapped / bare interpreter).

Provides minimal stand-ins for the ``tmp_path``, ``tmp_config`` and
``coordinator`` fixtures, then executes every ``test_*`` function across the
test modules.  ``python -m jobpilot.tests.run_standalone`` from the repo root.

When pytest *is* installed, prefer ``pytest jobpilot/tests``.
"""

from __future__ import annotations

import importlib
import inspect
import os
import shutil
import tempfile
import traceback
from pathlib import Path

from jobpilot.config import Config
from jobpilot.coordinator import Coordinator

MODULES = [
    "test_constants", "test_database", "test_analysis", "test_restart",
    "test_prediction", "test_chatbot", "test_license", "test_dashboard",
    "test_discovery",
]


class _Cleanup:
    def __init__(self):
        self.dirs = []
        self.coordinators = []

    def tmp_path(self):
        d = Path(tempfile.mkdtemp(prefix="jptest_"))
        self.dirs.append(d)
        return d

    def tmp_config(self):
        d = self.tmp_path()
        os.environ["JOBPILOT_DATA"] = str(d / "jpdata")
        return Config(config_path=d / "config.ini")

    def coordinator(self):
        cfg = self.tmp_config()
        co = Coordinator(cfg)
        self.coordinators.append(co)
        return co

    def finish(self):
        for co in self.coordinators:
            try:
                co.stop()
            except Exception:
                pass
        self.coordinators.clear()
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)
        self.dirs.clear()


def _supply(param, cl):
    return getattr(cl, param)()


def main():
    passed = failed = 0
    failures = []
    for modname in MODULES:
        mod = importlib.import_module(f"jobpilot.tests.{modname}")
        for name, fn in sorted(vars(mod).items()):
            if not (name.startswith("test_") and inspect.isfunction(fn)):
                continue
            cl = _Cleanup()
            try:
                params = list(inspect.signature(fn).parameters)
                args = [_supply(p, cl) for p in params]
                fn(*args)
                passed += 1
                print(f"  PASS {modname}.{name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                failures.append((f"{modname}.{name}", traceback.format_exc()))
                print(f"  FAIL {modname}.{name}: {e}")
            finally:
                cl.finish()
    print(f"\n{passed} passed, {failed} failed")
    if failures:
        print("\n── Failures ──")
        for name, tb in failures:
            print(f"\n{name}\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
