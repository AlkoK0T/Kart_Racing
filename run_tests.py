"""Single entrypoint for unit (including JS/root TCP contract) and Behave tests."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent


def unit_suite():
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / 'tests'))
    suite.addTests(loader.loadTestsFromName('test_tcp_race_contract'))
    return suite


def run_unit():
    return unittest.TextTestRunner(verbosity=2).run(unit_suite()).wasSuccessful()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit-only', action='store_true')
    args = parser.parse_args(argv)
    if args.unit_only:
        return 0 if run_unit() else 1
    environment = dict(os.environ, LOG_FILE='', KART_DEBUG='False', PYTHONUTF8='1',
                       PYGAME_HIDE_SUPPORT_PROMPT='1')
    commands = ([sys.executable, str(ROOT / 'run_tests.py'), '--unit-only'],
                [sys.executable, '-m', 'behave', '--format', 'progress'])
    success = True
    for command in commands:
        print('Running: ' + ' '.join(command), flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, env=environment, timeout=180)
            success = result.returncode == 0 and success
        except (OSError, subprocess.TimeoutExpired) as exc:
            print('Test command failed: ' + str(exc), file=sys.stderr)
            success = False
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
