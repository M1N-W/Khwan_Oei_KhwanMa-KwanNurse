# -*- coding: utf-8 -*-
"""
Bug Fix Verification Test Suite
Test all 4 bugs have been fixed correctly
"""
import sys
import json
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock

# Add project to path
sys.path.insert(0, '/mnt/user-data/outputs')

from config import LOCAL_TZ, get_logger
from services.reminder import send_reminder, get_reminder_summary
from services.notification import send_line_push

logger = get_logger(__name__)


class Colors:
    """ANSI color codes"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_test_header(test_num, test_name):
    """Print formatted test header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}")
    print(f"TEST {test_num}: {test_name}")
    print(f"{'='*70}{Colors.RESET}\n")


def print_result(passed, message):
    """Print test result"""
    if passed:
        print(f"{Colors.GREEN}✅ PASS{Colors.RESET}: {message}")
    else:
        print(f"{Colors.RED}❌ FAIL{Colors.RESET}: {message}")
    return passed


def test_bug_1_parameter_order():
    """
    BUG #1: Test send_line_push parameter order is correct
    
    The bug was: send_line_push(user_id, message) 
    Should be: send_line_push(message, user_id)
    """
    print_test_header(1, "Parameter Order in send_line_push()")
    
    test_passed = True
    
    # Test 1.1: Mock send_line_push and verify call signature
    print("1.1 Testing send_reminder() calls send_line_push() with correct order...")
    
    with patch('services.reminder.send_line_push') as mock_send:
        with patch('services.reminder.save_reminder_sent'):
            # Configure mock to return True
            mock_send.return_value = True
            
            # Call send_reminder
            result = send_reminder('test_user_123', 'day3')
            
            # Verify it was called
            if not mock_send.called:
                test_passed = print_result(False, "send_line_push was not called")
            else:
                # Check call signature
                call_args = mock_send.call_args[0]
                
                # First argument should be the message (string)
                first_arg_is_message = isinstance(call_args[0], str) and len(call_args[0]) > 50
                test_passed = print_result(
                    first_arg_is_message,
                    f"First argument is message: {first_arg_is_message}"
                )
                
                # Second argument should be user_id
                second_arg_is_user = call_args[1] == 'test_user_123'
                test_passed = print_result(
                    second_arg_is_user,
                    f"Second argument is user_id: {second_arg_is_user}"
                ) and test_passed
    
    # Test 1.2: Verify send_line_push function signature
    print("\n1.2 Verifying send_line_push() function signature...")
    
    import inspect
    sig = inspect.signature(send_line_push)
    params = list(sig.parameters.keys())
    
    first_param_is_message = params[0] == 'message'
    test_passed = print_result(
        first_param_is_message,
        f"First parameter is 'message': {first_param_is_message}"
    ) and test_passed
    
    second_param_is_target = params[1] == 'target_id'
    test_passed = print_result(
        second_param_is_target,
        f"Second parameter is 'target_id': {second_param_is_target}"
    ) and test_passed
    
    return test_passed


def test_bug_2_return_values():
    """
    BUG #2: Test get_reminder_summary returns correct field names
    
    Expected fields:
    - total_reminders (not total_scheduled)
    - responded (new)
    - pending (not pending_response)
    - no_response (new)
    - latest (new)
    """
    print_test_header(2, "Return Values in get_reminder_summary()")
    
    test_passed = True
    
    # Test 2.1: Call function and check return structure
    print("2.1 Testing get_reminder_summary() return structure...")
    
    with patch('services.reminder.get_scheduled_reminders') as mock_scheduled:
        with patch('services.reminder.get_pending_reminders') as mock_pending:
            # Mock empty data
            mock_scheduled.return_value = []
            mock_pending.return_value = []
            
            # Call function
            result = get_reminder_summary('test_user')
            
            # Check required fields exist
            required_fields = [
                'user_id',
                'total_reminders',
                'responded', 
                'pending',
                'no_response',
                'latest'
            ]
            
            for field in required_fields:
                has_field = field in result
                test_passed = print_result(
                    has_field,
                    f"Has field '{field}': {has_field}"
                ) and test_passed
            
            # Check old fields don't exist
            old_fields = ['total_scheduled', 'pending_response']
            for field in old_fields:
                no_old_field = field not in result
                test_passed = print_result(
                    no_old_field,
                    f"Old field '{field}' removed: {no_old_field}"
                ) and test_passed
    
    # Test 2.2: Test with mock data
    print("\n2.2 Testing with mock reminder data...")
    
    with patch('services.reminder.get_scheduled_reminders') as mock_scheduled:
        with patch('services.reminder.get_pending_reminders') as mock_pending:
            # Mock data with different statuses
            mock_scheduled.return_value = [
                {'User_ID': 'test_user', 'Status': 'responded', 'Created_At': '2026-01-01'},
                {'User_ID': 'test_user', 'Status': 'sent', 'Created_At': '2026-01-05'},
                {'User_ID': 'test_user', 'Status': 'no_response', 'Created_At': '2026-01-03'},
                {'User_ID': 'other_user', 'Status': 'sent', 'Created_At': '2026-01-04'},
            ]
            mock_pending.return_value = []
            
            result = get_reminder_summary('test_user')
            
            # Verify counts
            total_correct = result['total_reminders'] == 3
            test_passed = print_result(
                total_correct,
                f"Total reminders = 3: {total_correct} (got {result['total_reminders']})"
            ) and test_passed
            
            responded_correct = result['responded'] == 1
            test_passed = print_result(
                responded_correct,
                f"Responded = 1: {responded_correct} (got {result['responded']})"
            ) and test_passed
            
            pending_correct = result['pending'] == 1
            test_passed = print_result(
                pending_correct,
                f"Pending = 1: {pending_correct} (got {result['pending']})"
            ) and test_passed
            
            no_response_correct = result['no_response'] == 1
            test_passed = print_result(
                no_response_correct,
                f"No response = 1: {no_response_correct} (got {result['no_response']})"
            ) and test_passed
    
    return test_passed


