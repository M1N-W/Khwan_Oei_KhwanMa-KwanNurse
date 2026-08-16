"""Backfill PatientProfile and SurveySchedules from Sheets into Render PostgreSQL.

Run with ``DATABASE_URL`` set. The default is a dry run; pass ``--apply`` only
after row counts and invalid-user report are reviewed.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from config import LOCAL_TZ, SHEET_PATIENT_PROFILE, SHEET_SURVEY_SCHEDULES
from database.patient_profile import _row_to_dict
from database.sheets import get_worksheet


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=LOCAL_TZ)


def _rows(sheet_name: str) -> tuple[list[str], list[list[str]]]:
    sheet = get_worksheet(sheet_name)
    if not sheet:
        raise RuntimeError(f"worksheet unavailable: {sheet_name}")
    values = sheet.get_all_values()
    return (values[0] if values else []), (values[1:] if len(values) > 1 else [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", default="postgres_backfill_report.json")
    args = parser.parse_args()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")

    profile_headers, profile_rows = _rows(SHEET_PATIENT_PROFILE)
    survey_headers, survey_rows = _rows(SHEET_SURVEY_SCHEDULES)
    profiles, invalid_user_ids = [], []
    for row in profile_rows:
        profile = _row_to_dict(profile_headers, row)
        user_id = str(profile.get("user_id") or "").strip()
        if not user_id:
            invalid_user_ids.append(len(invalid_user_ids) + 2)
            continue
        profiles.append(profile)

    report = {
        "profile_rows": len(profile_rows), "profiles_valid": len(profiles),
        "survey_rows": len(survey_rows), "invalid_profile_rows": invalid_user_ids,
        "applied": bool(args.apply),
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not args.apply:
        print(json.dumps(report, ensure_ascii=False))
        return 0

    import psycopg
    schema = Path(__file__).resolve().parents[1].joinpath("database", "postgres_schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(schema)
        for profile in profiles:
            cursor.execute(
                """INSERT INTO patient_profiles (user_id, profile, registration_status)
                   VALUES (%s, %s::jsonb, %s)
                   ON CONFLICT (user_id) DO UPDATE SET profile=EXCLUDED.profile,
                       registration_status=EXCLUDED.registration_status, updated_at=now()""",
                (profile["user_id"], json.dumps(profile), profile.get("registration_status", "incomplete")),
            )
        indexes = {name: index for index, name in enumerate(survey_headers)}
        required_survey_columns = {"User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Scheduled_Date"}
        if not required_survey_columns.issubset(indexes):
            missing = sorted(required_survey_columns - set(indexes))
            raise RuntimeError(f"SurveySchedules is missing required columns: {', '.join(missing)}")
        for row in survey_rows:
            def value(column: str) -> str:
                index = indexes[column]
                return row[index].strip() if len(row) > index else ""
            user_id = value("User_ID")
            token = value("Tracking_Token")
            scheduled = value("Scheduled_Date")
            if not user_id or not token or not scheduled:
                continue
            cursor.execute(
                """INSERT INTO patient_profiles (user_id, profile, registration_status)
                   VALUES (%s, %s::jsonb, 'incomplete') ON CONFLICT (user_id) DO NOTHING""",
                (user_id, json.dumps({"user_id": user_id, "registration_status": "incomplete"})),
            )
            cursor.execute(
                """INSERT INTO survey_schedules (user_id, milestone_day, survey_url, tracking_token, status, scheduled_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (user_id, milestone_day) DO NOTHING""",
                (user_id, int(value("Milestone_Day")), value("Survey_URL"), token,
                 value("Status") or "scheduled", _parse_datetime(scheduled)),
            )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
