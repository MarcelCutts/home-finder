"""mutmut configuration file.

NOTE: mutmut 3.x does NOT auto-load this file (that was a 2.x feature).
This file is kept for documentation purposes only.

The actual workarounds are implemented in:

1. setproctitle SIGSEGV fix: .venv site-packages _mutmut_setproctitle_fix.pth
   - Patches setproctitle at Python startup (before mutmut imports it)
   - Required on macOS + Python 3.14 where os.fork() + setproctitle() = SIGSEGV
   - See: https://github.com/boxed/mutmut/issues/457

2. structlog closed-stdout fix: handled via --capture=sys in mutmut pytest args

3. Hypothesis differing_executors: handled via --hypothesis-profile=mutmut
   in pyproject.toml, loading the 'mutmut' profile from tests/conftest.py
"""