def test_bug_3_missing_handler():
    """
    BUG #3: Test GetFollowUpSummary intent handler exists
    """
    print_test_header(3, "GetFollowUpSummary Intent Handler")
    
    test_passed = True
    
    # Test 3.1: Check handler function exists
    print("3.1 Checking handler function exists...")
    
    try:
        from routes.webhook import register_routes
        import inspect
        
        # Get source code of register_routes
        source = inspect.getsource(register_routes)
        
        has_intent_route = 'GetFollowUpSummary' in source
        test_passed = print_result(
            has_intent_route,
            f"Intent route registered: {has_intent_route}"
        )
        
        has_handler_function = 'handle_get_followup_summary' in source
        test_passed = print_result(
            has_handler_function,
            f"Handler function defined: {has_handler_function}"
        ) and test_passed
        
    except Exception as e:
        test_passed = print_result(False, f"Error checking handler: {e}")
    
    # Test 3.2: Test handler with mock request
    print("\n3.2 Testing handler with mock webhook request...")
    
    try:
        from flask import Flask
        from routes.webhook import register_routes
        
        app = Flask(__name__)
        register_routes(app)
        
        with app.test_client() as client:
            # Create mock Dialogflow request
            request_data = {
                "queryResult": {
                    "intent": {"displayName": "GetFollowUpSummary"},
                    "parameters": {},
                    "queryText": "ติดตามหลังจำหน่าย"
                },
                "session": "test-session/test-user-123"
            }
            
            # Mock database functions
            with patch('services.reminder.get_reminder_summary') as mock_summary:
                mock_summary.return_value = {
                    'total_reminders': 2,
                    'responded': 1,
                    'pending': 1,
                    'no_response': 0,
                    'latest': None
                }
                
                # Send request
                response = client.post(
                    '/webhook',
                    data=json.dumps(request_data),
                    content_type='application/json'
                )
                
                # Check response
                is_200 = response.status_code == 200
                test_passed = print_result(
                    is_200,
                    f"Response status 200: {is_200} (got {response.status_code})"
                )
                
                if is_200:
                    data = json.loads(response.data)
                    has_fulfillment = 'fulfillmentText' in data
                    test_passed = print_result(
                        has_fulfillment,
                        f"Has fulfillmentText: {has_fulfillment}"
                    ) and test_passed
                    
                    if has_fulfillment:
                        message = data['fulfillmentText']
                        has_summary = 'สรุปการติดตาม' in message
                        test_passed = print_result(
                            has_summary,
                            f"Contains summary text: {has_summary}"
                        ) and test_passed
        
    except Exception as e:
        test_passed = print_result(False, f"Error testing handler: {e}")
    
    return test_passed


