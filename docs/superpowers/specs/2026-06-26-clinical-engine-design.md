# Clinical Risk Engine (KWN-10) Design Specification

## 1. Goal
To decouple the clinical risk assessment calculations (symptoms evaluation and personal demographics risk scoring) from database persistence, LINE messaging APIs, and other side effects. This ensures that the core clinical decision logic is pure, deterministic, and easily verifiable through automated tests.

## 2. Architecture & Components

The core logic is relocated from `services/risk_assessment.py` into a new, dependency-free module `services/clinical_engine.py`.

### 2.1 Pure Input and Output Contracts
We use Python dataclasses to represent the parameters and result structures.

```python
from dataclasses import dataclass
from typing import Optional, List

@dataclass(frozen=True)
class SymptomClinicalInput:
    pain: Optional[int]
    wound: Optional[str]
    fever: Optional[str]
    mobility: Optional[str]
    neuro: Optional[str] = None

@dataclass(frozen=True)
class SymptomClinicalOutput:
    risk_score: int
    risk_code: str
    risk_label: str
    risk_details: List[str]
    action_advice: str
    patient_message: str
    notification_required: bool

@dataclass(frozen=True)
class PersonalClinicalInput:
    age: Optional[int]
    weight: Optional[float]
    height: Optional[float]
    disease: Optional[str]

@dataclass(frozen=True)
class PersonalClinicalOutput:
    risk_score: int
    risk_level: str
    risk_factors: List[str]
    bmi: float
    diseases_normalized: List[str]
    description: str
    advice: List[str]
    patient_message: str
    notification_required: bool
```

### 2.2 Core Logic Separation
* `evaluate_symptom_risk(inputs: SymptomClinicalInput) -> SymptomClinicalOutput`: Performs the parsing, scoring, and text generation for symptom triage.
* `evaluate_personal_risk(inputs: PersonalClinicalInput) -> PersonalClinicalOutput`: Performs age, BMI, and disease normalization risk calculations.

### 2.3 Wrapper Integration (`services/risk_assessment.py`)
The existing endpoints `calculate_symptom_risk_outcome` and `calculate_personal_risk` remain as the integration layer:
1. Wrap incoming parameters into the respective dataclass input objects.
2. Delegate to the pure engine functions in `services/clinical_engine.py`.
3. Handle Sheets queries/saves, LINE alert pushes, and audit logs.
4. Return the outcome dict or dataclass format as expected by existing callers.

## 3. Verification Plan

### 3.1 Unit Testing
A new test file `tests/test_clinical_engine.py` will verify:
* Deterministic output matching identical input.
* Proper calculation of scores, levels, and Thais warnings.
* Correct identification of when `notification_required` is triggered.
* Disease normalization boundary cases.

### 3.2 Regression Testing
* Ensure all existing tests in `tests/test_symptom_risk.py` and `tests/test_patient_registration.py` continue to pass.
