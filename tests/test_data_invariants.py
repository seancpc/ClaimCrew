"""凍結資料集(data/claims)的不變量——把先前只在對話裡手動 assert 的檢查固化(B7)。

任何一條失敗代表資料集或生成器被改壞,不是測試錯。
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.datagen.bundle import bundle_from_documents
from app.datagen.cases import CLAIMS_DIR, all_cases
from app.datagen.variants import POLICY_PARAMS
from app.gate.checks import AMOUNT_UPPER_BOUND_BASE, AMOUNT_UPPER_BOUND_PER_ADMISSION, amount_upper_bound
from app.gate.policy_index import load_policy_index

INDEX = load_policy_index()
CASE_DIRS = sorted(d for d in CLAIMS_DIR.iterdir() if d.is_dir())


def _load(d: Path):
    return (json.loads((d / "claim.json").read_text(encoding="utf-8")),
            json.loads((d / "truth.json").read_text(encoding="utf-8")))


CASES = [_load(d) for d in CASE_DIRS]
IDS = [c["claim_id"] for c, _ in CASES]


def test_case_ids_are_c001_to_c073_contiguous():  # SPEC-y 新增 C073
    assert [f"C{i:03d}" for i in range(1, 74)] == IDS


def test_on_disk_matches_generator_spec():
    """落盤資料必須等於生成器規格(防手改 JSON 造成單一真相源分裂)。"""
    spec = {c["claim_id"]: c for c in all_cases()}
    for claim, truth in CASES:
        s = spec[claim["claim_id"]]
        assert claim["documents"] == s["documents"], claim["claim_id"]
        for k in ("decision", "approved_amount", "derivation", "clause_basis"):
            assert truth[k] == s["truth"][k], (claim["claim_id"], k)


@pytest.mark.parametrize("claim,truth", CASES, ids=IDS)
def test_receipt_total_equals_sum_of_items(claim, truth):
    r = claim["documents"]["medical_receipt"]
    if r is None:
        return
    assert Decimal(r["total_amount"]) == sum(Decimal(i["amount"]) for i in r["items"])


@pytest.mark.parametrize("claim,truth", CASES, ids=IDS)
def test_amount_consistent_with_decision(claim, truth):
    amt = Decimal(truth["approved_amount"])
    if truth["decision"] == "approve":
        assert amt > 0
        policy = claim["documents"]["claim_application"]["policy_number"][:5]
        n_adm = len(claim["documents"]["diagnosis_certificate"]["admissions"])
        assert amt <= amount_upper_bound(policy, n_adm), "核付金額超過 Gate 理論上界"
    else:
        assert amt == 0


@pytest.mark.parametrize("claim,truth", CASES, ids=IDS)
def test_claimed_amount_not_below_approved(claim, truth):
    app = claim["documents"]["claim_application"]
    if app is None:
        return
    assert Decimal(app["claimed_amount"]) >= Decimal(truth["approved_amount"])


@pytest.mark.parametrize("claim,truth", CASES, ids=IDS)
def test_dates_are_ordered(claim, truth):
    dc, app = claim["documents"]["diagnosis_certificate"], claim["documents"]["claim_application"]
    if dc is not None:
        assert dc["admissions"], "全案皆含至少一段住院/門診日"
        prev_dis = None
        for a in dc["admissions"]:
            adm, dis = date.fromisoformat(a["admission_date"]), date.fromisoformat(a["discharge_date"])
            assert adm <= dis <= date.fromisoformat(dc["issue_date"])
            if prev_dis is not None:
                assert prev_dis < adm, "多段住院須依時間遞增且不重疊"
            prev_dis = dis
            assert 0 <= int(a["icu_days"]) <= (dis - adm).days + 1, "ICU 日數不得超過該段住院日數"
        first_adm = date.fromisoformat(dc["admissions"][0]["admission_date"])
        if app is not None:
            assert date.fromisoformat(app["policy_effective_date"]) < first_adm, "生效日須早於入院日"
            assert prev_dis < date.fromisoformat(app["application_date"]), "申請日須晚於出院日"


@pytest.mark.parametrize("claim,truth", CASES, ids=IDS)
def test_clause_basis_resolves_in_policy_index(claim, truth):
    """真值引用的每一條依據都必須真實存在(與 Gate 同一套反查)。"""
    for basis in truth["clause_basis"]:
        policy_id, clause_no = basis.split(" ", 1)
        assert clause_no in INDEX[policy_id], basis


@pytest.mark.parametrize("claim,truth", CASES, ids=IDS)
def test_clause_basis_policy_matches_application(claim, truth):
    app = claim["documents"]["claim_application"]
    if app is None:
        assert truth["clause_basis"] == [], "無申請書即無保單可歸戶,依據應留白(C026 檢核結論)"
        return
    policy = app["policy_number"][:5]
    assert all(b.startswith(policy) for b in truth["clause_basis"]), "依據引到別張保單"


def test_decision_distribution_is_frozen():
    counts = {d: sum(1 for _, t in CASES if t["decision"] == d) for d in ("approve", "deny", "manual_review")}
    assert counts == {"approve": 41, "deny": 23, "manual_review": 9}  # SPEC-y 新增 C073(approve)


def test_names_and_hospitals_are_synthetic_pool_only():
    """人名/院名只能來自生成器內的合成池(資料安全紅線:零真實個資)。"""
    from app.datagen.cases import CASES as HANDWRITTEN
    from app.datagen.variants import HOSPITALS, NAMES
    hand = [c["documents"]["diagnosis_certificate"] for c in HANDWRITTEN
            if c["documents"]["diagnosis_certificate"]]
    pool = set(NAMES) | {d["patient_name"] for d in hand}
    hospitals = set(HOSPITALS) | {d["hospital_name"].split("(")[0] for d in hand}  # 去括號註記取基底名
    for claim, _ in CASES:
        for key in ("diagnosis_certificate", "medical_receipt", "claim_application"):
            doc = claim["documents"][key]
            if doc is None:
                continue
            name = doc.get("patient_name") or doc.get("applicant_name")
            assert name in pool, (claim["claim_id"], name)
            if "hospital_name" in doc:
                assert any(h in doc["hospital_name"] for h in hospitals), (claim["claim_id"], doc["hospital_name"])


@pytest.mark.parametrize("claim,truth", CASES, ids=IDS)
def test_claim_json_conforms_to_claim_bundle_schema(claim, truth):
    """落盤的 claim.json 必須能構成 ClaimBundle(契約與資料同步;A17)。缺件案為 Intake 中止案。"""
    docs = claim["documents"]
    if any(docs[k] is None for k in ("diagnosis_certificate", "medical_receipt", "claim_application")):
        assert truth["decision"] == "manual_review"
        with pytest.raises(ValueError):
            bundle_from_documents(claim["claim_id"], docs)
        return
    b = bundle_from_documents(claim["claim_id"], docs)
    assert b.claim_id == claim["claim_id"]
    assert len(b.diagnosis_certificate.admissions) == len(docs["diagnosis_certificate"]["admissions"])
    assert b.medical_receipt.total_amount == sum(i.amount for i in b.medical_receipt.items)


def test_amount_upper_bounds_derive_from_policy_params():
    """Gate 金額上界與條款參數同源(C28):任一方改動而另一方未改即失敗。"""
    a, b, c = POLICY_PARAMS["POL-A"], POLICY_PARAMS["POL-B"], POLICY_PARAMS["POL-C"]
    # 基底:POL-A 年度累計 180 日全程加護(第13條 90/180 日限制含 ICU 日數,SPEC §14 2026-09-08)
    base_a = (a["icu_daily"] + a["aux_daily"]) * a["annual_cap_days"]
    # 每段:POL-A 手術最高一項 + 門診 6 次;POL-B 同次麻醉兩項皆最高級 + 慰問 30 日;POL-C 90 日啟發式病房 + 三項限額
    per_a = max(a["surgery"].values()) + a["outpatient_visit"] * a["outpatient_visits_cap"]
    top = max(b["surgery"].values())
    per_b = top + int(Decimal(top) * b["multi_surgery_second_pct"]) + b["solace_daily"] * b["solace_cap_days"]
    heuristic_days = 90
    per_c = c["room_daily_cap"] * heuristic_days + c["surgery_base"] + c["misc_cap"] + c["ambulance_cap"]
    assert AMOUNT_UPPER_BOUND_BASE == {"POL-A": Decimal(base_a), "POL-B": Decimal(0), "POL-C": Decimal(0)}
    assert AMOUNT_UPPER_BOUND_PER_ADMISSION == {
        "POL-A": Decimal(per_a), "POL-B": Decimal(per_b), "POL-C": Decimal(per_c)}
    assert amount_upper_bound("POL-A", 3) == Decimal(base_a + 3 * per_a)  # 三段各一次第三級手術亦在界內(C061)