def test_bug_4_error_handling():
    """
    BUG #4: Test enhanced error handling in send_line_push
    """
    print_test_header(4, "Enhanced Error Handling")
    
    test_passed = True
    
    # Test 4.1: Test with invalid message
    print("4.1 Testing validation for invalid message...")
    
    result = send_line_push("", "target123")  # Empty message
    test_passed = print_result(
        result == False,
        f"Empty message rejected: {result == False}"
    )
    
    result = send_line_push(None, "target123")  # None message
    test_passed = print_result(
        result == False,
        f"None message rejected: {result == False}"
    ) and test_passed
    
    # Test 4.2: Test with invalid target_id
    print("\n4.2 Testing validation for invalid target_id...")
    
    with patch('services.notification.LINE_CHANNEL_ACCESS_TOKEN', 'mock_token'):
        result = send_line_push("Test message", "")  # Empty target
        test_passed = print_result(
            result == False,
            f"Empty target_id rejected: {result == False}"
        ) and test_passed
        
        result = send_line_push("Test message", "short")  # Too short
        test_passed = print_result(
            result == False,
            f"Short target_id rejected: {result == False}"
        ) and test_passed
    
    # Test 4.3: Test with missing token
    print("\n4.3 Testing with missing LINE token...")
    
    with patch('services.notification.LINE_CHANNEL_ACCESS_TOKEN', None):
        result = send_line_push("Test", "valid_target_123")
        test_passed = print_result(
            result == False,
            f"Missing token handled: {result == False}"
        ) and test_passed
    
    # Test 4.4: Test exception handling
    print("\n4.4 Testing exception handling...")
    
    with patch('services.notification.requests.post') as mock_post:
        mock_post.side_effect = Exception("Network error")
        
        with patch('services.notification.LINE_CHANNEL_ACCESS_TOKEN', 'mock_token'):
            result = send_line_push("Test", "valid_target_12345")
            test_passed = print_result(
                result == False,
                f"Exception caught gracefully: {result == False}"
            ) and test_passed
    
    return test_passed


def run_all_tests():
    """Run all test suites"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}")
    print("="*70)
    print(" " * 15 + "BUG FIX VERIFICATION TEST SUITE")
    print("="*70)
    print(f"{Colors.RESET}\n")
    
    results = {}
    
    # Run each test
    try:
        results['bug_1'] = test_bug_1_parameter_order()
    except Exception as e:
        print(f"{Colors.RED}❌ Test 1 crashed: {e}{Colors.RESET}")
        results['bug_1'] = False
    
    try:
        results['bug_2'] = test_bug_2_return_values()
    except Exception as e:
        print(f"{Colors.RED}❌ Test 2 crashed: {e}{Colors.RESET}")
        results['bug_2'] = False
    
    try:
        results['bug_3'] = test_bug_3_missing_handler()
    except Exception as e:
        print(f"{Colors.RED}❌ Test 3 crashed: {e}{Colors.RESET}")
        results['bug_3'] = False
    
    try:
        results['bug_4'] = test_bug_4_error_handling()
    except Exception as e:
        print(f"{Colors.RED}❌ Test 4 crashed: {e}{Colors.RESET}")
        results['bug_4'] = False
    
    # Print summary
    print(f"\n{Colors.BOLD}{Colors.BLUE}")
    print("="*70)
    print(" " * 25 + "TEST SUMMARY")
    print("="*70)
    print(f"{Colors.RESET}\n")
    
    for bug_name, passed in results.items():
        status = f"{Colors.GREEN}✅ PASSED{Colors.RESET}" if passed else f"{Colors.RED}❌ FAILED{Colors.RESET}"
        print(f"{bug_name.upper()}: {status}")
    
    # Overall result
    all_passed = all(results.values())
    
    print(f"\n{Colors.BOLD}")
    if all_passed:
        print(f"{Colors.GREEN}{'='*70}")
        print(" " * 20 + "🎉 ALL TESTS PASSED! 🎉")
        print(f"{'='*70}{Colors.RESET}\n")
        print("✅ All 4 bugs have been successfully fixed!")
        print("✅ Code is ready for deployment!")
    else:
        print(f"{Colors.RED}{'='*70}")
        print(" " * 20 + "⚠️  SOME TESTS FAILED  ⚠️")
        print(f"{'='*70}{Colors.RESET}\n")
        failed_tests = [name for name, passed in results.items() if not passed]
        print(f"❌ Failed tests: {', '.join(failed_tests)}")
        print("⚠️  Please review the fixes and try again!")
    
    print()
    
    return all_passed


if __name__ == '__main__':
    try:
        success = run_all_tests()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Test interrupted by user{Colors.RESET}")
        sys.exit(2)
    except Exception as e:
        print(f"\n{Colors.RED}Test suite crashed: {e}{Colors.RESET}")
        import traceback
        traceback.print_exc()
        sys.exit(3)
