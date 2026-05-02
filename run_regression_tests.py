# -*- coding: utf-8 -*-
"""
Run the current regression suites with scheduler side effects disabled.
"""
import os
import subprocess
import sys


def main():
    """
    Run every ``tests/test_*.py`` via ``unittest discover``. New test files
    dropped into ``tests/`` are picked up automatically — no edit to this
    file needed.
    """
    env = os.environ.copy()
    env.setdefault("RUN_SCHEDULER", "false")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    command = [
        sys.executable, "-m", "unittest", "discover",
        "-s", "tests", "-p", "test_*.py", "-t", ".", "-v",
    ]
    print(f">>> Running: {' '.join(command)}")
    result = subprocess.run(command, env=env)
    if result.returncode != 0:
        return result.returncode

    print("\nAll regression suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
