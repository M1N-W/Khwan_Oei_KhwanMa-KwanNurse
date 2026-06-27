# -*- coding: utf-8 -*-
"""
Risk Assessment Service Module
Handles symptom and personal risk calculations
"""
from dataclasses import dataclass
from database import save_symptom_data, save_profile_data
from services.notification import (
    send_line_push,
    build_symptom_notification,
    build_risk_notification
)
from database.failed_nurse_alerts import save_failed_symptom_alert
from services.metrics import incr as _metric
from config import get_logger
from services.clinical_engine import (
    SymptomClinicalInput,
    evaluate_symptom_risk,
    PersonalClinicalInput,
    evaluate_personal_risk,
    normalize_diseases
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class SymptomAssessmentOutcome:
    """Internal structured outcome for symptom-assessment reliability tests."""
    message: str
    risk_code: str
    risk_score: int
    save_succeeded: bool
    notification_required: bool
    notification_succeeded: bool | None
    failed_alert_persisted: bool | None


def calculate_symptom_risk(user_id, pain, wound, fever, mobility, neuro=None):
    """Compatibility API: return the patient-facing assessment message."""
    return calculate_symptom_risk_outcome(
        user_id, pain, wound, fever, mobility, neuro=neuro,
    ).message


def calculate_symptom_risk_outcome(user_id, pain, wound, fever, mobility, neuro=None):
    """
    Calculate symptom-based risk score by delegating to pure clinical_engine logic,
    then performing sheet logging and notifications.
    """
    inputs = SymptomClinicalInput(
        pain=pain,
        wound=wound,
        fever=fever,
        mobility=mobility,
        neuro=neuro
    )
    engine_out = evaluate_symptom_risk(inputs)
    risk_score = engine_out.risk_score
    risk_code = engine_out.risk_code
    risk_label = engine_out.risk_label
    message = engine_out.patient_message

    # Save to sheet. Treat both False and unexpected exceptions as failures.
    try:
        save_succeeded = bool(
            save_symptom_data(user_id, pain, wound, fever, mobility, risk_code, risk_score)
        )
    except Exception:
        save_succeeded = False
        logger.exception(
            "Symptom assessment save raised risk_code=%s risk_score=%s",
            risk_code, risk_score,
        )

    if not save_succeeded:
        _metric("symptom_assessment.save_failed")
        logger.warning(
            "Symptom assessment save not confirmed risk_code=%s risk_score=%s",
            risk_code, risk_score,
        )

    # Send notification if high risk
    notification_required = engine_out.notification_required
    notification_succeeded = None
    failed_alert_persisted = None
    if notification_required:
        notify_msg = build_symptom_notification(
            user_id, pain, wound, fever, mobility, risk_label, risk_score
        )
        try:
            notification_succeeded = bool(send_line_push(notify_msg))
        except Exception:
            notification_succeeded = False
            logger.exception(
                "Symptom assessment notification raised risk_code=%s risk_score=%s",
                risk_code, risk_score,
            )

        if not notification_succeeded:
            _metric("symptom_assessment.notify_failed")
            logger.warning(
                "Symptom assessment notification not confirmed risk_code=%s risk_score=%s",
                risk_code, risk_score,
            )
            try:
                failed_alert_persisted = bool(save_failed_symptom_alert(
                    user_id=user_id,
                    risk_code=risk_code,
                    risk_score=risk_score,
                    pain=pain,
                    wound=wound,
                    fever=fever,
                    mobility=mobility,
                    neuro=neuro,
                    notification_message=notify_msg or "",
                ))
            except Exception:
                failed_alert_persisted = False

            if failed_alert_persisted:
                _metric("symptom_assessment.failed_alert_persisted")
            else:
                _metric("symptom_assessment.failed_alert_persist_failed")

    if (not save_succeeded) or (notification_required and notification_succeeded is False):
        _metric("symptom_assessment.partial_failure")

    # Trend check
    if save_succeeded:
        try:
            from services.early_warning import check_user_early_warning
            check_user_early_warning(user_id)
        except Exception:
            logger.exception("Early-warning check failed for %s", user_id)
    else:
        _metric("symptom_assessment.early_warning_skipped_save_failed")

    message = _append_symptom_reliability_notice(
        message=message,
        save_succeeded=save_succeeded,
        notification_required=notification_required,
        notification_succeeded=notification_succeeded,
    )

    return SymptomAssessmentOutcome(
        message=message,
        risk_code=risk_code,
        risk_score=risk_score,
        save_succeeded=save_succeeded,
        notification_required=notification_required,
        notification_succeeded=notification_succeeded,
        failed_alert_persisted=failed_alert_persisted,
    )


def _append_symptom_reliability_notice(
    *,
    message: str,
    save_succeeded: bool,
    notification_required: bool,
    notification_succeeded: bool | None,
) -> str:
    """Append patient-safe reliability status only on failure paths."""
    if save_succeeded and (not notification_required or notification_succeeded is True):
        return message

    notices = []
    if not notification_required:
        notices.append(
            "ประเมินอาการเรียบร้อย แต่ยังไม่สามารถยืนยันการบันทึกประวัติได้ "
            "กรุณาลองรายงานอาการอีกครั้งภายหลัง หากอาการแย่ลงให้ติดต่อพยาบาลทันที"
        )
    elif not save_succeeded and notification_succeeded is True:
        notices.append(
            "ส่งแจ้งเตือนพยาบาลแล้ว แต่ยังไม่สามารถยืนยันการบันทึกรายงานได้"
        )
    elif save_succeeded and notification_succeeded is False:
        notices.append(
            "บันทึกรายงานไว้แล้ว แต่ยังไม่สามารถยืนยันว่าแจ้งพยาบาลสำเร็จ "
            "กรุณากดปุ่ม 'ปรึกษาพยาบาล' หรือโทรติดต่อทีมรักษาทันที"
        )
    else:
        notices.append(
            "ยังไม่สามารถยืนยันการบันทึกรายงาน และยังไม่สามารถยืนยันว่าแจ้งพยาบาลสำเร็จ "
            "กรุณากดปุ่ม 'ปรึกษาพยาบาล' หรือโทรติดต่อทีมรักษาทันที"
        )

    return message + "\n\n📌 สถานะระบบ:\n" + "\n".join(notices)


def calculate_personal_risk(user_id, age, weight, height, disease):
    """
    Calculate personal health risk based on demographics and conditions by delegating
    to pure clinical_engine logic, then performing sheet logging and notifications.
    """
    inputs = PersonalClinicalInput(
        age=age,
        weight=weight,
        height=height,
        disease=disease
    )
    engine_out = evaluate_personal_risk(inputs)
    risk_score = engine_out.risk_score
    risk_level = engine_out.risk_level
    bmi = engine_out.bmi
    disease_normalized = engine_out.diseases_normalized
    message = engine_out.patient_message

    # Save to sheet
    save_profile_data(user_id, inputs.age, inputs.weight, inputs.height, bmi, 
                      disease_normalized, risk_level, risk_score)
    
    # Send notification if high risk
    if engine_out.notification_required:
        diseases_str = ", ".join(disease_normalized) if disease_normalized else "ไม่มีโรคประจำตัว"
        notify_msg = build_risk_notification(
            user_id,
            inputs.age if inputs.age is not None else "ไม่ระบุ",
            bmi,
            diseases_str,
            risk_level,
            risk_score
        )
        send_line_push(notify_msg)
    
    return message
