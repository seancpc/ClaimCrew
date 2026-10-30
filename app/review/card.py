"""SPEC-r:人工覆核卡片——確定性渲染既有 PipelineOutput + ClaimBundle 欄位,POST 到 Logic Apps HTTP 觸發器。

設計(ADR-008 顧問輸出的讀者):不新增 LLM 呼叫;渲染邏輯在程式(可進 gauntlet),Logic App 只「收 → 寄 → 回應」。
資料安全紅線 3:病患/申請人姓名**不進卡片**(合成資料亦然)。觸發 URL 含 SAS 簽章(sig=),任何訊息一律遮罩。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from app.gate.checks import DENY_FLAGS
from app.schemas.claim import (
    CalcLine,
    ClaimBundle,
    ClaimDecision,
    GateFlag,
    Money,
    PipelineOutput,
    PolicyCitation,
    StrictModel,
)

Poster = Callable[[str, bytes, float], tuple[int, dict[str, str], str]]
POST_TIMEOUT_S = 30.0
RUN_ID_HEADER = "x-ms-workflow-run-id"
_SIG = re.compile(r"(sig=)[^&\s]+")


class AdvisoryNote(StrictModel):
    """Repair 顧問建議摘要(RepairRecord 之人讀子集)。"""

    plan: str
    guard_reason: str
    would_release: bool
    repaired_decision: ClaimDecision | None
    repaired_amount: Money | None
    second_opinion_decision: ClaimDecision | None
    second_opinion_amount: Money | None
    flags_after: list[GateFlag]


class ReviewCard(StrictModel):
    """送交人工覆核的卡片;`html` 為確定性渲染結果(同輸入 → 同位元組)。"""

    claim_id: str
    subject: str
    original_decision: ClaimDecision
    final_decision: ClaimDecision
    approved_amount: Money
    flags: list[GateFlag]
    calculation: list[CalcLine]
    citations: list[PolicyCitation]
    reasoning: str
    confidence: float
    advisory: AdvisoryNote | None
    facts: dict[str, str]
    ungrounded_pct: str | None = None  # SPEC-v 影子欄位:groundedness ungrounded 百分比(無 safety 為 None)
    ungrounded_details: list[str] = []
    undisputed_amount: Money = Decimal(0)  # SPEC-x 顧問欄位(僅 manual_review;無放行權)
    undisputed_calculation: list[CalcLine] = []
    disputed_items: list[str] = []
    review_link: str = ""  # SPEC-am:覆核表單連結(未設 REVIEW_ACTION_URL 為空,卡片位元組不變)
    html: str


@dataclass
class ReviewPost:
    status: str  # sent | error
    http_status: int | None
    run_id: str
    error: str


AUDIT_RATE = 0.10  # SPEC-ae ②:使用者定案(2026-10-07)每輪抽樣 10% 自動核付案


def audit_selected(claim_id: str, run_tag: str, rate: float) -> bool:
    """SPEC-ae ②:放行抽樣稽核——以 sha256(run_tag:claim_id) 決定,同一輪可重現、無亂數狀態。"""
    digest = hashlib.sha256(f"{run_tag}:{claim_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate


def should_review(output: PipelineOutput, *, include_auto_deny: bool = False,
                  audit_rate: float = 0.0, run_tag: str = "") -> bool:
    """預設只送 final manual_review;`include_auto_deny` 另送 P2 程式改派之 deny(原判 approve + deny 類旗標);
    `audit_rate` > 0 時另依抽樣送自動核付案(放行稽核)。"""
    final = output.gate.final_decision
    if final is ClaimDecision.MANUAL_REVIEW:
        return True
    if final is ClaimDecision.APPROVE:
        return audit_selected(output.claim_id, run_tag, audit_rate)
    if include_auto_deny and final is ClaimDecision.DENY and output.gate.original_decision is not ClaimDecision.DENY:
        return any(f.check in DENY_FLAGS for f in output.gate.flags)
    return False


def _facts(bundle: ClaimBundle | None) -> dict[str, str]:
    if bundle is None:  # SPEC-al②:報錯案無抽取結果
        return {}
    dc, rc, app_ = bundle.diagnosis_certificate, bundle.medical_receipt, bundle.claim_application
    stays = ";".join(f"{a.admission_date.value}~{a.discharge_date.value}(ICU {a.icu_days.value})"
                     for a in dc.admissions)
    return {  # 不含 patient_name / applicant_name(資料安全紅線 3)
        "policy_number": app_.policy_number.value,
        "policy_effective_date": app_.policy_effective_date.value,
        "application_date": app_.application_date.value,
        "claimed_amount": str(app_.claimed_amount),
        "hospital": dc.hospital_name.value,
        "diagnosis": dc.diagnosis.value,
        "icd_code": dc.icd_code.value,
        "admissions": stays,
        "treatment_summary": dc.treatment_summary.value,
        "receipt_total": str(rc.total_amount),
        "receipt_items": "、".join(f"{it.item_name} {it.amount}" for it in rc.items),
        "low_confidence_fields": "、".join(bundle.low_confidence_fields),
    }


def _advisory(output: PipelineOutput) -> AdvisoryNote | None:
    r = output.repair
    if r is None or not r.invoked:
        return None
    return AdvisoryNote(plan=r.plan, guard_reason=r.guard_reason, would_release=r.would_release,
                        repaired_decision=r.repaired_decision, repaired_amount=r.repaired_amount,
                        second_opinion_decision=r.second_opinion_decision,
                        second_opinion_amount=r.second_opinion_amount, flags_after=r.flags_after)


REDACTED = "<被保險人>"


def _names(bundle: ClaimBundle | None) -> list[str]:
    if bundle is None:
        return []
    vals = {bundle.diagnosis_certificate.patient_name.value, bundle.medical_receipt.patient_name.value,
            bundle.claim_application.applicant_name.value}
    return sorted((v for v in vals if len(v) >= 2), key=len, reverse=True)  # 長名先換,避免子字串殘留


def _redact(text: str, names: list[str]) -> str:
    for n in names:
        text = text.replace(n, REDACTED)
    return text


REVIEW_ACTION_ENV = "REVIEW_ACTION_URL"


def review_link(action_url: str, run_tag: str, claim_id: str) -> str:
    """SPEC-am:覆核表單連結 = Logic App ① 觸發 URL(含 sig)+ run / case(URL 編碼;表單頁 GET 不寫入)。"""
    return f"{action_url}&{urlencode({'run': run_tag, 'case': claim_id})}"


def build_review_card(output: PipelineOutput, bundle: ClaimBundle | None, *, audit: bool = False,
                      action_url: str | None = None, run_tag: str = "") -> ReviewCard:
    """v8 C019/C020/C054 實證:LLM 的 relevance / reasoning 會寫出病患姓名 → 自由文字一律以已知姓名值遮罩。"""
    names = _names(bundle)
    adj, gate = output.adjudication, output.gate
    citations = [c.model_copy(update={"relevance": _redact(c.relevance, names)}) for c in adj.citations]
    flags = [f.model_copy(update={"detail": _redact(f.detail, names)}) for f in gate.flags]
    facts = {k: _redact(v, names) for k, v in _facts(bundle).items()}
    base: dict[str, Any] = {
        "claim_id": output.claim_id,
        "subject": f"[ClaimCrew {'抽樣稽核' if audit else '人工覆核'}] {output.claim_id} {gate.final_decision.value}"
                   f"(原判 {gate.original_decision.value},旗標 {len(gate.flags)})",
        "original_decision": gate.original_decision, "final_decision": gate.final_decision,
        "approved_amount": adj.approved_amount, "flags": flags, "calculation": adj.calculation,
        "citations": citations, "reasoning": _redact(adj.reasoning, names), "confidence": adj.confidence,
        "advisory": _advisory(output), "facts": facts,
        "undisputed_amount": adj.undisputed_amount, "undisputed_calculation": adj.undisputed_calculation,
        "disputed_items": [_redact(d, names) for d in adj.disputed_items],
        "review_link": review_link(action_url, run_tag, output.claim_id) if action_url else "",
    }
    g = output.safety.groundedness if output.safety is not None else None
    if g is not None:
        base["ungrounded_pct"] = f"{int(g.percentage * 100)}%"
        base["ungrounded_details"] = [_redact(d, names) for d in g.details]
    return ReviewCard(**base, html=render_html(ReviewCard(**base, html="")))


def _e(x: object) -> str:
    return html.escape(str(x), quote=True)


def _rows(rows: list[list[object]]) -> str:
    return "".join("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in r) + "</tr>" for r in rows)


HTML_HEAD = "<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head><body style=\"font-family:sans-serif\">"
HTML_TAIL = "</body></html>"


def render_html(card: ReviewCard) -> str:
    """純字串拼接、全部欄位 html.escape;無時間戳/隨機值,確保位元組穩定。"""
    return HTML_HEAD + card_body(card) + HTML_TAIL


def _undisputed_section(card: ReviewCard) -> str:
    """SPEC-x:人工案可確定金額(保險法施行細則第7條已認定部分先行給付;由人工決定是否給付)。無內容則不輸出。"""
    if not card.undisputed_calculation and not card.disputed_items:
        return ""
    rows = _rows([[ln.clause_no, ln.item, ln.kind.value, ln.quantity, ln.unit_amount, ln.actual_amount, ln.cap]
                  for ln in card.undisputed_calculation]) or "<tr><td colspan=7>(無)</td></tr>"
    disputed = "".join(f"<li>{_e(d)}</li>" for d in card.disputed_items) or "<li>(無)</li>"
    return ("<h3>可確定金額(模型建議,未經確定性金額反查)</h3>"
            f"<p><b>可確定金額</b> {_e(card.undisputed_amount)}</p>"
            "<table border=1 cellpadding=4><tr><th>條號</th><th>項目</th><th>kind</th><th>數量</th><th>單價</th>"
            "<th>實際</th><th>限額</th></tr>" + rows + "</table>"
            "<p><b>爭議 / 待補件項目</b></p><ul>" + disputed + "</ul>")


def card_body(card: ReviewCard) -> str:
    """單張卡片本文(不含 <html> 外殼;摘要信把多張本文串起來)。"""
    flags = _rows([[f.check, f.detail] for f in card.flags]) or "<tr><td colspan=2>(無)</td></tr>"
    calc = _rows([[ln.clause_no, ln.item, ln.kind.value, ln.quantity, ln.unit_amount, ln.actual_amount, ln.deduction,
                   ln.cap, ln.lump_amount, ln.ratio] for ln in card.calculation]) or "<tr><td colspan=10>(無)</td></tr>"
    cits = _rows([[c.policy_id, c.clause_no, c.quote, c.relevance] for c in card.citations]) or \
        "<tr><td colspan=4>(無)</td></tr>"
    facts = _rows([[k, v] for k, v in card.facts.items()])
    a = card.advisory
    if a is None:
        adv = "<p>無顧問建議(未啟用 Repair 或未觸發)。</p>"
    else:
        verdict = "守門全過(would_release=True)" if a.would_release else f"守門退回:{a.guard_reason}"
        adv = ("<table border=1 cellpadding=4>" + _rows([
            ["plan", a.plan], ["結論", verdict],
            ["建議判定 / 金額", f"{a.repaired_decision.value if a.repaired_decision else '—'} / "
                                 f"{a.repaired_amount if a.repaired_amount is not None else '—'}"],
            ["第二意見 判定 / 金額", f"{a.second_opinion_decision.value if a.second_opinion_decision else '—'} / "
                                     f"{a.second_opinion_amount if a.second_opinion_amount is not None else '—'}"],
            ["修補後旗標", "、".join(f.check for f in a.flags_after) or "(無)"],
        ]) + "</table>")
    return (
        f"<h2>{_e(card.subject)}</h2>"
        f"<p><b>最終判定</b> {_e(card.final_decision.value)}(原判 {_e(card.original_decision.value)});"
        f"<b>核付金額</b> {_e(card.approved_amount)};<b>判定信心</b> {_e(card.confidence)}</p>"
        "<h3>Gate 旗標</h3><table border=1 cellpadding=4><tr><th>check</th><th>detail</th></tr>" + flags + "</table>"
        "<h3>計算明細</h3><table border=1 cellpadding=4><tr><th>條號</th><th>項目</th><th>kind</th><th>數量</th>"
        "<th>單價</th><th>實際</th><th>扣除</th><th>限額</th><th>定額</th><th>比例</th></tr>" + calc + "</table>"
        + _undisputed_section(card) +
        f"<h3>判定理由</h3><p>{_e(card.reasoning)}</p>"
        "<h3>條款引用</h3><table border=1 cellpadding=4><tr><th>保單</th><th>條號</th><th>引文</th><th>適用性</th></tr>"
        + cits + "</table>"
        "<h3>AI 顧問建議(Repair,無放行權)</h3>" + adv +
        f"<h3>Groundedness(Content Safety,影子欄位)</h3><p><b>ungrounded</b> {_e(card.ungrounded_pct or '—')}"
        + (("<br>無源頭句:" + _e("、".join(card.ungrounded_details))) if card.ungrounded_details else "") + "</p>"
        "<h3>案件事實(抽取)</h3><table border=1 cellpadding=4>" + facts + "</table>"
        "<p style=\"color:#666\">合成資料;卡片由程式自既有輸出渲染,未含病患/申請人姓名。</p>"
        + (f"<p><a href=\"{_e(card.review_link)}\">填寫覆核</a>(開啟表單頁,按送出才記錄)</p>"
           if card.review_link else "")
    )


def mask_url(url: str) -> str:
    return _SIG.sub(r"\1***", url)


def card_payload(card: ReviewCard) -> dict[str, str]:
    return {"claim_id": card.claim_id, "subject": card.subject, "html": card.html,
            "final_decision": card.final_decision.value, "original_decision": card.original_decision.value,
            "approved_amount": str(Decimal(card.approved_amount))}


def _urllib_post(url: str, body: bytes, timeout: float) -> tuple[int, dict[str, str], str]:
    """回 (狀態碼, 小寫標頭, 回應本文前 2 KB);本文供失敗時顯示 Logic App Response 動作帶回的錯誤。"""
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 目標為使用者設定之 Logic Apps URL
            text = resp.read(2048).decode("utf-8", "replace")
            return int(resp.status), {k.lower(): v for k, v in resp.headers.items()}, text
    except urllib.error.HTTPError as e:
        return int(e.code), {k.lower(): v for k, v in e.headers.items()}, e.read(2048).decode("utf-8", "replace")


def post_card(url: str, card: ReviewCard, *, post: Poster | None = None, timeout: float = POST_TIMEOUT_S) -> ReviewPost:
    """同步 POST;2xx → sent,其餘/例外 → error(不拋出;錯誤訊息遮罩 sig=)。"""
    return _post_bytes(url, json.dumps(card_payload(card), ensure_ascii=False).encode("utf-8"), post, timeout)


def _post_bytes(url: str, body: bytes, post: Poster | None, timeout: float) -> ReviewPost:
    try:
        status, headers, text = (post or _urllib_post)(url, body, timeout)
    except Exception as e:  # noqa: BLE001 — 送卡失敗不得中斷管線
        return ReviewPost(status="error", http_status=None, run_id="", error=mask_url(f"{type(e).__name__}: {e}")[:300])
    run_id = headers.get(RUN_ID_HEADER, "")
    if 200 <= status < 300:
        return ReviewPost(status="sent", http_status=status, run_id=run_id, error="")
    return ReviewPost(status="error", http_status=status, run_id=run_id, error=mask_url(f"HTTP {status} {text}")[:300])


# ====================== SPEC-s 摘要信(一輪一封;Outlook.com 逐案寄送第 6 封即被封鎖) ======================
DIGEST_MAX_BYTES = 1_000_000
DIGEST_FILE = "_review_digest.html"


class ReviewDigest(StrictModel):
    subject: str
    html: str
    claim_ids: list[str]
    size_bytes: int


def build_digest(cards: list[ReviewCard], run_tag: str, part: tuple[int, int] | None = None) -> ReviewDigest:
    """目錄表(案號錨點、原判→最終、金額、旗標數)+ 逐案卡片本文;無時間戳,同輸入同位元組。"""
    suffix = f"({part[0]}/{part[1]})" if part and part[1] > 1 else ""
    subject = f"[ClaimCrew 人工覆核] 摘要 {run_tag} — {len(cards)} 案{suffix}"
    toc = "".join(
        f"<tr class=\"toc\"><td><a href=\"#{_e(c.claim_id)}\">{_e(c.claim_id)}</a></td>"
        f"<td>{_e(c.original_decision.value)} → {_e(c.final_decision.value)}</td><td>{_e(c.approved_amount)}</td>"
        f"<td>{len(c.flags)}</td><td>{_e('、'.join(f.check for f in c.flags))}</td>"
        f"<td>{_e(c.ungrounded_pct or '—')}</td></tr>" for c in cards)
    bodies = "".join(f"<hr><div id=\"{_e(c.claim_id)}\">{card_body(c)}</div>" for c in cards)
    html_ = (HTML_HEAD + f"<h1>{_e(subject)}</h1>"
             "<table border=1 cellpadding=4><tr><th>案號</th><th>原判 → 最終</th><th>核付金額</th><th>旗標數</th>"
             "<th>旗標</th><th>ungrounded</th></tr>" + toc + "</table>" + bodies + HTML_TAIL)
    return ReviewDigest(subject=subject, html=html_, claim_ids=[c.claim_id for c in cards],
                        size_bytes=len(html_.encode("utf-8")))


def split_digest(cards: list[ReviewCard], run_tag: str, max_bytes: int = DIGEST_MAX_BYTES) -> list[ReviewDigest]:
    """依大小貪婪分封(順序保留);單卡超限仍獨立成封(不丟案)。"""
    groups: list[list[ReviewCard]] = []
    cur: list[ReviewCard] = []
    for c in cards:
        if cur and build_digest([*cur, c], run_tag).size_bytes > max_bytes:
            groups.append(cur)
            cur = []
        cur.append(c)
    if cur:
        groups.append(cur)
    n = len(groups)
    return [build_digest(g, run_tag, (i + 1, n)) for i, g in enumerate(groups)]


def digest_payload(d: ReviewDigest) -> dict[str, Any]:
    return {"subject": d.subject, "html": d.html, "claim_ids": d.claim_ids, "case_count": len(d.claim_ids)}


def post_digest(url: str, d: ReviewDigest, *, post: Poster | None = None,
                timeout: float = POST_TIMEOUT_S) -> ReviewPost:
    body = json.dumps(digest_payload(d), ensure_ascii=False).encode("utf-8")
    return _post_bytes(url, body, post, timeout)


def write_digests(digests: list[ReviewDigest], out_dir: Path) -> list[Path]:
    """寄送前先落地(pre-mortem:寄失敗檔案仍在);單封為 _review_digest.html,多封加 _k。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, d in enumerate(digests, 1):
        p = out_dir / (DIGEST_FILE if len(digests) == 1 else DIGEST_FILE.replace(".html", f"_{i}.html"))
        p.write_text(d.html, encoding="utf-8")
        paths.append(p)
    return paths


class DigestCollector:
    """run_pipeline --review 用:逐案收集卡片(符合 should_review 者),跑完一次 flush 寄出摘要。"""

    def __init__(self, run_tag: str, *, post: Poster | None = None, max_bytes: int = DIGEST_MAX_BYTES,
                 include_auto_deny: bool = False, action_url: str | None = None) -> None:
        self.run_tag, self.post, self.max_bytes, self.include_auto_deny = run_tag, post, max_bytes, include_auto_deny
        self.action_url = action_url
        self.cards: list[ReviewCard] = []

    def __call__(self, output: PipelineOutput, bundle: ClaimBundle | None) -> str:
        if not should_review(output, include_auto_deny=self.include_auto_deny):
            return "skipped"
        self.cards.append(build_review_card(output, bundle, action_url=self.action_url, run_tag=self.run_tag))
        return "queued"

    def flush(self, url: str, out_dir: Path) -> list[ReviewPost]:
        if not self.cards:
            return []
        digests = split_digest(self.cards, self.run_tag, self.max_bytes)
        write_digests(digests, out_dir)
        results = [post_digest(url, d, post=self.post) for d in digests]
        self.cards = []
        return results
