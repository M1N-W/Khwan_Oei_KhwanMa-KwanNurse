# -*- coding: utf-8 -*-
"""
Helper script to initialize all required worksheets in the Google Spreadsheet.
"""
from __future__ import annotations
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config import (
    SPREADSHEET_NAME,
    SHEET_SYMPTOM_LOG,
    SHEET_RISK_PROFILE,
    SHEET_APPOINTMENTS,
    SHEET_FOLLOW_UP_REMINDERS,
    SHEET_REMINDER_SCHEDULES,
    SHEET_TELECONSULT_SESSIONS,
    SHEET_TELECONSULT_QUEUE,
    SHEET_WOUND_ANALYSIS_LOG,
    SHEET_PATIENT_PROFILE,
    SHEET_EDUCATION_LOG,
    SHEET_VOICE_LOG,
    SHEET_FAILED_NURSE_ALERTS,
    SHEET_SURVEY_SCHEDULES,
)
from database.sheets import get_spreadsheet

# Define the expected worksheets and their default headers
REQUIRED_SHEETS = {
    SHEET_SYMPTOM_LOG: ["Timestamp", "User_ID", "Pain", "Wound", "Fever", "Mobility", "Risk_Level", "Risk_Score"],
    SHEET_RISK_PROFILE: ["Timestamp", "User_ID", "Age", "Weight", "Height", "BMI", "Diseases", "Risk_Level", "Risk_Score"],
    SHEET_APPOINTMENTS: ["Timestamp", "User_ID", "Name", "Phone", "Preferred_Date", "Preferred_Time", "Reason", "Status", "Assigned_To", "Notes"],
    SHEET_FOLLOW_UP_REMINDERS: ["Timestamp", "User_ID", "Reminder_Type", "Status", "Response_Text", "Message_Sent", "Response_Timestamp"],
    SHEET_REMINDER_SCHEDULES: ["Created_At", "User_ID", "Discharge_Date", "Reminder_Type", "Scheduled_Date", "Status", "Notes", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error", "Last_Attempt_At"],
    SHEET_TELECONSULT_SESSIONS: ["Session_ID", "Timestamp", "User_ID", "Issue_Type", "Priority", "Status", "Description", "Queue_Position", "Assigned_Nurse", "Started_At", "Completed_At", "Notes"],
    SHEET_TELECONSULT_QUEUE: ["Queue_ID", "Timestamp", "Session_ID", "User_ID", "Issue_Type", "Priority", "Status", "Estimated_Wait"],
    SHEET_WOUND_ANALYSIS_LOG: ["Timestamp", "User_ID", "Severity", "Observations", "Advice", "Confidence", "Image_Size_KB", "Message_ID"],
    SHEET_PATIENT_PROFILE: ["User_ID", "Age", "Sex", "Surgery_Type", "Surgery_Date", "Diseases", "Updated_At", "First_Name", "Last_Name", "HN", "Phone", "Registration_Status", "Registered_At", "Consent_Version", "Consent_At", "Last_Active_At"],
    SHEET_EDUCATION_LOG: ["Timestamp", "User_ID", "Topic", "Source", "Personalized"],
    SHEET_VOICE_LOG: ["Timestamp", "User_ID", "Duration_Sec", "MIME", "Transcription_Length", "Status"],
    SHEET_FAILED_NURSE_ALERTS: ["Created_At", "Idempotency_Key", "Event_Type", "User_ID", "Risk_Level", "Risk_Score", "Payload_JSON", "Notification_Message", "Status", "Retry_Count", "Last_Error", "Last_Attempt_At", "Resolved_At", "Resolved_By"],
    SHEET_SURVEY_SCHEDULES: ["Created_At", "User_ID", "Milestone_Day", "Survey_URL", "Tracking_Token", "Status", "Sent_At", "Clicked_At", "Claimed_By", "Claimed_At", "Retry_Count", "Last_Error"]
}

def main():
    print(f"Connecting to Google Spreadsheet: {SPREADSHEET_NAME}...")
    spreadsheet = get_spreadsheet()
    if not spreadsheet:
        print("Error: Could not open spreadsheet. Please check your credentials and SPREADSHEET_NAME configuration.")
        return 1

    existing_worksheets = {w.title for w in spreadsheet.worksheets()}
    print(f"Found {len(existing_worksheets)} existing worksheets: {', '.join(existing_worksheets)}")

    created_count = 0
    for title, headers in REQUIRED_SHEETS.items():
        if title not in existing_worksheets:
            print(f"Creating missing worksheet '{title}'...")
            try:
                sheet = spreadsheet.add_worksheet(title=title, rows=1000, cols=max(20, len(headers)))
                sheet.append_row(headers, value_input_option="USER_ENTERED")
                print(f"Successfully created '{title}' with headers.")
                created_count += 1
            except Exception as e:
                print(f"Error creating worksheet '{title}': {e}")
        else:
            print(f"Worksheet '{title}' already exists. Skipping.")

    print(f"\nInitialization finished. Created {created_count} new worksheet(s).")
    return 0

if __name__ == "__main__":
    sys.exit(main())
