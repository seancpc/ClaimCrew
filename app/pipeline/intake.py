"""程式側 Intake(SPEC 2026-10-01 行為 3):文件存在性是確定性事實,不需 LLM。

對應 Intake prompt 規則:三件應備(診斷證明書 / 醫療收據 / 理賠申請書),事故證明為選擇性;
`clause_basis` 為該保單「應備文件」與「文件不全之補正」兩條(以標題定位,不寫死條號);
缺理賠申請書而無法歸戶保單時為空列表。
"""

from __future__ import annotations

from pathlib import Path

from app.gate.policy_index import Clause
from app.schemas.claim import IntakeResult, IntakeStatus

REQUIRED_DOCUMENTS: tuple[tuple[str, str], ...] = (
    ("diagnosis_certificate", "診斷證明書"),
    ("medical_receipt", "醫療收據"),
    ("claim_application", "理賠申請書"),
)
OPTIONAL_DOCUMENTS: tuple[tuple[str, str], ...] = (("incident_statement", "事故證明"),)
INTAKE_CLAUSE_TITLES = ("應備文件", "文件不全之補正")


def pdf_path(case_dir: Path, doc_key: str) -> Path:
    return case_dir / "pdf" / f"{doc_key}.pdf"


def check_documents(case_dir: Path) -> tuple[IntakeStatus, list[str]]:
    missing = [name for key, name in REQUIRED_DOCUMENTS if not pdf_path(case_dir, key).is_file()]
    return (IntakeStatus.INCOMPLETE if missing else IntakeStatus.COMPLETE), missing


def intake_clause_basis(policy_code: str | None, index: dict[str, dict[str, Clause]]) -> list[str]:
    if policy_code is None or policy_code not in index:
        return []
    return [f"{policy_code} {cn}" for cn, c in index[policy_code].items() if c.title in INTAKE_CLAUSE_TITLES]


def intake_result(
    claim_id: str,
    status: IntakeStatus,
    missing: list[str],
    policy_code: str | None,
    index: dict[str, dict[str, Clause]],
) -> IntakeResult:
    return IntakeResult(
        claim_id=claim_id,
        status=status,
        missing_documents=list(missing),
        clause_basis=intake_clause_basis(policy_code, index),
    )
