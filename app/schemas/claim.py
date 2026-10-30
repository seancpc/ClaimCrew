"""統一 claim schema — 全專案唯一真相源(2026-08-30 方案 B 定案)。

所有欄位定義只出現在此檔;Gate、eval 直接 import 本模組,合成資料生成器經
app/datagen/bundle.py 轉接層在落盤前以本模組驗證,prompt 的欄位說明對齊本檔;禁止另行定義欄位名稱。
JSON Schema 由 to_structured_output_schema() 生成(Azure OpenAI structured
outputs 子集:additionalProperties=false、全欄位 required)。
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

_EXPLICIT_ZERO = frozenset({"", "-", "—", "–", "零", "無", "ゼロ", "0", "00"})  # NFKC 後(－ → -)


def _clean_money(v: Any) -> Any:
    """金額字串前置正規化(確定性層,固定解法 8):去千分位逗號、幣別符號、空白,全形轉半形。

    2026-10-01 C008 第二輪實測:模型把收據「142,500」連逗號照抄 → Decimal 解析失敗;
    同輸入不同輪行為不一致,故不交給 prompt 裁量,由 schema 層吸收。
    """
    if isinstance(v, str):
        s = unicodedata.normalize("NFKC", v)
        s = re.sub(r"[,，\s]|NT\$|NTD|TWD|新臺幣|新台幣|元", "", s)
        # 2026-10-02 v4 C064:模型把「無此金額」寫成「ゼロ」「-」→ 整案 error;明示為零的寫法白名單映 0,其餘仍拒
        if s in _EXPLICIT_ZERO:
            return "0"
        return s
    return v


Money = Annotated[Decimal, BeforeValidator(_clean_money)]


class StrictModel(BaseModel):
    """所有 schema 模型的基底:禁止未知欄位。"""

    model_config = ConfigDict(extra="forbid")


class ClaimDecision(str, Enum):
    """三分類判定(SPEC §4 真值標註)。"""

    APPROVE = "approve"
    DENY = "deny"
    MANUAL_REVIEW = "manual_review"


class ExtractedField(StrictModel):
    """單一抽取欄位值 + 信心分數(Document Intelligence 信心傳遞)。"""

    value: str = Field(description="抽取出的原始字串值;無法抽取時為空字串")
    confidence: float = Field(ge=0.0, le=1.0, description="抽取信心分數 0~1")


class Admission(StrictModel):
    """單一住院期間(同一張診斷書可載多段住院,如出院後再入院)。"""

    admission_date: ExtractedField = Field(description="入院日 YYYY-MM-DD")
    discharge_date: ExtractedField = Field(description="出院日 YYYY-MM-DD")
    icu_days: ExtractedField = Field(description="本段住院之加護病房日數(整數字串);無記載時 \"0\"")


class DiagnosisCertificate(StrictModel):
    """診斷證明書(custom neural 抽取平面欄位,Extraction Agent 整理為 admissions 列表)。"""

    patient_name: ExtractedField
    diagnosis: ExtractedField = Field(description="診斷名稱(可含多診斷,分號分隔)")
    icd_code: ExtractedField = Field(description="ICD-10 代碼;無記載時空字串")
    admissions: list[Admission] = Field(description="住院期間列表,依入院日遞增;純門診(無住院)為空列表")
    treatment_summary: ExtractedField = Field(description="治療處置摘要(含手術名稱)")
    issue_date: ExtractedField = Field(description="開立日期 YYYY-MM-DD")
    hospital_name: ExtractedField


class ReceiptItem(StrictModel):
    """醫療收據單一費用項目。"""

    item_name: str = Field(description="費用項目名稱")
    amount: Money = Field(ge=0, description="金額(新台幣元)")


class MedicalReceipt(StrictModel):
    """醫療收據(prebuilt-receipt 抽取)。"""

    hospital_name: ExtractedField
    patient_name: ExtractedField
    receipt_date: ExtractedField = Field(description="收據日期 YYYY-MM-DD")
    items: list[ReceiptItem] = Field(description="項目化費用清單")
    total_amount: Money = Field(ge=0, description="收據總額(新台幣元)")
    is_copy: bool = Field(description="是否為影本(蓋有「正本已用於申領他保」戳記);正本為 false")


class OtherInsurerPaid(StrictModel):
    """其他保險已給付金額,依 POL-C 第8~11條四個給付項目分桶(第14條:先逐桶扣除再套限額)。

    來源為理賠申請書「已向其他保險申請 / 已獲理賠金額」欄;無法歸入四桶之總額填 unallocated
    (Adjudication 規則:unallocated > 0 → manual_review)。
    """

    room_fee: Money = Field(ge=0, description="他保已給付之病房費用(第8條桶)")
    surgery_fee: Money = Field(ge=0, description="他保已給付之手術費用(第9條桶)")
    misc_fee: Money = Field(ge=0, description="他保已給付之住院雜費(第10條桶)")
    ambulance_fee: Money = Field(ge=0, description="他保已給付之救護車費用(第11條桶)")
    unallocated: Money = Field(ge=0, description="他保給付總額中無法歸入四桶之部分(如日額型保單)")


class ClaimApplication(StrictModel):
    """理賠申請書(prebuilt-read/layout 抽取)。"""

    applicant_name: ExtractedField
    policy_number: ExtractedField = Field(description="保單號碼")
    policy_effective_date: ExtractedField = Field(description="保單生效日 YYYY-MM-DD(等待期起算)")
    claim_type: ExtractedField = Field(description="申請險種/給付類型")
    application_date: ExtractedField = Field(description="申請日期 YYYY-MM-DD")
    claimed_amount: Money = Field(ge=0, description="申請理賠金額(新台幣元)")
    other_insurer_paid: OtherInsurerPaid = Field(description="已向其他保險申請並獲理賠之金額(申請書欄位);未申請則全 0")


class IncidentSource(str, Enum):
    """事故事實來源(決定可信度來源;酒駕/無照/犯罪類事實不會出現在病患自述)。"""

    POLICE_RECORD = "police_record"
    PATIENT_STATEMENT = "patient_statement"
    ACCIDENT_CERTIFICATE = "accident_certificate"


class IncidentStatement(StrictModel):
    """第四類文件:事故證明 / 警方紀錄 / 病患聲明(prebuilt-read 抽取)。

    非應備文件(條款未要求),Intake 不因缺席判缺件;承載酒駕、無照、犯罪等**非醫療事實**,
    使診斷證明書只載醫療事實。缺席時 ClaimBundle.incident_statement 為 null(缺席 ≠ 低信心)。
    """

    source: IncidentSource
    incident_date: ExtractedField = Field(description="事故日期 YYYY-MM-DD")
    statement_text: ExtractedField = Field(description="事故經過原文(逐字)")


class ClaimBundle(StrictModel):
    """一件理賠案的三文件抽取彙整 + 選擇性第四類文件(Extraction Agent 產出)。"""

    claim_id: str = Field(description="案件編號,格式 C###")
    diagnosis_certificate: DiagnosisCertificate
    medical_receipt: MedicalReceipt
    claim_application: ClaimApplication
    incident_statement: IncidentStatement | None = Field(
        description="事故證明/警方紀錄/病患聲明;無此文件時為 null(非缺件)"
    )
    low_confidence_fields: list[str] = Field(
        description="信心分數低於門檻的欄位路徑清單,如 diagnosis_certificate.icd_code"
    )


class IntakeStatus(str, Enum):
    """Intake 完整性判定。"""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class IntakeResult(StrictModel):
    """文件完整性檢查(Intake Agent 產出;incomplete 即中止流程)。"""

    claim_id: str
    status: IntakeStatus
    missing_documents: list[str] = Field(description="缺漏文件名稱清單;complete 時為空")
    clause_basis: list[str] = Field(
        description="應備文件條款與補正條款,如 [\"POL-A 第25條\", \"POL-A 第26條\"];無法歸戶保單時為空列表"
    )


class PolicyCitation(StrictModel):
    """單條條款依據(Policy Agent 產出;「有據不假」紀律)。"""

    policy_id: str = Field(description="保單條款庫文件代號")
    clause_no: str = Field(description="條款編號,如 第十二條 或 12.1")
    quote: str = Field(description="條款原文逐字引用;填 segment 時填空字串,由程式填入")
    relevance: str = Field(description="本條與本案的適用性一句話說明")
    segment: str = Field(
        default="",
        description="依據段落:本文 / 列舉項中文數字 / 末段 / 附錄表格等級(第X級)/ 附錄編號;quote 由程式依此填入"
        "(SPEC-aj;歷史輸出無此欄 → 空字串,quote 原樣)",
    )


class PolicyResult(StrictModel):
    """條款依據清單(Policy Agent 產出;有據不假紀律)。"""

    claim_id: str
    citations: list[PolicyCitation] = Field(description="檢索到的適用條款,每條附原文引用")
    not_found_aspects: list[str] = Field(
        description="檢索不到相關條款的面向,如 給付項目/除外責任/等待期/給付限額/應備文件;禁止腦補"
    )


class CalcKind(str, Enum):
    """計算明細行的算式型別(程式依此加總;SPEC 2026-10-01-b B1)。"""

    PER_UNIT = "per_unit"  # 定額型:quantity × unit_amount(日額、慰問金、門診次數)
    CAPPED_ACTUAL = "capped_actual"  # 限額型:min(max(actual − deduction, 0), cap)(實支實付各桶)
    CAPPED_PER_UNIT = "capped_per_unit"  # 逐日限額:min(max(actual − deduction, 0), quantity × unit_amount)(POL-C 第8條)
    LUMP_SUM = "lump_sum"  # 定額一次給付:lump_amount(手術等級表金額)


Ratio = Annotated[Decimal, BeforeValidator(_clean_money)]


class CalcLine(StrictModel):
    """單行計算明細:LLM 只填算式的材料,子項與總額一律由程式計算(gpt-4o 算術與抄數不可靠,72 案首跑 28/37 金額錯)。

    未用到的欄位填 0;ratio 為乘數(預設 "1";如次高手術 50% 填 "0.5"、非健保身分 65% 填 "0.65")。
    """

    item: str = Field(description="給付項目名稱(如 住院日額 / 病房費 / 手術保險金)")
    clause_no: str = Field(description="依據條號原樣(如 第7條 / 附錄一)")
    kind: CalcKind
    quantity: int = Field(ge=0, description="per_unit 之數量(日數/次數);其他型別填 0")
    unit_amount: Money = Field(ge=0, description="per_unit 之單價(條款金額);其他型別填 \"0\"")
    actual_amount: Money = Field(ge=0, description="capped_actual 之收據實際金額;其他型別填 \"0\"")
    deduction: Money = Field(ge=0, description="capped_actual 之他保已給付扣除額;無則 \"0\"")
    cap: Money = Field(ge=0, description="capped_actual 之條款限額;其他型別填 \"0\"")
    lump_amount: Money = Field(ge=0, description="lump_sum 之條款定額;其他型別填 \"0\"")
    ratio: Ratio = Field(ge=0, le=1, description="乘數,十進位字串;無則 \"1\"")


class AdjudicationDraft(StrictModel):
    """初審判定草稿(Adjudication Agent 的 LLM 輸出):**不含** approved_amount,金額由程式自 calculation 加總。"""

    claim_id: str
    decision: ClaimDecision
    calculation: list[CalcLine] = Field(
        default_factory=list,
        description="核付金額計算明細;approve 時每一給付項目一行,deny / manual_review 時為空列表",
    )
    citations: list[PolicyCitation] = Field(description="判定依據條款,逐條綁定")
    reasoning: str = Field(description="判定理由;每一主張須對應 citations 中的條款")
    confidence: float = Field(ge=0.0, le=1.0, description="判定信心分數 0~1")
    undisputed_calculation: list[CalcLine] = Field(
        default_factory=list,
        description="保留欄位(SPEC-x 顧問欄位已撤回):一律輸出空列表",
    )
    disputed_items: list[str] = Field(
        default_factory=list,
        description="保留欄位(SPEC-x 顧問欄位已撤回):一律輸出空列表",
    )


class AdjudicationResult(AdjudicationDraft):
    """初審判定(Gate 檢核與 eval 的對象)= 草稿 + 程式加總之 approved_amount。"""

    approved_amount: Money = Field(ge=0, description="核付金額(程式自 calculation 加總);拒付/轉人工時為 0")
    undisputed_amount: Money = Field(
        default=Decimal(0), ge=0,
        description="SPEC-x 可確定金額(程式自 undisputed_calculation 加總;僅 manual_review,顧問欄位)",
    )


class GateFlag(StrictModel):
    """Gate 單一檢核旗標。"""

    check: str = Field(description="檢核代號,如 citation_not_found")
    detail: str = Field(description="人讀得懂的說明,含具名對象")


class GateResult(StrictModel):
    """程式側 Gate 分流結果(系統最終輸出的判定以 final_decision 為準)。"""

    claim_id: str
    original_decision: ClaimDecision
    final_decision: ClaimDecision = Field(description="Gate 分流後的最終判定")
    flags: list[GateFlag]
    passed: bool = Field(description="True=無任何旗標,原判定放行")


class DecisionResult(StrictModel):
    """單 agent 骨架之呼叫 2 輸出:條款依據 + 初審判定一次產出(2026-10-01 SPEC 行為 6)。"""

    policy: PolicyResult
    adjudication: AdjudicationDraft


class RepairRecord(StrictModel):
    """SPEC-o Repair agent 執行記錄(非 LLM 輸出 schema;adjudication / gate 一律為最終狀態)。"""

    invoked: bool = Field(description="是否真的呼叫了 Repair agent(skip / 無旗標為 False)")
    plan: str = Field(description="skip | citation_only | repairable")
    rounds: int = Field(ge=0, description="Gate 工具回填輪數")
    flags_before: list[GateFlag] = Field(description="修補前(原 Gate)旗標")
    flags_after: list[GateFlag] = Field(description="修補後重跑 Gate 的旗標;未修補為空")
    guard_reason: str = Field(description="released | skipped | gate_flags | upgrade_blocked | unflagged_new_line | "
                                          "unflagged_line_changed | facts_shrunk | confidence_raised | "
                                          "citation_only_changed | second_opinion_mismatch | exhausted | error")
    pre_repair_decision: ClaimDecision
    pre_repair_amount: Money
    repaired_decision: ClaimDecision | None = None
    repaired_amount: Money | None = None
    second_opinion_decision: ClaimDecision | None = None
    second_opinion_amount: Money | None = None
    error: str = ""
    usage: list[dict[str, int]] = Field(default_factory=list, description="逐呼叫 token(input/output/cached)")
    applied: bool = Field(default=False,
                          description="SPEC-p P5:修補結果是否真的取代了 adjudication/gate(顧問模式恆 False)")
    would_release: bool = Field(default=False, description="守門全過(guard_reason == released);顧問模式下僅記錄")


class GroundednessResult(StrictModel):
    """SPEC-v:Content Safety groundedness 偵測結果(影子欄位;不參與判定)。"""

    ungrounded: bool = Field(description="API ungroundedDetected")
    percentage: Decimal = Field(ge=0, le=1, description="API ungroundedPercentage(0~1)")
    details: list[str] = Field(description="被判無源頭支撐的句子(API ungroundedDetails[].text)")
    truncated: bool = Field(default=False, description="送出前文字或 sources 曾因長度上限被裁切")


class SafetyRecord(StrictModel):
    """SPEC-v:Content Safety 呼叫記錄;`groundedness` / `harm` 任一為 None 代表該支 API 未成功。"""

    invoked: bool
    groundedness: GroundednessResult | None = None
    harm: dict[str, int] | None = Field(default=None, description="四類 severity:Hate / SelfHarm / Sexual / Violence")
    error: str = ""
    elapsed_ms: int = 0


class PipelineOutput(StrictModel):
    """一件理賠案跑完整條管線的落盤/評測單位(eval 讀取此格式);`repair` 為 SPEC-o 選配,舊檔無此欄位。"""

    claim_id: str
    adjudication: AdjudicationResult
    gate: GateResult
    repair: RepairRecord | None = None
    safety: SafetyRecord | None = None  # SPEC-v 影子欄位;無 --safety 時不落欄位


def to_structured_output_schema(model: type[BaseModel]) -> dict[str, Any]:
    """轉為 Azure OpenAI structured outputs 可用的 JSON Schema 子集。

    約束:所有物件 additionalProperties=false、所有欄位列入 required。
    Decimal 欄位在 Pydantic 預設輸出為 anyOf[number, string],structured
    outputs 不接受頂層 anyOf 混型,統一收斂為 string(金額以字串傳遞,
    由程式側轉 Decimal,避免浮點誤差)。
    """
    schema = model.model_json_schema()
    _enforce_subset(schema)
    for def_schema in schema.get("$defs", {}).values():
        _enforce_subset(def_schema)
    return schema


_UNSUPPORTED_KEYWORDS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "default", "format")


def _enforce_subset(node: dict[str, Any]) -> None:
    if "$ref" in node:  # strict 模式:$ref 不得帶兄弟關鍵字(Azure 400 實測 2026-10-01)
        for extra in [k for k in node if k != "$ref"]:
            node.pop(extra)
        return
    for bad in _UNSUPPORTED_KEYWORDS:  # ge/le 等邊界交由 Pydantic 驗證層
        node.pop(bad, None)
    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        node["required"] = list(node["properties"].keys())
        for prop in node["properties"].values():
            _collapse_decimal_anyof(prop)
            _enforce_subset(prop)
    if node.get("type") == "array" and isinstance(node.get("items"), dict):
        _enforce_subset(node["items"])
    for variant in node.get("anyOf", []):
        if isinstance(variant, dict):
            _enforce_subset(variant)


def _collapse_decimal_anyof(prop: dict[str, Any]) -> None:
    variants = prop.get("anyOf")
    if variants and {v.get("type") for v in variants} <= {"number", "string"}:
        prop.pop("anyOf")
        prop["type"] = "string"


def is_nullable_anyof(prop: dict[str, Any]) -> bool:
    """structured outputs 唯一允許之屬性層 anyOf:恰為 [非 null 之單一型別/$ref, {type: null}](optional 欄位)。"""
    variants = prop.get("anyOf")
    if not variants or len(variants) != 2:
        return False
    nulls = [v for v in variants if v.get("type") == "null"]
    return len(nulls) == 1
