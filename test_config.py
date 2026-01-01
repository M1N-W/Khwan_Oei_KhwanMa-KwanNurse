#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script for KwanNurse-Bot
ใช้สำหรับทดสอบว่า configuration ถูกต้องหรือไม่
"""

import os
import json
import requests
from datetime import datetime

def test_environment_variables():
    """ทดสอบว่า Environment Variables ถูกตั้งค่าครบหรือไม่"""
    print("=" * 60)
    print("🔍 ตรวจสอบ Environment Variables")
    print("=" * 60)
    
    required_vars = {
        "CHANNEL_ACCESS_TOKEN": "LINE Channel Access Token",
        "NURSE_GROUP_ID": "LINE Group ID สำหรับแจ้งเตือน",
        "GSPREAD_CREDENTIALS": "Google Service Account Credentials"
    }
    
    all_set = True
    for var, description in required_vars.items():
        value = os.environ.get(var)
        if value:
            if var == "GSPREAD_CREDENTIALS":
                try:
                    json.loads(value)
                    print(f"✅ {var}: ตั้งค่าแล้ว (valid JSON)")
                except:
                    print(f"⚠️  {var}: ตั้งค่าแล้วแต่ JSON ไม่ valid")
                    all_set = False
            else:
                masked = value[:10] + "..." if len(value) > 10 else value
                print(f"✅ {var}: ตั้งค่าแล้ว ({masked})")
        else:
            print(f"❌ {var}: ยังไม่ได้ตั้งค่า - {description}")
            all_set = False
    
    print()
    return all_set

def test_webhook_endpoint(base_url):
    """ทดสอบว่า webhook endpoint ทำงานหรือไม่"""
    print("=" * 60)
    print("🌐 ทดสอบ Webhook Endpoint")
    print("=" * 60)
    
    # Test health check
    try:
        print(f"กำลังทดสอบ: {base_url}/")
        response = requests.get(f"{base_url}/", timeout=10)
        if response.status_code == 200:
            print(f"✅ Health check: OK ({response.json()})")
        else:
            print(f"⚠️  Health check: ได้รับ status code {response.status_code}")
    except Exception as e:
        print(f"❌ Health check: Error - {e}")
    
    # Test webhook with mock request
    try:
        print(f"\nกำลังทดสอบ: {base_url}/webhook")
        test_payload = {
            "queryResult": {
                "intent": {
                    "displayName": "AssessRisk"
                },
                "parameters": {
                    "age": 65,
                    "weight": 98,
                    "height": 165,
                    "diseases": ["diabetes"]
                }
            },
            "session": "test-session-123"
        }
        
        response = requests.post(
            f"{base_url}/webhook",
            json=test_payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            print(f"✅ Webhook: OK")
            print(f"📝 Response: {result.get('fulfillmentText', '')[:100]}...")
        else:
            print(f"⚠️  Webhook: ได้รับ status code {response.status_code}")
            print(f"📝 Response: {response.text}")
    except Exception as e:
        print(f"❌ Webhook: Error - {e}")
    
    print()

def test_dialogflow_intent_simulation():
    """จำลองการทดสอบ Dialogflow Intent"""
    print("=" * 60)
    print("🤖 คำแนะนำในการทดสอบ Dialogflow")
    print("=" * 60)
    
    test_cases = [
        {
            "name": "AssessRisk - High Risk Case",
            "input": "ประเมินความเสี่ยง อายุ 65 น้ำหนัก 98 ส่วนสูง 165 เป็นเบาหวาน",
            "expected": "ระดับ: สูง (High Risk)"
        },
        {
            "name": "AssessRisk - Low Risk Case",
            "input": "ประเมินความเสี่ยง อายุ 30 น้ำหนัก 60 ส่วนสูง 170 ไม่มีโรค",
            "expected": "ระดับ: ต่ำ (Low Risk)"
        },
        {
            "name": "ReportSymptoms - Emergency",
            "input": "รายงานอาการ ปวด 10 มีไข้ แผลมีหนอง เดินได้",
            "expected": "🚨 อันตราย"
        }
    ]
    
    print("\nทดสอบ Intent ต่อไปนี้ใน Dialogflow Console หรือ LINE:\n")
    
    for i, test in enumerate(test_cases, 1):
        print(f"{i}. {test['name']}")
        print(f"   📝 Input: {test['input']}")
        print(f"   ✅ Expected: {test['expected']}")
        print()

def generate_curl_commands(base_url):
    """สร้าง curl commands สำหรับทดสอบ"""
    print("=" * 60)
    print("💻 Curl Commands สำหรับทดสอบ")
    print("=" * 60)
    
    commands = [
        ("Health Check", f'curl {base_url}/'),
        ("Test AssessRisk Intent", f'''curl -X POST {base_url}/webhook \\
  -H "Content-Type: application/json" \\
  -d '{{
    "queryResult": {{
      "intent": {{"displayName": "AssessRisk"}},
      "parameters": {{
        "age": 65,
        "weight": 98,
        "height": 165,
        "diseases": ["diabetes"]
      }}
    }},
    "session": "test-session"
  }}'
''')
    ]
    
    for name, command in commands:
        print(f"\n### {name}")
        print(f"```bash\n{command}\n```")
    
    print()

def main():
    print("\n" + "=" * 60)
    print("🏥 KwanNurse-Bot Configuration Test")
    print("=" * 60 + "\n")
    
    # 1. Test environment variables
    env_ok = test_environment_variables()
    
    # 2. Get base URL
    base_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
    if base_url == "http://localhost:5000":
        print("⚠️  กำลังทำงานใน Local Mode")
        print("   ถ้าต้องการทดสอบ Production ให้ตั้งค่า RENDER_EXTERNAL_URL")
        print()
    
    # 3. Test webhook endpoints
    test_webhook_endpoint(base_url)
    
    # 4. Show Dialogflow test cases
    test_dialogflow_intent_simulation()
    
    # 5. Generate curl commands
    generate_curl_commands(base_url)
    
    # Summary
    print("=" * 60)
    print("📊 สรุป")
    print("=" * 60)
    if env_ok:
        print("✅ Environment Variables: ตั้งค่าครบถ้วน")
    else:
        print("❌ Environment Variables: ยังตั้งค่าไม่ครบ")
        print("   👉 โปรดตั้งค่าใน Render Dashboard > Environment")
    
    print("\n💡 ขั้นตอนต่อไป:")
    print("   1. ตั้งค่า Environment Variables ที่ยังขาดใน Render")
    print("   2. Deploy โค้ดใหม่ไป Render")
    print("   3. ตรวจสอบ Logs ใน Render Dashboard")
    print("   4. ทดสอบส่งข้อความใน LINE")
    print("=" * 60 + "\n")

if __name__ == "__main__":
    main()
