"""確定性事實注入(SPEC 2026-10-01-c P1)。手算值取自真值 derivation(C001/C008/C019/C060)。"""

from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.pipeline.derived import DerivedFacts, derive_facts
from app.schemas.claim import Admission
from tests.test_gate import _bundle, _f


def _b(adms: list[tuple[str, str, str]], effective: str = "2025-01-10", icd: str = "J18.9",
       receipt: str = "2026-03-07"):
    b = _bundle()
    dc = b.diagnosis_certificate.model_copy(update={
        "admissions": [Admission(admission_date=_f(a), discharge_date=_f(d), icu_days=_f(i)) for a, d, i in adms],
        "icd_code": _f(icd)})
    app = b.claim_application.model_copy(update={"policy_effective_date": _f(effective)})
    rc = b.medical_receipt.model_copy(update={"receipt_date": _f(receipt), "total_amount": Decimal("12345")})
    return b.model_copy(update={"diagnosis_certificate": dc, "claim_application": app, "medical_receipt": rc})


def test_c001_single_admission_inclusive_days():
    f = derive_facts(_b([("2026-03-02", "2026-03-07", "0")]))
    assert [a.days_inclusive for a in f.admissions] == [6]
    assert f.admissions[0].gap_days_after_previous is None
    assert f.total_days == 6 and f.total_icu_days == 0
    assert f.days_since_effective == (date(2026, 3, 2) - date(2025, 1, 10)).days
    assert f.is_injury_icd is False and f.warnings == []


def test_c008_two_admissions_gap_counts_from_day_after_discharge():
    f = derive_facts(_b([("2026-01-05", "2026-02-24", "0"), ("2026-03-06", "2026-04-20", "0")]))
    assert [a.days_inclusive for a in f.admissions] == [51, 46]
    assert f.admissions[1].gap_days_after_previous == 10  # 出院後 10 日再住院(真值:「出院後 10 日(3/6)」)
    assert f.total_days == 97


def test_c060_gap_fifteen_days_is_beyond_fourteen():
    f = derive_facts(_b([("2026-01-05", "2026-03-05", "0"), ("2026-03-20", "2026-05-03", "0")]))
    assert [a.days_inclusive for a in f.admissions] == [60, 45]
    assert f.admissions[1].gap_days_after_previous == 15  # 真值:「03-20 再住院為第 15 日」
    assert f.total_days == 105


def test_c019_icu_days_parsed_and_summed():
    f = derive_facts(_b([("2026-05-01", "2026-05-20", "7")]))
    assert f.admissions[0].days_inclusive == 20 and f.admissions[0].icu_days == 7 and f.total_icu_days == 7


def test_injury_icd_and_receipt_facts():
    f = derive_facts(_b([("2026-05-12", "2026-05-14", "0")], effective="2026-05-01", icd="S52.501"))
    assert f.is_injury_icd is True and f.days_since_effective == 11
    assert f.receipt_total == Decimal("12345") and f.other_insurer_paid_any is False


def test_no_admission_uses_receipt_date_for_waiting_days():
    f = derive_facts(_b([], effective="2026-05-01", receipt="2026-05-10"))
    assert f.admissions == [] and f.total_days == 0 and f.days_since_effective == 9


def test_unparseable_dates_yield_null_and_warnings_not_exception():
    f = derive_facts(_b([("2026/03/02", "2026-03-07", "x")], effective="unknown"))
    assert f.admissions[0].days_inclusive is None and f.admissions[0].icu_days == 0
    assert f.days_since_effective is None and f.total_days == 0
    assert {"admissions[0].admission_date", "admissions[0].icu_days", "policy_effective_date"} <= set(f.warnings)


def test_other_insurer_paid_any_true_when_any_bucket_nonzero():
    b = _b([("2026-03-02", "2026-03-07", "0")])
    paid = b.claim_application.other_insurer_paid.model_copy(update={"misc_fee": Decimal("500")})
    b = b.model_copy(update={"claim_application": b.claim_application.model_copy(update={"other_insurer_paid": paid})})
    assert derive_facts(b).other_insurer_paid_any is True


def test_derived_facts_is_json_serialisable_model():
    f = derive_facts(_b([("2026-03-02", "2026-03-07", "0")]))
    assert isinstance(f, DerivedFacts)
    import json
    assert json.loads(f.model_dump_json())["admissions"][0]["days_inclusive"] == 6


# --- P1-5 property ---
_dates = st.dates(min_value=date(2024, 1, 1), max_value=date(2027, 12, 31))


