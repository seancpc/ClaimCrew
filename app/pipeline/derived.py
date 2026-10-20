"""程式先算的確定性事實(SPEC 2026-10-01-c P1)。

v2 全量實證:gpt-4o 會抄對輸入裡的數字,但自己做日期算術會錯(等待期 20 日寫成「已滿 30 日」、住院日數少算出院日)。
故日數、間隔、距生效日天數一律由程式算好注入呼叫 2(`derived_facts`),prompt 規定模型只能引用、不得自算。
日期不可解析 → 對應欄位 None + warnings(不拋例外;Gate A2 另行 fail-closed)。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from app.schemas.claim import ClaimBundle, StrictModel

INJURY_ICD_PREFIXES = ("S", "T")  # ICD-10 第十九章「損傷、中毒」:傷害不受疾病等待期限制


class AdmissionFacts(StrictModel):
    admission_date: str
    discharge_date: str
    days_inclusive: int | None = Field(description="含入出院日之住院日數(首尾皆計);日期不可解析為 null")
    icu_days: int = Field(description="加護病房日數;不可解析為 0(見 warnings)")
    gap_days_after_previous: int | None = Field(
        description="與前一段出院日之間隔(出院日不計、翌日為第 1 日;本段入院日 − 前段出院日);首段為 null")


class DerivedFacts(StrictModel):
    admissions: list[AdmissionFacts]
    total_days: int = Field(description="各段 days_inclusive 加總(不含合併/上限判斷,那是條款適用問題)")
    total_icu_days: int
    days_since_effective: int | None = Field(description="事件日(首段入院日;無住院取收據日)− 保單生效日 之日曆天數")
    is_injury_icd: bool = Field(description="ICD-10 代碼以 S/T 開頭(損傷、中毒章)")
    receipt_total: Decimal
    other_insurer_paid_any: bool
    claim_event_date: str = Field(description="得為申請之日(最後一段出院日;無住院取收據日)原文")
    application_date: str = Field(description="申請書申請日原文")
    days_event_to_application: int | None = Field(
        description="申請日 − 得為申請之日 之日曆天數(給付申請時效二年);不可解析為 null")
    stay_groups: list[list[int]] = Field(
        description="同一次住院分組(段落索引;出院後十四日內再住院同組,含第 14 日;程式假設同一診斷書=同一疾病)")
    stay_group_days: list[int] = Field(description="各組 days_inclusive 合計(不可解析之段計 0)")
    warnings: list[str] = Field(description="無法解析的欄位路徑")


MERGE_GAP_DAYS_DEFAULT = 14  # POL-A 第12條 / POL-C 第13條「出院後十四日內」;POL-B 無合併條款(Gate 不使用分組)


def _group_stays(adms: list[AdmissionFacts], merge_gap: int = MERGE_GAP_DAYS_DEFAULT) -> list[list[int]]:
    groups: list[list[int]] = []
    for i, a in enumerate(adms):
        if groups and a.gap_days_after_previous is not None and a.gap_days_after_previous <= merge_gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def _parse(s: str, path: str, warnings: list[str]) -> date | None:
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        warnings.append(path)
        return None


def _parse_int(s: str, path: str, warnings: list[str]) -> int:
    try:
        return int(s.strip())
    except ValueError:
        warnings.append(path)
        return 0


def derive_facts(bundle: ClaimBundle) -> DerivedFacts:
    warnings: list[str] = []
    adms: list[AdmissionFacts] = []
    prev_discharge: date | None = None
    for i, a in enumerate(bundle.diagnosis_certificate.admissions):
        adm = _parse(a.admission_date.value, f"admissions[{i}].admission_date", warnings)
        dis = _parse(a.discharge_date.value, f"admissions[{i}].discharge_date", warnings)
        icu = _parse_int(a.icu_days.value, f"admissions[{i}].icu_days", warnings)
        days = (dis - adm).days + 1 if adm and dis else None
        gap = (adm - prev_discharge).days if adm and prev_discharge else None
        adms.append(AdmissionFacts(admission_date=a.admission_date.value, discharge_date=a.discharge_date.value,
                                   days_inclusive=days, icu_days=icu, gap_days_after_previous=gap))
        prev_discharge = dis if dis else prev_discharge
    effective = _parse(bundle.claim_application.policy_effective_date.value, "policy_effective_date", warnings)
    if bundle.diagnosis_certificate.admissions:
        event = _parse(bundle.diagnosis_certificate.admissions[0].admission_date.value, "event_date", warnings)
    else:
        event = _parse(bundle.medical_receipt.receipt_date.value, "medical_receipt.receipt_date", warnings)
    paid = bundle.claim_application.other_insurer_paid
    adm_list = bundle.diagnosis_certificate.admissions
    claim_event_raw = adm_list[-1].discharge_date.value if adm_list else bundle.medical_receipt.receipt_date.value
    claim_event = _parse(claim_event_raw, "claim_event_date", warnings)
    app_raw = bundle.claim_application.application_date.value
    app_date = _parse(app_raw, "application_date", warnings)
    groups = _group_stays(adms)
    return DerivedFacts(
        admissions=adms,
        stay_groups=groups,
        stay_group_days=[sum(adms[i].days_inclusive or 0 for i in g) for g in groups],
        total_days=sum(a.days_inclusive or 0 for a in adms),
        total_icu_days=sum(a.icu_days for a in adms),
        days_since_effective=(event - effective).days if event and effective else None,
        is_injury_icd=bundle.diagnosis_certificate.icd_code.value.strip().upper().startswith(INJURY_ICD_PREFIXES),
        receipt_total=bundle.medical_receipt.total_amount,
        other_insurer_paid_any=any(getattr(paid, k) != 0 for k in type(paid).model_fields),
        claim_event_date=claim_event_raw,
        application_date=app_raw,
        days_event_to_application=(app_date - claim_event).days if app_date and claim_event else None,
        warnings=warnings,
    )
