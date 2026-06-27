# E2E Test Infra: Persistent Due Dispatcher (KWN-04)

## Test Philosophy
- Opaque-box, requirement-driven E2E tests verifying the Persistent Due Dispatcher requirements (R1, R2, R3, R4).
- Methodology: Category-Partition + Boundary Value Analysis + Pairwise Combinatorial Testing + Real-World Workload Testing (Tiers 1-4).

## Feature Inventory
| # | Feature | Source (requirement) | Tier 1 | Tier 2 | Tier 3 |
|---|---------|---------------------|:------:|:------:|:------:|
| 1 | Persistent Due Dispatcher | R1. Persistent Due Dispatcher | 5 | 5 | ✓ |
| 2 | Dispatcher Loop in APScheduler | R2. Dispatcher Loop inside APScheduler | 5 | 5 | ✓ |
| 3 | Claim Lifecycle & Cache Leases | R3. Claim Lifecycle & Cache Leases | 5 | 5 | ✓ |
| 4 | Outage & Restart Catch-up | R4. Outage and Restart Catch-up | 5 | 5 | ✓ |

## Test Architecture
- **Test Runner**: standard Python `unittest` module, run via `python -m unittest tests/test_due_dispatcher.py`.
- **Test Case Location**: `tests/test_due_dispatcher.py`.
- **Mocking**:
  - Google Sheets API is mocked at the worksheet layer (`database.sheets.get_worksheet`) to prevent hitting live spreadsheets during tests.
  - LINE message pushes are mocked at `services.notification.send_line_push`.
  - Time is controlled/mocked using Python's `unittest.mock.patch` or datetime injection to test schedules, boundaries, and outages.

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | End-to-End Patient Follow-Up | Persistent Dispatcher, Claim Lifecycle, Responded Update | Medium |
| 2 | Parallel/Multi-Instance Operation | Cache Leases, Concurrency, Duplicate Claim avoidance | High |
| 3 | Recovery from System Crash | Outage Catch-up, Stale Claim Recovery, Re-scheduling | High |

## Coverage Thresholds
- **Tier 1 (Feature Coverage)**: >=5 test cases per feature (Total: 20 cases).
- **Tier 2 (Boundary & Corner)**: >=5 test cases per feature (Total: 20 cases).
- **Tier 3 (Cross-Feature combinations)**: 4 test cases covering major interactions.
- **Tier 4 (Real-World Application)**: 3 realistic multi-step workloads.