@given(st.lists(st.tuples(_dates, st.integers(0, 120), st.integers(0, 30)), min_size=1, max_size=4))
@settings(max_examples=100, deadline=None)
def test_property_days_at_least_one_and_gaps_nonnegative_when_sorted(spec):
    spec = sorted(spec, key=lambda t: t[0])
    adms = []
    prev_end = None
    for start, length, icu in spec:
        if prev_end is not None and start <= prev_end:  # 保證段落不重疊(依入院日遞增、前段出院在後段入院前)
            start = prev_end + timedelta(days=1)
        end = start + timedelta(days=length)
        adms.append((start.isoformat(), end.isoformat(), str(min(icu, length + 1))))
        prev_end = end
    f = derive_facts(_b(adms))
    assert all(a.days_inclusive is not None and a.days_inclusive >= 1 for a in f.admissions)
    assert all(a.gap_days_after_previous is None or a.gap_days_after_previous >= 0 for a in f.admissions)
    assert f.total_days == sum(a.days_inclusive or 0 for a in f.admissions)
    assert f.total_icu_days == sum(a.icu_days for a in f.admissions)
    assert f.warnings == []


# --- P4-2 時效事實(SPEC 2026-10-01-d D1)---
def _with_app_date(b, app_date: str):
    return b.model_copy(update={"claim_application": b.claim_application.model_copy(
        update={"application_date": _f(app_date)})})


def test_claim_event_date_is_last_discharge_and_days_to_application():
    # C067 形:出院 2024-04-10、申請 2026-05-02 → 752 日
    f = derive_facts(_with_app_date(_b([("2024-04-06", "2024-04-10", "0")], receipt="2024-04-10"), "2026-05-02"))
    assert f.claim_event_date == "2024-04-10" and f.application_date == "2026-05-02"
    assert f.days_event_to_application == 752
    two = derive_facts(_with_app_date(_b([("2026-01-05", "2026-02-24", "0"), ("2026-03-06", "2026-04-20", "0")]),
                                      "2026-05-01"))
    assert two.claim_event_date == "2026-04-20" and two.days_event_to_application == 11  # 取最後一段出院日


def test_claim_event_date_falls_back_to_receipt_without_admission():
    f = derive_facts(_with_app_date(_b([], receipt="2026-05-10"), "2026-05-20"))
    assert f.claim_event_date == "2026-05-10" and f.days_event_to_application == 10


def test_unparseable_application_date_yields_null_and_warning():
    f = derive_facts(_with_app_date(_b([("2026-03-02", "2026-03-07", "0")]), "unknown"))
    assert f.days_event_to_application is None and "application_date" in f.warnings


# --- SPEC 2026-10-02-g G1:同一次住院分組(間隔 ≤14 日同組;POL-B 無合併條款由 Gate 決定不呼叫分組語意) ---
def test_stay_groups_split_by_fourteen_day_gap():
    f = derive_facts(_b([("2026-01-05", "2026-02-24", "0"), ("2026-03-06", "2026-04-20", "0")]))  # C008 gap 10
    assert f.stay_groups == [[0, 1]] and f.stay_group_days == [97]
    f = derive_facts(_b([("2026-01-05", "2026-03-05", "0"), ("2026-03-20", "2026-05-03", "0")]))  # C060 gap 15
    assert f.stay_groups == [[0], [1]] and f.stay_group_days == [60, 45]
    f = derive_facts(_b([("2026-04-01", "2026-04-05", "0"), ("2026-04-19", "2026-04-21", "0")]))  # C066 gap 14(含)
    assert f.stay_groups == [[0, 1]] and f.stay_group_days == [8]
    three = derive_facts(_b([("2026-01-05", "2026-03-15", "0"), ("2026-04-24", "2026-06-22", "0"),
                             ("2026-08-22", "2026-10-25", "0")]))  # C061 gaps 40/61
    assert three.stay_groups == [[0], [1], [2]] and three.stay_group_days == [70, 60, 65]
    assert derive_facts(_b([])).stay_groups == []
    assert derive_facts(_b([("2026-03-02", "2026-03-07", "0")])).stay_groups == [[0]]


def test_stay_groups_with_unparseable_gap_keep_segments_separate():
    f = derive_facts(_b([("2026-01-05", "x", "0"), ("2026-03-06", "2026-04-20", "0")]))
    assert f.stay_groups == [[0], [1]] and f.stay_group_days == [0, 46]
