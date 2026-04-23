# -*- coding: utf-8 -*-
"""
Run the current regression suites with scheduler side effects disabled.
"""
import os
import subprocess
import sys


TEST_COMMANDS = [
    [sys.executable, "test_bug_fixes.py"],
    [sys.executable, "-m", "unittest", "test_teleconsult.py", "-v"],
    [sys.executable, "-m", "unittest", "test_reminder.py", "-v"],
    [sys.executable, "-m", "unittest", "test_llm.py", "-v"],
    [sys.executable, "-m", "unittest", "test_symptom_risk.py", "-v"],
    [sys.executable, "-m", "unittest", "test_presession.py", "-v"],
]


def main():
    env = os.environ.copy()
    env.setdefault("RUN_SCHEDULER", "false")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    for command in TEST_COMMANDS:
        print(f"\n>>> Running: {' '.join(command)}")
        result = subprocess.run(command, env=env)
        if result.returncode != 0:
            return result.returncode

    print("\nAll regression suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
