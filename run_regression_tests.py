# -*- coding: utf-8 -*-
"""
Run the current regression suites with scheduler side effects disabled.
"""
import os
import subprocess
import sys


TEST_COMMANDS = [
    [sys.executable, "-m", "unittest", "test_teleconsult.py", "-v"],
    [sys.executable, "-m", "unittest", "test_reminder.py", "-v"],
    [sys.executable, "-m", "unittest", "test_llm.py", "-v"],
    [sys.executable, "-m", "unittest", "test_symptom_risk.py", "-v"],
    [sys.executable, "-m", "unittest", "test_presession.py", "-v"],
    [sys.executable, "-m", "unittest", "test_early_warning.py", "-v"],
    [sys.executable, "-m", "unittest", "test_integration_e2e.py", "-v"],
    [sys.executable, "-m", "unittest", "test_metrics.py", "-v"],
    [sys.executable, "-m", "unittest", "test_cache.py", "-v"],
    [sys.executable, "-m", "unittest", "test_dashboard_readers.py", "-v"],
    [sys.executable, "-m", "unittest", "test_dashboard_actions.py", "-v"],
    [sys.executable, "-m", "unittest", "test_dashboard_auth.py", "-v"],
    [sys.executable, "-m", "unittest", "test_dashboard_polish.py", "-v"],
    [sys.executable, "-m", "unittest", "test_preconsult_summary.py", "-v"],
    [sys.executable, "-m", "unittest", "test_wound_analysis.py", "-v"],
    [sys.executable, "-m", "unittest", "test_personalized_education.py", "-v"],
    [sys.executable, "-m", "unittest", "test_bugfix_office_and_knowledge.py", "-v"],
    [sys.executable, "-m", "unittest", "test_hotfix_logsec_and_choice.py", "-v"],
    [sys.executable, "-m", "unittest", "test_quickwins_d3.py", "-v"],
    [sys.executable, "-m", "unittest", "test_phase5_voice_stt.py", "-v"],
    [sys.executable, "-m", "unittest", "test_phase4_resilience.py", "-v"],
    [sys.executable, "-m", "unittest", "test_phase4_observability.py", "-v"],
    [sys.executable, "-m", "unittest", "test_phase4_security.py", "-v"],
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
