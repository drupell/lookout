"""Eval harness — runs the full eval suite and reports results.

Usage:
    python -m evals.harness
    # or
    make eval
"""

from __future__ import annotations

import subprocess
import sys


def run_eval_suite() -> int:
    """Run the full eval suite via pytest and report results."""
    # Internal eval harness: all args are literal + sys.executable. No
    # untrusted input on the command line, so S603 is a false positive here.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "evals/",
            "-v",
            "--tb=short",
            "-x",  # stop on first failure for faster feedback
        ],
        capture_output=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(run_eval_suite())
