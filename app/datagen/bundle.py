"""合成案件文件(claim.json 之 documents)→ ClaimBundle 轉接層。

合成資料以「已知正確值」表達,故每個抽取欄位信心一律 1.0、low_confidence_fields 為空。
用途:①生成器落盤前以 schema 驗證(契約與資料不得漂移,稽核 A17);
②Phase 1 前以「完美抽取」模擬 Extraction Agent 輸出跑 Gate / eval。
缺件案(任一文件為 None)不構成 ClaimBundle——Intake 已中止,由呼叫端自行處理。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.claim import (
    Admission,
    ClaimApplication,
    ClaimBundle,
    DiagnosisCertificate,
    ExtractedField,
    IncidentSource,
    IncidentStatement,
    MedicalReceipt,
    OtherInsurerPaid,
    ReceiptItem,
)


def _f(value: str) -> ExtractedField:
    return ExtractedField(value=value, confidence=1.0)


def bundle_from_documents(claim_id: str, docs: dict[str, Any]) -> ClaimBundle:
    dc, mr, ca = docs["diagnosis_certificate"], docs["medical_receipt"], docs["claim_application"]
    if dc is None or mr is None or ca is None:
        raise ValueError(f"{claim_id}:缺件案不構成 ClaimBundle")
    inc = docs.get("incident_statement")
    incident = None if inc is None else IncidentStatement(
        source=IncidentSource(inc["source"]), incident_date=_f(inc["incident_date"]),
        statement_text=_f(inc["statement_text"]),
    )
    return ClaimBundle(
        claim_id=claim_id,
        diagnosis_certificate=DiagnosisCertificate(
            patient_name=_f(dc["patient_name"]),
            diagnosis=_f(dc["diagnosis"]),
            icd_code=_f(dc["icd_code"]),
            admissions=[
                Admission(admission_date=_f(a["admission_date"]), discharge_date=_f(a["discharge_date"]),
                          icu_days=_f(a["icu_days"]))
                for a in dc["admissions"]
            ],
            treatment_summary=_f(dc["treatment_summary"]),
            issue_date=_f(dc["issue_date"]),
            hospital_name=_f(dc["hospital_name"]),
        ),
        medical_receipt=MedicalReceipt(
            hospital_name=_f(mr["hospital_name"]),
            patient_name=_f(mr["patient_name"]),
            receipt_date=_f(mr["receipt_date"]),
            items=[ReceiptItem(item_name=i["item_name"], amount=Decimal(i["amount"])) for i in mr["items"]],
            total_amount=Decimal(mr["total_amount"]),
            is_copy=bool(mr["is_copy"]),
        ),
        claim_application=ClaimApplication(
            applicant_name=_f(ca["applicant_name"]),
            policy_number=_f(ca["policy_number"]),
            policy_effective_date=_f(ca["policy_effective_date"]),
            claim_type=_f(ca["claim_type"]),
            application_date=_f(ca["application_date"]),
            claimed_amount=Decimal(ca["claimed_amount"]),
            other_insurer_paid=OtherInsurerPaid(**{k: Decimal(v) for k, v in ca["other_insurer_paid"].items()}),
        ),
        incident_statement=incident,
        low_confidence_fields=[],
    )
