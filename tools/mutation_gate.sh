#!/usr/bin/env bash
# Gate 檢核 / 計算明細 / runner 加總 / 事實注入 / 參考表 / 探針 手動變異測試(SPEC-b 12 + SPEC-c 6 + SPEC-d 6 + SPEC-e 2 + SPEC-f 3 + SPEC-g 4 + … + SPEC-n 5 + SPEC-o 9 + R2 2 + SPEC-p 5 = 72 mutants)
# 用法:bash tools/mutation_gate.sh            全套件(kill 歸屬最先失敗的測試)
#       bash tools/mutation_gate.sh property   只跑 -k property 子集(calc M1–M4 + M18;驗不變量本身的強度,CLAUDE.md 固定解法 13)
# 殘留偵測:原字串必須恰好出現一次(固定解法 15);還原用執行前副本而非 git checkout(固定解法 17)
set -uo pipefail
MODE="${1:-full}"
CALC=app/gate/calc.py
CHECKS=app/gate/checks.py
RUNNER=app/pipeline/runner.py
DERIVED=app/pipeline/derived.py
REF=app/gate/reference.py
COST=tools/cost_report.py
PROBE=tools/policy_probe.py
AGENT=tools/agent_probe.py
AGENTSVC=app/pipeline/agent_service.py
REPAIR=app/pipeline/repair.py
CARD=app/review/card.py
DICLIENT=app/pipeline/di_client.py
CLOUDRUN=tools/cloud_run.py
SAFETY=app/pipeline/safety.py
CSCLIENT=app/pipeline/cs_client.py
EVAL=eval/run_eval.py
REGATE=tools/regate.py
BICEPCHK=tools/bicep_check.py
PIDX=app/gate/policy_index.py
DEGRADE=tools/degrade_pdfs.py
RECORD=app/review/record.py
RSTATS=tools/review_stats.py
# SPEC-ak:tools/degrade_pdfs.py 為獨立模組(無他處 import),其測試殺不到 Gate 變異、每次全套多 8 秒(約 200 變異 → +27 分,
# 2026-10-10 gauntlet 因此逾時被砍、M156 殘留)→ 一般變異排除 test_spec_ak.py;M195–M200 以 run_mutant_ak 只跑該檔
# (ak4 全子集 78 次劣化約 20 秒只驗檔案大小,兩者皆排除;第 1 層全套照跑)
PYTEST_ARGS=(tests/ -q --ignore tests/test_spec_ak.py)
AK_ARGS=(tests/test_spec_ak.py -q --deselect tests/test_spec_ak.py::test_ak4_full_subset_within_f0_limits)
[ "$MODE" = "property" ] && PYTEST_ARGS=(tests/ -q -k property --ignore tests/test_spec_ak.py)

check() {  # check <檔> <字串> <次數>
  n=$(grep -cF -- "$2" "$1")
  [ "$n" -eq "$3" ] || { echo "殘留偵測失敗:$1 中 '$2' 出現 $n 次(應為 $3),先 git diff $1"; exit 1; }
}
check "$CALC"   'rounding=ROUND_DOWN' 1
check "$CALC"   'base = min(max(line.actual_amount - line.deduction, Decimal(0)), line.cap)' 1
check "$CALC"   'return base * line.ratio' 1
check "$CHECKS" 'if elapsed < WAITING_PERIOD_DAYS:' 1
check "$DERIVED" 'INJURY_ICD_PREFIXES = ("S", "T")' 1
check "$CHECKS" 'if effective is None or event is None:' 1
check "$CHECKS" 'return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", s))' 1
check "$CHECKS" 'if not result.calculation:' 1
check "$CHECKS" 'if result.decision is not ClaimDecision.APPROVE:' 13  # 等待期/參考表/日數/結構/限額合計/判定推理/提醒型/時效/特定疾病窗口/SPEC-w 等級值/住院段涵蓋各一/SPEC-ae 等待期內已診斷/SPEC-af 合併限額
check "$RUNNER" '    amount = total_amount(lines) if draft.decision is ClaimDecision.APPROVE else Decimal(0)' 1
check "$CALC"   '        key = (cap_group_clause(ln), ln.cap, ln.ratio)' 1
check "$CALC"   '    return "第9條" if clause == "附錄一" and line.kind is CalcKind.CAPPED_ACTUAL else clause' 1
check "$CHECKS" '            groups.setdefault(cap_group_clause(ln), []).append(ln)' 1
check "$CHECKS" '           if ln.kind is CalcKind.CAPPED_ACTUAL and cap_group_clause(ln) == "第9條" and ln.cap != want]' 1
check "$PIDX"   '        return f"第{_INT_TO_CN.get(int(n), n) if n.isdigit() else n}級"' 1
check "$DEGRADE" '    if level not in LEVELS:' 1
check "$CHECKS" '    if len(ratios) < 2:' 1
check "$BICEPCHK" '    if template.get("parameters", {}).get("enableReviewAction", {}).get("defaultValue") is not False:' 1
check "$BICEPCHK" '        if "guid()" not in uri or "triggerFormDataValue" in uri or "queries" in uri:' 1
check "$BICEPCHK" '        if a.get("inputs", {}).get("authentication", {}).get("type") != "ManagedServiceIdentity":' 1
check "$BICEPCHK" '    if any(a.get("type") != "Response" for a in actions):' 1
check "$BICEPCHK" '    if refs == 0 or body.count(f"uriComponent(coalesce({QUERY_REF}") != refs:' 1
check "$BICEPCHK" ' or "/containers/" not in str(roles[0].get("scope", "")):' 1
check "$CARD" '    return f"{action_url}&{urlencode(' 1
check "$CARD" '        + (f"<p><a href=\"{_e(card.review_link)}\">' 1
check "$CARD" '        self.cards.append(build_review_card(output, bundle, action_url=self.action_url, run_tag=self.run_tag))' 1
check "$RECORD" '        if cur is None or r.submitted_at > cur.submitted_at:' 1
check "$RECORD" '        if not d.is_finite() or d < 0:' 1
check "$RECORD" '    note: str = Field(default="", max_length=500)' 1
check "$RSTATS" 'BURST_GAP_S = 5 ' 1
check "$RSTATS" '    hits = {cid for cid, r in latest.items() if any(k in r.user_agent.lower() for k in SCANNER_UA)}' 1
check "$RSTATS" 'if r.decision is not cards[cid].gate.original_decision' 1
check "$RUNNER" '    head = f"{type(exc).__name__}: {exc}".split(RAW_TEXT_MARKER)[0].rstrip()' 1
check "$RUNNER" '        _write_atomic(out_dir / f"{claim_id}.json", output_json(err_out))' 1
check "$RUNNER" '        _review_hook(rec, err_out, None, reviewer)' 1
check "$REGATE" '        if bundle_file.is_file() and not any(f.check == "pipeline_error" for f in out.gate.flags):' 1
check "$CHECKS" '        *_check_ratio_consistency(result, bundle),' 1
check "$CHECKS" '    ratios = {ln.ratio for ln in result.calculation if ln.kind in capped and cap_group_clause(ln) in _POLC_RATIO_GROUPS}' 1
check "$CHECKS" '    capped = (CalcKind.CAPPED_ACTUAL, CalcKind.CAPPED_PER_UNIT)' 1
check "$CHECKS" '_POLC_RATIO_GROUPS = frozenset({"第8條", "第9條", "第10條", "第11條"})' 1
check "$CHECKS" '    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":' 7  # 既有 6 個 POL-C 檢核 + SPEC-al
check "$DEGRADE" '                                              creationDate=FIXED_PDF_DATE, modDate=FIXED_PDF_DATE)' 1
check "$DEGRADE" '    if existing and not args.force:' 1
check "$DEGRADE" '        if not pdfs:' 1
check "$DEGRADE" '    img = _salt_pepper(img, p["noise"], rng)' 1
check "$DEGRADE" '    return random.Random(int(hashlib.sha256(f"{seed_key}|{level}".encode()).hexdigest()[:16], 16))' 1
check "$CHECKS" '    if bundle.claim_application.policy_effective_date.confidence < EXTRACTION_FIELD_CONFIDENCE_THRESHOLD:' 1
check "$CHECKS" '    if len(days) < 2 or len(days) != len(rooms) or any(d is None for d in days):' 1
check "$CHECKS" '    if routes_to_deny(flags, bundle):' 1
check "$REPAIR" '                 would_release=reason == "released", applied=not advisory)' 1
check "$RUNNER" '                                                deployment=decision_deployment)' 1
check "$RUNNER" '            rec.calls.append(_call_record("extraction", r1, t1))' 1
check "$COST"   '    raw = ((Decimal(prompt - cached) * prices["input"] + Decimal(cached) * prices["cached_input"]' 1
check "$COST"   '    if "calls" in row:' 1
check "$AGENTSVC" '        if rounds >= max_rounds:' 1
check "$AGENTSVC" '    fmt = TextResponseFormatJsonSchema(name="DecisionResult", strict=True,' 1
check "$AGENTSVC" '        instructions = instructions + GATE_TOOL_INSTRUCTIONS' 1
check "$AGENTSVC" '            "cached_tokens": int(getattr(details, "cached_tokens", 0) or 0)}' 1
check "$AGENT"  '    out["identical"] = all(out.values())' 1
check "$REPAIR" '    if not flags or any(f.check.startswith(ADVISORY_PREFIXES) for f in flags):' 1
check "$REPAIR" '    if all(f.check.startswith(CITATION_PREFIX) for f in flags):' 1
check "$REPAIR" '    if repaired.decision is ClaimDecision.APPROVE and original.decision is not ClaimDecision.APPROVE:' 1
check "$REPAIR" '                return "unflagged_new_line"' 1
check "$REPAIR" '    if repaired.confidence > original.confidence:' 1
check "$REPAIR" '        if so is None or so.decision != repaired.decision or so.approved_amount != repaired.approved_amount:' 1
check "$REPAIR" '    except (AgentServiceError, openai.OpenAIError, ValueError) as e:' 1
check "$REPAIR" '    if lr.kind != "final" or lr.result is None:' 1
check "$REPAIR" '        if sum(ln["actual_amount"] for ln in new) < sum(ln["actual_amount"] for ln in lines):' 1
check "$CHECKS" '    actual_total = sum((ln.actual_amount for ln in lines), Decimal(0))' 1
check "$CHECKS" '    if actual_total >= receipt_total or actual_total >= cap_total:' 1
check "$CHECKS" '    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":  # SPEC-u 只檢 POL-C' 1
check "$CHECKS" 'PRE_EXISTING_CLAUSE: dict[str, str] = {"POL-A": "第18條", "POL-B": "第16條", "POL-C": "第19條"}' 1
check "$CHECKS" '    if result.decision is not ClaimDecision.DENY:' 2  # 既往症 B + SPEC-ai
check "$CHECKS" '                           r"|既往|(?:定期|長期).{0,10}追蹤|確診於.{0,6}年前|\d+\s*年前")' 1
check "$CLOUDRUN" '"--run-tag", rid, *extra]' 1
check "$REPAIR" '        if sum(ln["deduction"] for ln in new) != sum(ln["deduction"] for ln in lines):' 1
check "$REPAIR" '                      final_decision=ClaimDecision.MANUAL_REVIEW, flags=[*output.gate.flags, flag], passed=False)' 1
check "$PROBE"  '    recall = len(truth_basis & pred_basis) / len(truth_basis) if truth_basis else 0.0' 1
check "$PROBE"  '    valid = sum(1 for c in result.citations if validate_citation(c, index) is None) / n if n else 0.0' 1
check "$PROBE"  "        f\"{runner._prompt('policy')}\\n\\n---\\n\\n\"" 1
check "$DERIVED" 'days = (dis - adm).days + 1 if adm and dis else None' 1
check "$DERIVED" 'gap = (adm - prev_discharge).days if adm and prev_discharge else None' 1
check "$CHECKS"  'counted = {clip(g) for g in group_days} | {total} | {clip(d) for d in days}' 1
check "$REF"     'c_ratios = frozenset({ONE, Decimal(str(c["non_nhi_factor"]))})' 1
check "$REF"     'return [("calc_unknown_clause", f"{tag} 不在給付條款參考表內")]' 1
check "$CALC"    'base = min(max(line.actual_amount - line.deduction, Decimal(0)), Decimal(line.quantity) * line.unit_amount)' 1
check "$CHECKS"  'elif has("第11條") and qty("第11條") != daily:' 1
check "$CHECKS"  'ANNUAL_CAP_DAYS: dict[str, int] = {"POL-A": 180}' 1
check "$CHECKS"  'return d.replace(year=d.year + years, day=28)' 1
check "$CHECKS"  'if facts.is_injury_icd or d is None or not (WAITING_PERIOD_DAYS <= d < special):' 1
check "$REF"     'if lump in rule.half_lumps and line.ratio == ONE:' 1
check "$CHECKS"  '_FRAGMENT_SEPARATORS = re.compile(r"(?:\.\.\.|…+|[。|\n])")' 1
check "$CHECKS"  '        pos = _find(f, text, pos)' 1
check "$CHECKS"  '    cells = _CELL_SENTINEL.join(_normalize(cell) for cell in clause_text.split("|"))' 1
check "$CHECKS"  '    return bool(items) and all(x in rows[m.group(1)] for x in items)' 1
check "$CHECKS"  '    frags = _quote_fragments(_ENUM_MARKER.sub("\n", quote))' 1
check "$CHECKS"  'if cn != own and _find(c.quote, _normalize_cells(cl.text)) >= 0),' 1
check "$CHECKS"  'if total > cap * segments:' 1
check "$CHECKS"  '    pool = [Decimal(lumps[0])] + [Decimal(x) / 2 for x in lumps[1:]]' 1
check "$CHECKS"  '    if len(lumps) < 2 or not lines:' 1
check "$CHECKS"  '    return sorted(lumps, reverse=True)' 1
check "$CHECKS"  '    zero_lines = [ln for ln in result.calculation if compute_line(ln) == 0]' 1
check "$CHECKS"  '        total = sum((max(ln.actual_amount - ln.deduction, Decimal(0)) for ln in lines), Decimal(0))' 2  # cap-sum + SPEC-af 合併限額
check "$DERIVED" 'a.gap_days_after_previous <= merge_gap:' 1
check "$CHECKS"  'segments = max(1, len(derive_facts(bundle).stay_groups))' 1
check "$CHECKS"  '"復健", "救護車", "雜費", "診斷證明"' 1
check "$CHECKS"  'summary = re.sub(r"\s+", "", raw_summary).translate(_OCR_CONFUSABLES)' 1
check "$CHECKS"  'if inpatient != has("第12條") and (inpatient or has("第8條")):' 1
check "$CHECKS"  'r"(?<![不無非未])(應予?拒付|予以拒付|本案拒付|應拒絕給付|不負給付(?:保險金)?之?責任|不予給付|不符合給付)"' 1
check "$CHECKS"  'COVERING_CLAUSES["第9條"] = frozenset({"第9條", "附錄一"})' 1
check "$CHECKS"  '    "第11條": ("救護車",),' 1
check "$CHECKS"  '_policy_code_of(bundle) != "POL-C":  # SPEC-q 只檢 POL-C' 1
check "$RUNNER"  'merge_split_lines: bool = True) -> AdjudicationResult:' 1
check "$CARD"    '    return html.escape(str(x), quote=True)' 1
check "$CARD"    '    if final is ClaimDecision.MANUAL_REVIEW:' 1
check "$CARD"    '    return _SIG.sub(r"\1***", url)' 1
check "$CARD"    '    if 200 <= status < 300:' 1
check "$CARD"    '        text = text.replace(n, REDACTED)' 1
check "$CARD"    '        if cur and build_digest([*cur, c], run_tag).size_bytes > max_bytes:' 1
check "$CARD"    "f\"<td>{_e(c.ungrounded_pct or '—')}</td></tr>\" for c in cards)" 1
check "$CARD"    '        write_digests(digests, out_dir)' 1
check "$CARD"    '        if not should_review(output, include_auto_deny=self.include_auto_deny):' 1
check "$DICLIENT" '        if self._token_provider is not None:' 1
check "$CLOUDRUN" '        if p.is_file():' 1
check "$CLOUDRUN" '    _write_meta(store, prefix, meta)  # ① 先留證據' 1
check "$CLOUDRUN" '        dest = cache_dir / name' 1
check "$CSCLIENT" '            if self._token_provider is not None:' 1
check "$CSCLIENT" '    if len(text) > TEXT_MAX_CHARS:' 1
check "$CSCLIENT" '        if task == "QnA":' 1
check "$CSCLIENT" '        return ShieldResult(user_attack=bool((r.get("userPromptAnalysis") or {}).get("attackDetected", False)),' 1
check "$SAFETY"   '    except Exception as e:  # noqa: BLE001 — 影子欄位不得中斷管線' 1
check "$SAFETY"   '        if labelled not in clauses:' 1
check "$SAFETY"   '    parts = [_redact(adj.reasoning, names)]' 1
check "$SAFETY"   '    text = segment_sentences("\n".join(parts))' 1
check "$SAFETY"   '    return text, [doc, receipt, _derived_source(bundle), *clauses]' 1
check "$SAFETY"   '        labelled = f"{clause.policy_code} {clause.clause_no}({clause.title}):{annotate_numerals(clause.text)}"' 1
check "$RUNNER"   '    exclude = {f for f in ("repair", "safety") if getattr(output, f) is None}' 1
check "$CHECKS" '    if code not in ("POL-A", "POL-C"):' 1
check "$CHECKS" '        if not lines or got == [want]:' 1
check "$CHECKS" 'and ln.cap != want]' 1
check "$CHECKS" '        accepted.add(min(total, ANNUAL_CAP_DAYS[code]))' 1
check "$CHECKS" '            sums[clause] = sums.get(clause, 0) + ln.quantity' 1
check "$CHECKS" '        if len(bundle.diagnosis_certificate.admissions) > 1:' 1
check "$RUNNER" '    manual = draft.decision is ClaimDecision.MANUAL_REVIEW' 1
check "$RUNNER" '                              undisputed_amount=total_amount(undisputed) if undisputed else Decimal(0))' 1
check "$CARD" '        "disputed_items": [_redact(d, names) for d in adj.disputed_items],' 1
check "$CARD" '    if not card.undisputed_calculation and not card.disputed_items:' 1
check "$EVAL" '            und_hit += out.adjudication.undisputed_amount == Decimal(truth["undisputed_amount"])' 1
check "$REGATE" '                out.adjudication.model_dump(exclude={"approved_amount", "undisputed_amount"}))' 1
check "$CHECKS" '    if bundle.medical_receipt.is_copy:' 1
check "$CHECKS" 'if ln.deduction > 0]' 1
check "$CHECKS" '        *_check_deduction_without_copy(result, bundle),' 1
check "$REGATE" '    if outs:  # SPEC-y' 1
check "$CHECKS" '    if not bundle.medical_receipt.is_copy:' 1
check "$CHECKS" 'DEDUCTION_BUCKET_CLAUSE: dict[str, str] = {"room_fee": "第8條", "surgery_fee": "第9條",' 1
check "$CHECKS" '        *_check_deduction_bucket(result, bundle),' 1
check "$CHECKS" '        got[clause] = got.get(clause, Decimal(0)) + ln.deduction' 1
check "$CHECKS" '    if (dates[-1] - dates[0]).days > _REPEAT_SURGERY_GAP_DAYS:' 1
check "$CHECKS" '    if len(names) != 1 or len(dates) < 2 or len(lines) < 2:' 1
check "$CHECKS" '_REPEAT_SURGERY_CLAUSES = _SURGERY_GRADE_CLAUSES | {"第10條"}' 1
check "$CHECKS" '        *_check_surgery_repeat(result, bundle),' 1
check "$CHECKS" 'def _check_surgery_repeat(' 1
check "$CHECKS" '        return _check_polb_single_surgery_value(result, bundle)' 1
check "$CHECKS" '    want = {**{c: full for c in _POLB_FULL_CLAUSES}, _POLB_OUTPATIENT_CLAUSE: full / 2}' 1
check "$CHECKS" '    if len(grades) != 1:' 1
check "$CHECKS" '    if not bundle.medical_receipt.is_copy or unallocated <= 0:' 1
check "$CHECKS" '        *_check_deduction_unallocated(result, bundle),' 1
check "$CHECKS" 'def _check_deduction_unallocated(' 1
check "$CHECKS" '    if facts.days_since_effective < WAITING_PERIOD_DAYS:' 1
check "$CHECKS" '            if d is not None and 0 <= (d - effective).days < WAITING_PERIOD_DAYS]' 1
check "$CHECKS" '    if facts.is_injury_icd or effective is None or facts.days_since_effective is None:' 1
check "$CHECKS" '_PRIOR_DIAGNOSIS = re.compile(r"(\d{4}-\d{2}-\d{2})[^。;;]*?診斷")' 1
check "$CHECKS" '        *_check_prior_diagnosis(result, bundle),' 1
check "$CHECKS" 'def _check_prior_diagnosis(' 1
check "$CHECKS" '_NUMERAL_VARIANTS = str.maketrans({"仟": "千"})' 1
check "$CARD" '        return audit_selected(output.claim_id, run_tag, audit_rate)' 1
check "$CARD" '    digest = hashlib.sha256(f"{run_tag}:{claim_id}".encode()).digest()' 1
check "$CHECKS" '        if not lines or len(lines) >= groups:' 1
check "$CHECKS" '        if total > cap * len(lines):' 1
check "$CHECKS" '        *_check_stay_cap_merged(result, bundle),' 1
check "$CHECKS" 'def _check_stay_cap_merged(' 1
check "$CHECKS" '    if appendix2_match(bundle.diagnosis_certificate.icd_code.value) is False:' 1
check "$CHECKS" '            if letter == lt and lo <= digits[:len(lo)] <= hi:' 1
check "$CHECKS" '    ("E", "08", "13"),' 1
check "$CHECKS" '    ("N", "183", "186"),' 1
check "$CHECKS" '    if not codes:' 1
check "$CHECKS" 'ROUTING_EXEMPT_FIELDS = frozenset({"low_confidence_fields", "diagnosis_certificate.issue_date"})' 1
check "$CHECKS" 'SURGERY_DEFINITION_CLAUSE: dict[str, str] = {"POL-B": "第2條"}' 1
check "$CHECKS" '        *_check_surgery_definition_deny(result, bundle),' 1
check "$CHECKS" '    if clause is None or clause not in {normalize_clause_no(c.clause_no) for c in result.citations}:' 2  # 既往症 + SPEC-ai
check "$CLOUDRUN" '    queue.delete(msg_id, receipt)' 1
check "$CLOUDRUN" 'all(isinstance(c, str) and _CASE_ID.fullmatch(c) for c in ids)' 1
check "$CLOUDRUN" '        if container != INBOX_CONTAINER or not name.startswith(REQUEST_PREFIX) or not name.endswith(".json"):' 1
check "$CLOUDRUN" 'numofmessages=1&' 1
check "$BICEPCHK" '        if scale.get("maxExecutions") != 1:' 1
check "$BICEPCHK" '    for role in FORBIDDEN_ROLES:' 1
check "$BICEPCHK" '    if "fail(" not in guard or "enableReview" not in guard or "enableJobAlert" not in guard:' 1
check "$BICEPCHK" '        if value != EMAIL_GUARD_REF:' 1
check "$PIDX" '        for ln in lines[1:]:' 1
check "$PIDX" '            segs["末段" if seen_enum else "本文"] = ln' 1
check "$PIDX" '    m = _SEG_GRADE_RE.match(s)' 1
check "$PIDX" '    return m.group(1) if m else s' 2  # normalize_policy_id + SPEC-aj normalize_segment
check "$RUNNER" '        out.append(c.model_copy(update={"quote": text}) if text is not None else c)' 1
check "$RUNNER" '    cits = fill_citation_quotes(draft.citations)' 1
check "$CHECKS" '        if normalize_segment(c.segment) not in clause_segments(clause):' 1
check "$CHECKS" '    elif not c.quote.strip():' 1

B_CALC=$(mktemp); B_CHECKS=$(mktemp); B_RUNNER=$(mktemp); B_DERIVED=$(mktemp); B_REF=$(mktemp); B_COST=$(mktemp); B_PROBE=$(mktemp); B_AGENT=$(mktemp); B_AGENTSVC=$(mktemp); B_REPAIR=$(mktemp); B_CARD=$(mktemp); B_DICLIENT=$(mktemp); B_CLOUDRUN=$(mktemp); B_SAFETY=$(mktemp); B_CSCLIENT=$(mktemp); B_EVAL=$(mktemp); B_REGATE=$(mktemp); B_BICEPCHK=$(mktemp); B_PIDX=$(mktemp); B_DEGRADE=$(mktemp); B_RECORD=$(mktemp); B_RSTATS=$(mktemp)
cp "$CALC" "$B_CALC"; cp "$CHECKS" "$B_CHECKS"; cp "$RUNNER" "$B_RUNNER"; cp "$DERIVED" "$B_DERIVED"; cp "$REF" "$B_REF"; cp "$COST" "$B_COST"; cp "$PROBE" "$B_PROBE"; cp "$AGENT" "$B_AGENT"; cp "$AGENTSVC" "$B_AGENTSVC"; cp "$REPAIR" "$B_REPAIR"; cp "$CARD" "$B_CARD"; cp "$DICLIENT" "$B_DICLIENT"; cp "$CLOUDRUN" "$B_CLOUDRUN"; cp "$SAFETY" "$B_SAFETY"; cp "$CSCLIENT" "$B_CSCLIENT"; cp "$EVAL" "$B_EVAL"; cp "$REGATE" "$B_REGATE"; cp "$BICEPCHK" "$B_BICEPCHK"; cp "$PIDX" "$B_PIDX"; cp "$DEGRADE" "$B_DEGRADE"; cp "$RECORD" "$B_RECORD"; cp "$RSTATS" "$B_RSTATS"
restore() { cp "$B_CALC" "$CALC"; cp "$B_CHECKS" "$CHECKS"; cp "$B_RUNNER" "$RUNNER"; cp "$B_DERIVED" "$DERIVED"; cp "$B_REF" "$REF"; cp "$B_COST" "$COST"; cp "$B_PROBE" "$PROBE"; cp "$B_AGENT" "$AGENT"; cp "$B_AGENTSVC" "$AGENTSVC"; cp "$B_REPAIR" "$REPAIR"; cp "$B_CARD" "$CARD"; cp "$B_DICLIENT" "$DICLIENT"; cp "$B_CLOUDRUN" "$CLOUDRUN"; cp "$B_SAFETY" "$SAFETY"; cp "$B_CSCLIENT" "$CSCLIENT"; cp "$B_EVAL" "$EVAL"; cp "$B_REGATE" "$REGATE"; cp "$B_BICEPCHK" "$BICEPCHK"; cp "$B_PIDX" "$PIDX"; cp "$B_DEGRADE" "$DEGRADE"; cp "$B_RECORD" "$RECORD"; cp "$B_RSTATS" "$RSTATS"; }
BACKUPS=("$B_CALC" "$B_CHECKS" "$B_RUNNER" "$B_DERIVED" "$B_REF" "$B_COST" "$B_PROBE" "$B_AGENT" "$B_AGENTSVC" "$B_REPAIR" "$B_CARD" "$B_DICLIENT" "$B_CLOUDRUN" "$B_SAFETY" "$B_CSCLIENT" "$B_EVAL" "$B_REGATE" "$B_BICEPCHK" "$B_PIDX" "$B_DEGRADE" "$B_RECORD" "$B_RSTATS")
# trap 只還原、不刪副本:TERM 與 EXIT 先後觸發時第二次仍有副本可還原(D4,2026-10-04 M74 殘留);副本在結尾 cmp 通過後才刪
trap 'restore' EXIT INT TERM HUP
# SIGTERM(Bash 工具逾時)會讓 Python 跳過 finally;本腳本為 bash,trap 涵蓋 TERM(固定解法 15)

killed=0; survived=0
run_mutant() {  # run_mutant <名稱> <檔> <sed 表達式>
  sed -i "$3" "$2"
  if python3 -m pytest "${PYTEST_ARGS[@]}" >/dev/null 2>&1; then echo "SURVIVED: $1"; survived=$((survived+1))
  else echo "KILLED:   $1"; killed=$((killed+1)); fi
  restore
}
run_mutant_ak() {  # SPEC-ak 劣化工具專用:只跑 test_spec_ak.py(見 PYTEST_ARGS 註解)
  sed -i "$3" "$2"
  if python3 -m pytest "${AK_ARGS[@]}" >/dev/null 2>&1; then echo "SURVIVED: $1"; survived=$((survived+1))
  else echo "KILLED:   $1"; killed=$((killed+1)); fi
  restore
}
run_mutant "M1 總額向上取整"              "$CALC"   's/rounding=ROUND_DOWN/rounding=ROUND_UP/'
run_mutant "M2 扣除後不以 0 封底"          "$CALC"   's/base = min(max(line.actual_amount - line.deduction, Decimal(0)), line.cap)/base = min(line.actual_amount - line.deduction, line.cap)/'
run_mutant "M3 限額型不套 cap"             "$CALC"   's/base = min(max(line.actual_amount - line.deduction, Decimal(0)), line.cap)/base = max(line.actual_amount - line.deduction, Decimal(0))/'
run_mutant "M4 忽略 ratio"                 "$CALC"   's/return base \* line.ratio/return base/'
if [ "$MODE" = "full" ]; then
run_mutant "M5 等待期邊界 < 改 <="         "$CHECKS" 's/if elapsed < WAITING_PERIOD_DAYS:/if elapsed <= WAITING_PERIOD_DAYS:/'
run_mutant "M6 傷害章漏 T"                 "$DERIVED" 's/INJURY_ICD_PREFIXES = ("S", "T")/INJURY_ICD_PREFIXES = ("S",)/'
run_mutant "M7 日期解析 or 改 and"          "$CHECKS" 's/if effective is None or event is None:/if effective is None and event is None:/'
run_mutant "M8 引文正規化退回只去空白"      "$CHECKS" 's/return re.sub(r"\[\\W_\]+", "", unicodedata.normalize("NFKC", s))/return re.sub(r"\\s+", "", s)/'
run_mutant "M9 引文正規化回空字串(萬物相符)" "$CHECKS" 's/return re.sub(r"\[\\W_\]+", "", unicodedata.normalize("NFKC", s))/return ""/'
run_mutant "M10 核付不查明細"               "$CHECKS" 's/if not result.calculation:/if False and not result.calculation:/'
run_mutant "M11 等待期檢核不分判定"          "$CHECKS" '/def _check_waiting_period/,/return \[\]/ s/if result.decision is not ClaimDecision.APPROVE:/if False:/'
run_mutant "M12 deny 也照明細加總"          "$RUNNER" 's/    amount = total_amount(lines) if draft.decision is ClaimDecision.APPROVE else Decimal(0)/    amount = total_amount(lines) if True else Decimal(0)/'
# --- SPEC 2026-10-01-c ---
run_mutant "M13 住院日數不含出院日"          "$DERIVED" 's/days = (dis - adm).days + 1 if adm and dis else None/days = (dis - adm).days if adm and dis else None/'
run_mutant "M14 段落間隔含出院日"            "$DERIVED" 's/gap = (adm - prev_discharge).days if adm and prev_discharge else None/gap = (adm - prev_discharge).days + 1 if adm and prev_discharge else None/'
run_mutant "M15 候選集漏 90 日截斷"          "$CHECKS"  's/counted = {clip(g) for g in group_days} | {total} | {clip(d) for d in days}/counted = set(group_days) | {total} | set(days)/'
run_mutant "M16 參考表漏非健保 0.65"         "$REF"     's/c_ratios = frozenset({ONE, Decimal(str(c\["non_nhi_factor"\]))})/c_ratios = frozenset({ONE})/'
run_mutant "M17 表外條號放行"                "$REF"     's/return \[("calc_unknown_clause", f"{tag} 不在給付條款參考表內")\]/return []/'
# --- SPEC 2026-10-01-d ---
run_mutant "M19 配對漏算 ICU 日數"           "$CHECKS"  's/elif has("第11條") and qty("第11條") != daily:/elif has("第11條") and qty("第11條") != qty("第7條"):/'
run_mutant "M20 年度上限 180→200"            "$CHECKS"  's/ANNUAL_CAP_DAYS: dict\[str, int\] = {"POL-A": 180}/ANNUAL_CAP_DAYS: dict[str, int] = {"POL-A": 200}/'
run_mutant "M21 候選集漏各段日數"            "$CHECKS"  's/counted = {clip(g) for g in group_days} | {total} | {clip(d) for d in days}/counted = {clip(g) for g in group_days} | {total}/'
run_mutant "M22 閏日到期退錯日"              "$CHECKS"  's/return d.replace(year=d.year + years, day=28)/return d.replace(year=d.year + years, day=1)/'
run_mutant "M23 特定疾病窗口未排除 <30"       "$CHECKS"  's/if facts.is_injury_icd or d is None or not (WAITING_PERIOD_DAYS <= d < special):/if facts.is_injury_icd or d is None or not (d < special):/'
run_mutant "M24 折半表示不限 ratio 1"         "$REF"     's/if lump in rule.half_lumps and line.ratio == ONE:/if lump in rule.half_lumps:/'
# --- SPEC 2026-10-01-e ---
run_mutant "M25 引文分隔符加入冒號"           "$CHECKS"  's/_FRAGMENT_SEPARATORS = re.compile(r"(?:\\.\\.\\.|…+|\[。|\\n\])")/_FRAGMENT_SEPARATORS = re.compile(r"(?:\\.\\.\\.|…+|[。|\\n::])")/'
run_mutant "M26 片段不要求有序"              "$CHECKS"  's/        pos = _find(f, text, pos)/        pos = _find(f, text, 0)/'
# --- SPEC 2026-10-02-f ---
run_mutant "M27 限額合計不乘段數"            "$CHECKS"  's/if total > cap \* segments:/if total > cap:/'
run_mutant "M28 慰問金配對只檢住院方向"       "$CHECKS"  's/if inpatient != has("第12條") and (inpatient or has("第8條")):/if inpatient and not has("第12條"):/'
run_mutant "M29 拒付語正則去掉否定 lookbehind" "$CHECKS"  's/r"(?<!\[不無非未\])(應予?拒付/r"((應予?拒付/'
# --- SPEC 2026-10-02-g ---
run_mutant "M30 分組不含第 14 日"            "$DERIVED" 's/a.gap_days_after_previous <= merge_gap:/a.gap_days_after_previous < merge_gap:/'
run_mutant "M31 限額組數退回段數"            "$CHECKS"  's/segments = max(1, len(derive_facts(bundle).stay_groups))/segments = max(1, len(bundle.diagnosis_certificate.admissions))/'
run_mutant "M32 收據分類白名單漏「雜費」"     "$CHECKS"  's/"復健", "救護車", "雜費", "診斷證明"/"復健", "救護車", "診斷證明"/'
run_mutant "M34 R2 項目不限同一列"            "$CHECKS"  's/    return bool(items) and all(x in rows\[m.group(1)\] for x in items)/    return bool(items) and all(any(x in r for r in rows.values()) for x in items)/'
run_mutant "M35 R2 允許空項目"                "$CHECKS"  's/    return bool(items) and all(x in rows\[m.group(1)\] for x in items)/    return all(x in rows[m.group(1)] for x in items)/'
run_mutant "M36 「:」加進片段分隔符"          "$CHECKS"  's/_FRAGMENT_SEPARATORS = re.compile(r"(?:\\.\\.\\.|…+|\[。|\\n\])")/_FRAGMENT_SEPARATORS = re.compile(r"(?:\\.\\.\\.|…+|[。|\\n:])")/'
run_mutant "M37 表格儲存格不加哨兵"           "$CHECKS"  's/    cells = _CELL_SENTINEL.join(_normalize(cell) for cell in clause_text.split("|"))/    cells = "".join(_normalize(cell) for cell in clause_text.split("|"))/'
run_mutant "M38 R1 不剝引文側標記"            "$CHECKS"  's/    frags = _quote_fragments(_ENUM_MARKER.sub("\\n", quote))/    frags = _quote_fragments(quote)/'
run_mutant "M39 誤植回查不加哨兵、不排除本條"     "$CHECKS"  's/if cn != own and _find(c.quote, _normalize_cells(cl.text)) >= 0),/if _normalize(c.quote) in _normalize(cl.text)),/'
run_mutant "M40 限額合計退回比 ratio 後金額"   "$CHECKS"  '/def _check_cap_sum(/,/return flags/s/        total = sum((max(ln.actual_amount - ln.deduction, Decimal(0)) for ln in lines), Decimal(0))/        total = sum((max(ln.actual_amount - ln.deduction, Decimal(0)) * ln.ratio for ln in lines), Decimal(0))/'
run_mutant "M41 零值行檢核永不命中"           "$CHECKS"  's/    zero_lines = \[ln for ln in result.calculation if compute_line(ln) == 0\]/    zero_lines = [ln for ln in result.calculation if compute_line(ln) < 0]/'
run_mutant "M42 判定呼叫誤用抽取部署"           "$RUNNER"  's/                                                deployment=decision_deployment)/                                                deployment=extraction_deployment)/'
run_mutant "M43 cached 不自 prompt 扣除(重複計價)" "$COST"    's/    raw = ((Decimal(prompt - cached) \* prices\["input"\] + Decimal(cached) \* prices\["cached_input"\]/    raw = ((Decimal(prompt) * prices["input"] + Decimal(cached) * prices["cached_input"]/'
run_mutant "M44 舊格式回退失效"                 "$COST"    's/    if "calls" in row:/    if True:/'
run_mutant "M45 抽取呼叫不記帳"                 "$RUNNER"  's/            rec.calls.append(_call_record("extraction", r1, t1))/            pass/'
run_mutant "M46 允許集漏掉 0.5 項(其餘全額)"     "$CHECKS"  's/    pool = \[Decimal(lumps\[0\])\] + \[Decimal(x) \/ 2 for x in lumps\[1:\]\]/    pool = [Decimal(lumps[0])] + [Decimal(x) for x in lumps[1:]]/'
run_mutant "M47 門檻 ≥2 改 ≥1"                   "$CHECKS"  's/    if len(lumps) < 2 or not lines:/    if len(lumps) < 1 or not lines:/'
run_mutant "M48 等級排序取小為最高"              "$CHECKS"  's/    return sorted(lumps, reverse=True)/    return sorted(lumps)/'
run_mutant "M49 探針 recall 分母改引用數"       "$PROBE"   's/    recall = len(truth_basis \& pred_basis) \/ len(truth_basis) if truth_basis else 0.0/    recall = len(truth_basis \& pred_basis) \/ len(pred_basis) if pred_basis else 0.0/'
run_mutant "M50 探針 valid 不過 validate_citation"  "$PROBE"   's/    valid = sum(1 for c in result.citations if validate_citation(c, index) is None) \/ n if n else 0.0/    valid = 1.0 if n else 0.0/'
run_mutant "M51 探針 system 誤含 adjudication.md"  "$PROBE"   "s/        f\"{runner._prompt('policy')}\\\\n\\\\n---\\\\n\\\\n\"/        f\"{runner._prompt('policy')}\\\\n\\\\n{runner._prompt('adjudication')}\\\\n\\\\n---\\\\n\\\\n\"/"
run_mutant "M52 agent 工具迴圈無上限"          "$AGENTSVC" 's/        if rounds >= max_rounds:/        if False:/'
run_mutant "M53 agent 輸出 schema 不 strict"   "$AGENTSVC" 's/    fmt = TextResponseFormatJsonSchema(name="DecisionResult", strict=True,/    fmt = TextResponseFormatJsonSchema(name="DecisionResult", strict=False,/'
run_mutant "M54 gate 模式不附工具說明"          "$AGENTSVC" 's/        instructions = instructions + GATE_TOOL_INSTRUCTIONS/        instructions = instructions/'
run_mutant "M55 usage 缺 details 即炸"          "$AGENTSVC" 's/            "cached_tokens": int(getattr(details, "cached_tokens", 0) or 0)}/            "cached_tokens": int(details.cached_tokens)}/'
run_mutant "M56 比對忽略引文"                   "$AGENT"   's/    out\["identical"\] = all(out.values())/    out["citations"] = True; out["identical"] = all(out.values())/'
run_mutant "M57 Repair 不排除提醒型旗標"       "$REPAIR"  's/    if not flags or any(f.check.startswith(ADVISORY_PREFIXES) for f in flags):/    if not flags:/'
run_mutant "M58 混合旗標誤判為純引文"          "$REPAIR"  's/    if all(f.check.startswith(CITATION_PREFIX) for f in flags):/    if any(f.check.startswith(CITATION_PREFIX) for f in flags):/'
run_mutant "M59 允許升級為核付"                "$REPAIR"  's/    if repaired.decision is ClaimDecision.APPROVE and original.decision is not ClaimDecision.APPROVE:/    if False:/'
run_mutant "M60 未點名新增行放行"              "$REPAIR"  's/                return "unflagged_new_line"/                pass/'
run_mutant "M61 confidence 相等也擋"           "$REPAIR"  's/    if repaired.confidence > original.confidence:/    if repaired.confidence >= original.confidence:/'
run_mutant "M62 第二意見不比金額"              "$REPAIR"  's/        if so is None or so.decision != repaired.decision or so.approved_amount != repaired.approved_amount:/        if so is None or so.decision != repaired.decision:/'
run_mutant "M63 openai 例外不 fail-closed"     "$REPAIR"  's/    except (AgentServiceError, openai.OpenAIError, ValueError) as e:/    except (AgentServiceError, ValueError) as e:/'
run_mutant "M64 守門退回不掛理由旗標"          "$REPAIR"  's/                      final_decision=ClaimDecision.MANUAL_REVIEW, flags=\[\*output.gate.flags, flag\], passed=False)/                      final_decision=ClaimDecision.MANUAL_REVIEW, flags=[*output.gate.flags], passed=False)/'
run_mutant "M65 無 --repair 仍落 repair: null"  "$RUNNER"  's/    exclude = {f for f in ("repair", "safety") if getattr(output, f) is None}/    exclude = {f for f in ("safety",) if getattr(output, f) is None}/'
run_mutant "M66 G7 事實縮水不擋"                "$REPAIR"  's/        if sum(ln\["actual_amount"\] for ln in new) < sum(ln\["actual_amount"\] for ln in lines):/        if False:/'
run_mutant "M67 G7 不比他保扣除額"              "$REPAIR"  's/        if sum(ln\["deduction"\] for ln in new) != sum(ln\["deduction"\] for ln in lines):/        if False:/'
run_mutant "M68 合併鍵忽略 cap"                "$CALC"    's/        key = (cap_group_clause(ln), ln.cap, ln.ratio)/        key = (cap_group_clause(ln), ln.ratio)/'
run_mutant "M69 低信心生效日仍自動拒付"        "$CHECKS"  's/    if bundle.claim_application.policy_effective_date.confidence < EXTRACTION_FIELD_CONFIDENCE_THRESHOLD:/    if False:/'
run_mutant "M70 分段限額不限段數相等"          "$CHECKS"  's/    if len(days) < 2 or len(days) != len(rooms) or any(d is None for d in days):/    if any(d is None for d in days):/'
run_mutant "M71 deny 類旗標不改派 deny"        "$CHECKS"  's/    if routes_to_deny(flags, bundle):/    if False:/'
run_mutant "M73 附錄一不算覆蓋第9條"            "$CHECKS"  's/COVERING_CLAUSES\["第9條"\] = frozenset({"第9條", "附錄一"})/COVERING_CLAUSES["第9條"] = frozenset({"第9條"})/'
run_mutant "M74 負面列舉也要求覆蓋"              "$CHECKS"  's/    "第11條": ("救護車",),/    "第11條": ("救護車", "診斷證明", "掛號"),/'
run_mutant "M75 POL-A 也檢收據覆蓋"              "$CHECKS"  's/    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":  # SPEC-q 只檢 POL-C/    if result.decision is not ClaimDecision.APPROVE:  # SPEC-q 只檢 POL-C/'
run_mutant "M76 合併預設關(SPEC-q 翻轉失效)"      "$RUNNER"  's/                          merge_split_lines: bool = True) -> AdjudicationResult:/                          merge_split_lines: bool = False) -> AdjudicationResult:/'
run_mutant "M77 卡片不逃逸 HTML"                "$CARD"    's/    return html.escape(str(x), quote=True)/    return str(x)/'
run_mutant "M78 送卡範圍含 approve"              "$CARD"    's/    if final is ClaimDecision.MANUAL_REVIEW:/    if final is not ClaimDecision.DENY:/'
run_mutant "M79 URL 不遮 sig"                    "$CARD"    's/    return _SIG.sub(r"\\1\*\*\*", url)/    return url/'
run_mutant "M80 非 2xx 仍記 sent"                "$CARD"    's/    if 200 <= status < 300:/    if True:/'
run_mutant "M81 姓名不遮罩"                      "$CARD"    's/        text = text.replace(n, REDACTED)/        text = text/'
run_mutant "M82 分封忽略 max_bytes"              "$CARD"    's/        if cur and build_digest(\[\*cur, c\], run_tag).size_bytes > max_bytes:/        if False:/'
run_mutant "M83 目錄少列最後一筆"                "$CARD"    "s/{_e(c.ungrounded_pct or '—')}<\/td><\/tr>\" for c in cards)/{_e(c.ungrounded_pct or '—')}<\/td><\/tr>\" for c in cards[:-1])/"
run_mutant "M84 寄送前不落地 HTML"              "$CARD"    's/        write_digests(digests, out_dir)/        pass/'
run_mutant "M85 收集器對 approve 也 queued"      "$CARD"    's/        if not should_review(output, include_auto_deny=self.include_auto_deny):/        if False:/'
run_mutant "M86 entra 模式仍送金鑰標頭"          "$DICLIENT" 's/        if self._token_provider is not None:/        if False:/'
run_mutant "M87 上傳漏掉底線開頭檔(_run.jsonl)" "$CLOUDRUN" 's/        if p.is_file():/        if p.is_file() and not p.name.startswith("_"):/'
run_mutant "M88 run_meta 不先寫"                 "$CLOUDRUN" 's/    _write_meta(store, prefix, meta)  # ① 先留證據/    pass  # ① 先留證據/'
run_mutant "M89 拉快取丟掉模型子目錄"            "$CLOUDRUN" 's/        dest = cache_dir \/ name/        dest = cache_dir \/ Path(name).name/'
run_mutant "M90 A′ 用 ratio 後金額比較"           "$CHECKS"  's/    actual_total = sum((ln.actual_amount for ln in lines), Decimal(0))/    actual_total = sum((ln.actual_amount * ln.ratio for ln in lines), Decimal(0))/'
run_mutant "M91 A′ 限額綁住也旗標"               "$CHECKS"  's/    if actual_total >= receipt_total or actual_total >= cap_total:/    if actual_total >= receipt_total:/'
run_mutant "M92 A′ POL-A 也檢"                   "$CHECKS"  's/    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":  # SPEC-u 只檢 POL-C/    if result.decision is not ClaimDecision.APPROVE:  # SPEC-u 只檢 POL-C/'
run_mutant "M93 B 條號表漏 POL-C"                "$CHECKS"  's/"POL-C": "第19條"}/"POL-C": "第99條"}/'
run_mutant "M94 B 檢 approve 而非 deny"          "$CHECKS"  '/def _check_pre_existing_deny(/,/return \[GateFlag/s/    if result.decision is not ClaimDecision.DENY:/    if result.decision is ClaimDecision.DENY:/'
run_mutant "M95 既往 regex 擴充段刪除"            "$CHECKS"  's/r"|既往|.*年前")/r"")/'
run_mutant "M96 cloud_run 不傳 run-tag"          "$CLOUDRUN" 's/"--run-tag", rid, \*extra\]/*extra]/'
run_mutant "M97 CS entra 仍送金鑰標頭"            "$CSCLIENT" 's/            if self._token_provider is not None:/            if False:/'
run_mutant "M98 safety 例外中斷管線"              "$SAFETY"   's/    except Exception as e:  # noqa: BLE001 — 影子欄位不得中斷管線/    except ValueError as e:  # noqa: BLE001 — 影子欄位不得中斷管線/'
run_mutant "M99 sources 不含引用條款"             "$SAFETY"   's/        if labelled not in clauses:/        if False:/'
run_mutant "M100 送出文字不遮罩姓名"              "$SAFETY"   's/    parts = \[_redact(adj.reasoning, names)\]/    parts = [adj.reasoning]/'
run_mutant "M101 超長文字不截斷"                  "$CSCLIENT" 's/    if len(text) > TEXT_MAX_CHARS:/    if False:/'
run_mutant "M102 選配欄位 None 仍落檔"            "$RUNNER"   's/    exclude = {f for f in ("repair", "safety") if getattr(output, f) is None}/    exclude = set()/'
run_mutant "M103 中文句界不正規化(整段一塊)"      "$SAFETY"   's/    text = segment_sentences("\\n".join(parts))/    text = "\\n".join(parts)/'
run_mutant "M104 sources 漏推導事實"              "$SAFETY"   's/    return text, \[doc, receipt, _derived_source(bundle), \*clauses\]/    return text, [doc, receipt, *clauses]/'
run_mutant "M105 條款 source 無標籤不旁註數字"    "$SAFETY"   's/        labelled = f"{clause.policy_code} {clause.clause_no}({clause.title}):{annotate_numerals(clause.text)}"/        labelled = clause.text/'
run_mutant "M106 QnA 不送 query"                  "$CSCLIENT" 's/        if task == "QnA":/        if False:/'
run_mutant "M107 Prompt Shields 忽略 userPrompt 判定" "$CSCLIENT" 's/        return ShieldResult(user_attack=bool((r.get("userPromptAnalysis") or {}).get("attackDetected", False)),/        return ShieldResult(user_attack=False,/'
run_mutant "M108 W1 POL-A 不檢"                    "$CHECKS"  's/    if code not in ("POL-A", "POL-C"):/    if code not in ("POL-C",):/'
run_mutant "M109 W1 多條定額只要含最高即過"        "$CHECKS"  's/        if not lines or got == \[want\]:/        if not lines or want in got:/'
run_mutant "M110 W2 只擋超限不擋低填"              "$CHECKS"  's/and ln.cap != want\]/and ln.cap > want]/'
run_mutant "M111 W3 不接受年度上限"                "$CHECKS"  's/        accepted.add(min(total, ANNUAL_CAP_DAYS\[code\]))/        pass/'
run_mutant "M112 W3 單段亦檢"                      "$CHECKS"  's/    if len(days) < 2:$/    if len(days) < 1:/'
run_mutant "M113 W3 取單行最大值而非合計"          "$CHECKS"  's/            sums\[clause\] = sums.get(clause, 0) + ln.quantity/            sums[clause] = max(sums.get(clause, 0), ln.quantity)/'
run_mutant "M114 W1 多段住院也檢"                  "$CHECKS"  's/        if len(bundle.diagnosis_certificate.admissions) > 1:/        if False:/'
run_mutant "M115 X3 非 manual 不清空顧問欄位"      "$RUNNER"  's/    manual = draft.decision is ClaimDecision.MANUAL_REVIEW/    manual = True/'
run_mutant "M116 X2 可確定金額不加總"              "$RUNNER"  's/                              undisputed_amount=total_amount(undisputed) if undisputed else Decimal(0))/                              undisputed_amount=Decimal(0))/'
run_mutant "M117 X8 爭議項不遮罩姓名"              "$CARD"    's/        "disputed_items": \[_redact(d, names) for d in adj.disputed_items\],/        "disputed_items": list(adj.disputed_items),/'
run_mutant "M118 X8 無顧問欄位仍顯示段落"          "$CARD"    's/    if not card.undisputed_calculation and not card.disputed_items:/    if False:/'
run_mutant "M119 X7 可確定金額吻合恆真"            "$EVAL"    's/            und_hit += out.adjudication.undisputed_amount == Decimal(truth\["undisputed_amount"\])/            und_hit += 1/'
run_mutant "M120 X5 regate 不排除可確定金額"       "$REGATE"  's/                out.adjudication.model_dump(exclude={"approved_amount", "undisputed_amount"}))/                out.adjudication.model_dump(exclude={"approved_amount"}))/'
run_mutant "M121 Y3 影本也旗標"                    "$CHECKS"  's/    if bundle.medical_receipt.is_copy:/    if False:/'
run_mutant "M122 Y3 扣除額 0 也旗標"               "$CHECKS"  's/if ln.deduction > 0\]/if ln.deduction >= 0]/'
run_mutant "M123 Y3 run_gate 未掛新檢核"           "$CHECKS"  's/        \*_check_deduction_without_copy(result, bundle),/        *[],/'
run_mutant "M124 Y5 regate 缺件不評分"             "$REGATE"  's/    if outs:  # SPEC-y/    if False:  # SPEC-y/'
run_mutant "M125 Z 正本也檢落桶"                  "$CHECKS"  's/    if not bundle.medical_receipt.is_copy:/    if False:/'
run_mutant "M126 Z 病房桶對錯條"                   "$CHECKS"  's/{"room_fee": "第8條", "surgery_fee": "第9條",/{"room_fee": "第10條", "surgery_fee": "第9條",/'
run_mutant "M127 Z run_gate 未掛落桶檢核"          "$CHECKS"  's/        \*_check_deduction_bucket(result, bundle),/        *[],/'
run_mutant "M128 Z 同條多行扣除改為覆蓋"           "$CHECKS"  's/        got\[clause\] = got.get(clause, Decimal(0)) + ln.deduction/        got[clause] = ln.deduction/'
run_mutant "M129 AA 十四日改為不含第14日"           "$CHECKS"  's/    if (dates\[-1\] - dates\[0\]).days > _REPEAT_SURGERY_GAP_DAYS:/    if (dates[-1] - dates[0]).days >= _REPEAT_SURGERY_GAP_DAYS:/'
run_mutant "M130 AA 多手術名也檢"                 "$CHECKS"  's/    if len(names) != 1 or len(dates) < 2 or len(lines) < 2:/    if len(names) < 1 or len(dates) < 2 or len(lines) < 2:/'
run_mutant "M131 AA 單一日期也檢"                 "$CHECKS"  's/    if len(names) != 1 or len(dates) < 2 or len(lines) < 2:/    if len(names) != 1 or len(dates) < 1 or len(lines) < 2:/'
run_mutant "M132 AA 單行也檢"                     "$CHECKS"  's/    if len(names) != 1 or len(dates) < 2 or len(lines) < 2:/    if len(names) != 1 or len(dates) < 2 or len(lines) < 1:/'
run_mutant "M133 AA 不計第10條行"                 "$CHECKS"  's/^_REPEAT_SURGERY_CLAUSES = _SURGERY_GRADE_CLAUSES | {"第10條"}/_REPEAT_SURGERY_CLAUSES = _SURGERY_GRADE_CLAUSES/'
run_mutant "M134 AA run_gate 未掛再手術檢核"       "$CHECKS"  's/        \*_check_surgery_repeat(result, bundle),/        *[],/'
run_mutant "M135 AA 非 POL-B 也檢"                "$CHECKS"  '/def _check_surgery_repeat(/,/summary = /s/ or _policy_code_of(bundle) != "POL-B":/:/'
run_mutant "M136 AA 非核准也檢"                   "$CHECKS"  '/def _check_surgery_repeat(/,/summary = /s/    if result.decision is not ClaimDecision.APPROVE or/    if False and/'
run_mutant "M137 AC POL-B 分支未接"                "$CHECKS"  's/        return _check_polb_single_surgery_value(result, bundle)/        return []/'
run_mutant "M138 AC 門診不打五折"                 "$CHECKS"  's/    want = {\*\*{c: full for c in _POLB_FULL_CLAUSES}, _POLB_OUTPATIENT_CLAUSE: full \/ 2}/    want = {**{c: full for c in _POLB_FULL_CLAUSES}, _POLB_OUTPATIENT_CLAUSE: full}/'
run_mutant "M139 AC 住院行不檢"                   "$CHECKS"  's/    want = {\*\*{c: full for c in _POLB_FULL_CLAUSES}, _POLB_OUTPATIENT_CLAUSE: full \/ 2}/    want = {_POLB_OUTPATIENT_CLAUSE: full \/ 2}/'
run_mutant "M140 AC 多手術名也套單一值"           "$CHECKS"  '/def _check_polb_single_surgery_value(/,/return \[GateFlag/s/    if len(grades) != 1:/    if len(grades) < 1:/'
run_mutant "M141 AD 正本也檢"                      "$CHECKS"  's/    if not bundle.medical_receipt.is_copy or unallocated <= 0:/    if unallocated <= 0:/'
run_mutant "M142 AD unallocated 0 也旗標"          "$CHECKS"  's/    if not bundle.medical_receipt.is_copy or unallocated <= 0:/    if not bundle.medical_receipt.is_copy or unallocated < 0:/'
run_mutant "M143 AD run_gate 未掛"                "$CHECKS"  's/        \*_check_deduction_unallocated(result, bundle),/        *[],/'
run_mutant "M144 AD 非 POL-C 也檢"                "$CHECKS"  '/def _check_deduction_unallocated(/,/unallocated = /s/ or _policy_code_of(bundle) != "POL-C":/:/'
run_mutant "M145 AD 非核准也檢"                   "$CHECKS"  '/def _check_deduction_unallocated(/,/unallocated = /s/    if result.decision is not ClaimDecision.APPROVE or/    if False and/'
run_mutant "M146 AE① 事件在等待期內也檢"           "$CHECKS"  's/    if facts.days_since_effective < WAITING_PERIOD_DAYS:/    if facts.days_since_effective < 0:/'
run_mutant "M147 AE① 窗口放寬到 40 日"             "$CHECKS"  's/            if d is not None and 0 <= (d - effective).days < WAITING_PERIOD_DAYS\]/            if d is not None and 0 <= (d - effective).days <= WAITING_PERIOD_DAYS + 10]/'
run_mutant "M148 AE① 傷害也檢"                    "$CHECKS"  's/    if facts.is_injury_icd or effective is None or facts.days_since_effective is None:/    if effective is None or facts.days_since_effective is None:/'
run_mutant "M149 AE① 不要求「診斷」"               "$CHECKS"  's/^_PRIOR_DIAGNOSIS = re.compile(r"(\\d{4}-\\d{2}-\\d{2})\[^。;;\]\*?診斷")/_PRIOR_DIAGNOSIS = re.compile(r"(\\d{4}-\\d{2}-\\d{2})")/'
run_mutant "M150 AE① run_gate 未掛"               "$CHECKS"  's/        \*_check_prior_diagnosis(result, bundle),/        *[],/'
run_mutant "M151 AE① 非核准也檢"                  "$CHECKS"  '/def _check_prior_diagnosis(/,/facts = /s/    if result.decision is not ClaimDecision.APPROVE:/    if False:/'
run_mutant "M152 AE④ 仟千不正規化"                "$CHECKS"  's/^_NUMERAL_VARIANTS = str.maketrans({"仟": "千"})/_NUMERAL_VARIANTS = str.maketrans({})/'
run_mutant "M153 AE② 抽樣率>0 即全送"             "$CARD"    's/        return audit_selected(output.claim_id, run_tag, audit_rate)/        return audit_rate > 0/'
run_mutant "M154 AE② 雜湊不含 run_tag"            "$CARD"    's/    digest = hashlib.sha256(f"{run_tag}:{claim_id}".encode()).digest()/    digest = hashlib.sha256(f"{claim_id}".encode()).digest()/'
run_mutant "M155 AE② 稽核卡主旨未標記"            "$CARD"    's/        "subject": f"\[ClaimCrew {.抽樣稽核. if audit else .人工覆核.}\]/        "subject": f"[ClaimCrew 人工覆核]/'
run_mutant "M156 AF 行數等於組數也檢"             "$CHECKS"  's/        if not lines or len(lines) >= groups:/        if not lines or len(lines) > groups:/'
run_mutant "M157 AF 比組數 × 限額"                "$CHECKS"  's/        if total > cap \* len(lines):/        if total > cap * groups:/'
run_mutant "M158 AF 扣除前比較"                   "$CHECKS"  '/def _check_stay_cap_merged(/,/flags.append/s/        total = sum((max(ln.actual_amount - ln.deduction, Decimal(0)) for ln in lines), Decimal(0))/        total = sum((ln.actual_amount for ln in lines), Decimal(0))/'
run_mutant "M159 AF run_gate 未掛"                "$CHECKS"  's/        \*_check_stay_cap_merged(result, bundle),/        *[],/'
run_mutant "M160 AF 非核准也檢"                   "$CHECKS"  '/def _check_stay_cap_merged(/,/clauses = /s/    if result.decision is not ClaimDecision.APPROVE:/    if False:/'
run_mutant "M161 AG ICD 無法解析也豁免"           "$CHECKS"  's/    if appendix2_match(bundle.diagnosis_certificate.icd_code.value) is False:/    if not appendix2_match(bundle.diagnosis_certificate.icd_code.value):/'
run_mutant "M162 AG 範圍上界不含"                 "$CHECKS"  's/            if letter == lt and lo <= digits\[:len(lo)\] <= hi:/            if letter == lt and lo <= digits[:len(lo)] < hi:/'
run_mutant "M163 AG 糖尿病漏 E08/E09"            "$CHECKS"  's/    ("E", "08", "13"),/    ("E", "10", "13"),/'
run_mutant "M164 AG CKD 不分期"                   "$CHECKS"  's/    ("N", "183", "186"),/    ("N", "18", "18"),/'
run_mutant "M165 AG 無碼不回 None"                "$CHECKS"  '/def appendix2_match(/,/return False/s/    if not codes:/    if False:/'
run_mutant "M166 AG issue_date 未豁免"            "$CHECKS"  's/ROUTING_EXEMPT_FIELDS = frozenset({"low_confidence_fields", "diagnosis_certificate.issue_date"})/ROUTING_EXEMPT_FIELDS = frozenset({"low_confidence_fields"})/'
run_mutant "M167 AH 佇列訊息不刪"                 "$CLOUDRUN" '/^    queue.delete(msg_id, receipt)$/d'
run_mutant "M168 AH 案號只比前綴"                 "$CLOUDRUN" 's/all(isinstance(c, str) and _CASE_ID.fullmatch(c) for c in ids)/all(isinstance(c, str) and _CASE_ID.match(c) for c in ids)/'
run_mutant "M169 AH 請求位置不檢"                 "$CLOUDRUN" 's/        if container != INBOX_CONTAINER or not name.startswith(REQUEST_PREFIX) or not name.endswith(".json"):/        if False:/'
run_mutant "M170 AH 一次取多則訊息"               "$CLOUDRUN" 's/numofmessages=1&/numofmessages=32\&/'
run_mutant "M171 AH 不檢 maxExecutions"           "$BICEPCHK" 's/        if scale.get("maxExecutions") != 1:/        if False:/'
run_mutant "M172 AH 不檢過大角色"                 "$BICEPCHK" 's/    for role in FORBIDDEN_ROLES:/    for role in ():/'
run_mutant "M173 AI 對照表清空"                   "$CHECKS"  's/^SURGERY_DEFINITION_CLAUSE: dict\[str, str\] = {"POL-B": "第2條"}/SURGERY_DEFINITION_CLAUSE: dict[str, str] = {}/'
run_mutant "M174 AI 擴及三保單"                   "$CHECKS"  's/^SURGERY_DEFINITION_CLAUSE: dict\[str, str\] = {"POL-B": "第2條"}/SURGERY_DEFINITION_CLAUSE: dict[str, str] = {"POL-A": "第2條", "POL-B": "第2條", "POL-C": "第2條"}/'
run_mutant "M175 AI 非拒付也檢"                   "$CHECKS"  '/def _check_surgery_definition_deny(/,/return \[GateFlag/s/    if result.decision is not ClaimDecision.DENY:/    if False:/'
run_mutant "M176 AI 條號不正規化"                 "$CHECKS"  '/def _check_surgery_definition_deny(/,/return \[GateFlag/s/    if clause is None or clause not in {normalize_clause_no(c.clause_no) for c in result.citations}:/    if clause is None or clause not in {c.clause_no for c in result.citations}:/'
run_mutant "M177 AI run_gate 未掛"                "$CHECKS"  's/        \*_check_surgery_definition_deny(result, bundle),/        *[],/'
run_mutant "M178 AJ 附錄表頭列未略過"             "$PIDX"    's/        for ln in lines\[1:\]:/        for ln in lines:/'
run_mutant "M179 AJ 末段併入本文"                 "$PIDX"    's/            segs\["末段" if seen_enum else "本文"\] = ln/            segs["本文"] = ln/'
run_mutant "M180 AJ 等級不正規化"                 "$PIDX"    's/    m = _SEG_GRADE_RE.match(s)/    m = None/'
run_mutant "M181 AJ 項款不收斂"                   "$PIDX"    '/def normalize_segment(/,/return m.group/s/    return m.group(1) if m else s/    return s/'
run_mutant "M182 AJ 對上也不填 quote"             "$RUNNER"  's/        out.append(c.model_copy(update={"quote": text}) if text is not None else c)/        out.append(c)/'
run_mutant "M183 AJ finalize 未掛填引文"          "$RUNNER"  's/    cits = fill_citation_quotes(draft.citations)/    cits = draft.citations/'
run_mutant "M184 AJ Gate 段落不反查"              "$CHECKS"  's/        if normalize_segment(c.segment) not in clause_segments(clause):/        if False:/'
run_mutant "M185 AJ 空引文空段落放行"             "$CHECKS"  's/    elif not c.quote.strip():/    elif False:/'
run_mutant "M187 收件人守門變數不檢 fail"        "$BICEPCHK" 's/    if "fail(" not in guard or "enableReview" not in guard or "enableJobAlert" not in guard:/    if False:/'
run_mutant "M188 模組可繞過收件人守門"          "$BICEPCHK" 's/        if value != EMAIL_GUARD_REF:/        if False:/'
run_mutant "M189 AJ 附錄一限額行不歸第9條"        "$CALC"    's/    return "第9條" if clause == "附錄一" and line.kind is CalcKind.CAPPED_ACTUAL else clause/    return clause/'
run_mutant "M190 AJ 歸組不分 kind"                 "$CALC"    's/    return "第9條" if clause == "附錄一" and line.kind is CalcKind.CAPPED_ACTUAL else clause/    return "第9條" if clause == "附錄一" else clause/'
run_mutant "M191 AJ 合併鍵不歸組"                  "$CALC"    's/        key = (cap_group_clause(ln), ln.cap, ln.ratio)/        key = (ln.clause_no, ln.cap, ln.ratio)/'
run_mutant "M192 AJ F1 上限加總不歸組"             "$CHECKS"  's/            groups.setdefault(cap_group_clause(ln), \[\]).append(ln)/            groups.setdefault(normalize_clause_no(ln.clause_no), []).append(ln)/'
run_mutant "M193 AJ W2 附錄一限額行不檢"           "$CHECKS"  's/           if ln.kind is CalcKind.CAPPED_ACTUAL and cap_group_clause(ln) == "第9條" and ln.cap != want\]/           if ln.kind is CalcKind.CAPPED_ACTUAL and normalize_clause_no(ln.clause_no) == "第9條" and ln.cap != want]/'
run_mutant "M194 AJ 等級阿拉伯數字不轉"           "$PIDX"    's/        return f"第{_INT_TO_CN.get(int(n), n) if n.isdigit() else n}級"/        return f"第{n}級"/'
run_mutant "M201 AL 比例種類門檻 2→3"             "$CHECKS"  's/    if len(ratios) < 2:/    if len(ratios) < 3:/'
run_mutant "M202 AL run_gate 未掛"                "$CHECKS"  's/        \*_check_ratio_consistency(result, bundle),/        *[],/'
run_mutant "M203 AL 附錄一手術行不歸組"           "$CHECKS"  's/and cap_group_clause(ln) in _POLC_RATIO_GROUPS}/and normalize_clause_no(ln.clause_no) in _POLC_RATIO_GROUPS}/'
run_mutant "M204 AL 漏逐日限額病房行"             "$CHECKS"  's/    capped = (CalcKind.CAPPED_ACTUAL, CalcKind.CAPPED_PER_UNIT)/    capped = (CalcKind.CAPPED_ACTUAL,)/'
run_mutant "M205 AL 漏第11條救護車"               "$CHECKS"  's/^_POLC_RATIO_GROUPS = frozenset({"第8條", "第9條", "第10條", "第11條"})/_POLC_RATIO_GROUPS = frozenset({"第8條", "第9條", "第10條"})/'
run_mutant "M206 AL 非核准也檢"                   "$CHECKS"  '/def _check_ratio_consistency(/,/return \[\]/s/    if result.decision is not ClaimDecision.APPROVE or _policy_code_of(bundle) != "POL-C":/    if _policy_code_of(bundle) != "POL-C":/'
run_mutant "M207 AL 報錯 detail 不截原文片段"     "$RUNNER"  's/    head = f"{type(exc).__name__}: {exc}".split(RAW_TEXT_MARKER)\[0\].rstrip()/    head = f"{type(exc).__name__}: {exc}".rstrip()/'
run_mutant "M208 AL 報錯不寫輸出"                 "$RUNNER"  '/^        _write_atomic(out_dir \/ f"{claim_id}.json", output_json(err_out))$/d'
run_mutant "M209 AL 報錯不送卡"                   "$RUNNER"  '/^        _review_hook(rec, err_out, None, reviewer)$/d'
run_mutant "M210 AL regate 抹掉 pipeline_error"   "$REGATE"  's/        if bundle_file.is_file() and not any(f.check == "pipeline_error" for f in out.gate.flags):/        if bundle_file.is_file():/'
run_mutant "M211 AM 連結不 URL 編碼" "$CARD" 's/    return f"{action_url}\&{urlencode(.*$/    return f"{action_url}\&run={run_tag}\&case={claim_id}"/'
run_mutant "M212 AM href 不跳脫" "$CARD" 's/        + (f"<p><a href=\\"{_e(card.review_link)}\\">/        + (f"<p><a href=\\"{card.review_link}\\">/'
run_mutant "M213 AM 收集器不帶連結" "$CARD" 's/        self.cards.append(build_review_card(output, bundle, action_url=self.action_url, run_tag=self.run_tag))/        self.cards.append(build_review_card(output, bundle, run_tag=self.run_tag))/'
run_mutant "M214 AM 同案不比時間" "$RECORD" 's/        if cur is None or r.submitted_at > cur.submitted_at:/        if True:/'
run_mutant "M215 AM 負金額不擋" "$RECORD" 's/        if not d.is_finite() or d < 0:/        if not d.is_finite():/'
run_mutant "M216 AM 備註長度放寬" "$RECORD" 's/    note: str = Field(default="", max_length=500)/    note: str = Field(default="", max_length=5000)/'
run_mutant "M217 AM 連送間隔歸零" "$RSTATS" 's/^BURST_GAP_S = 5 /BURST_GAP_S = 0 /'
run_mutant "M218 AM 掃描器 UA 不檢" "$RSTATS" 's/    hits = {cid for cid, r in latest.items() if any(k in r.user_agent.lower() for k in SCANNER_UA)}/    hits: set[str] = set()/'
run_mutant "M219 AM 推翻比最終判定" "$RSTATS" 's/if r.decision is not cards\[cid\].gate.original_decision/if r.decision is not cards[cid].gate.final_decision/'
run_mutant "M220 AM ⑦ 預設關不檢" "$BICEPCHK" 's/    if template.get("parameters", {}).get("enableReviewAction", {}).get("defaultValue") is not False:/    if False:/'
run_mutant "M221 AM ⑦ 路徑使用者輸入不檢" "$BICEPCHK" 's/        if "guid()" not in uri or "triggerFormDataValue" in uri or "queries" in uri:/        if "guid()" not in uri:/'
run_mutant "M222 AM ⑦ 送出端受控身分不檢" "$BICEPCHK" 's/        if a.get("inputs", {}).get("authentication", {}).get("type") != "ManagedServiceIdentity":/        if False:/'
run_mutant "M223 AM ⑦ 表單頁寫入動作不檢" "$BICEPCHK" 's/    if any(a.get("type") != "Response" for a in actions):/    if False:/'
run_mutant "M224 AM ⑦ 回填未編碼不檢" "$BICEPCHK" 's/    if refs == 0 or body.count(f"uriComponent(coalesce({QUERY_REF}") != refs:/    if refs == 0:/'
run_mutant "M225 AM ⑦ 角色範圍不檢" "$BICEPCHK" 's/ or "\/containers\/" not in str(roles\[0\].get("scope", "")):/:/'
run_mutant_ak "M195 AK 未知等級不擋"                 "$DEGRADE" 's/    if level not in LEVELS:/    if False:/'
run_mutant_ak "M196 AK PDF 日期未固定"               "$DEGRADE" 's/                                              creationDate=FIXED_PDF_DATE, modDate=FIXED_PDF_DATE)/                                              )/'
run_mutant_ak "M197 AK 既有輸出照覆寫"               "$DEGRADE" 's/    if existing and not args.force:/    if False:/'
run_mutant_ak "M198 AK 缺件不提示略過"               "$DEGRADE" '/        if not pdfs:/,/            continue/d'
run_mutant_ak "M199 AK 不加椒鹽雜點"                 "$DEGRADE" 's/    img = _salt_pepper(img, p\["noise"\], rng)/    img = img/'
run_mutant_ak "M200 AK 種子不含文件鍵"               "$DEGRADE" 's/f"{seed_key}|{level}"/f"{level}"/'
run_mutant "M186 AJ Gate 段落不正規化"            "$CHECKS"  's/        if normalize_segment(c.segment) not in clause_segments(clause):/        if c.segment not in clause_segments(clause):/'
run_mutant "M72 顧問模式仍標記 applied"        "$REPAIR"  's/                 would_release=reason == "released", applied=not advisory)/                 would_release=reason == "released", applied=True)/'
run_mutant "M33 手術名比對不去空白"          "$CHECKS"  's/summary = re.sub(r"\\s+", "", raw_summary).translate(_OCR_CONFUSABLES)/summary = raw_summary.translate(_OCR_CONFUSABLES)/'
fi
run_mutant "M18 逐日限額不套上限"            "$CALC"    's/base = min(max(line.actual_amount - line.deduction, Decimal(0)), Decimal(line.quantity) \* line.unit_amount)/base = max(line.actual_amount - line.deduction, Decimal(0))/'

restore
for pair in "$B_CALC:$CALC" "$B_CHECKS:$CHECKS" "$B_RUNNER:$RUNNER" "$B_DERIVED:$DERIVED" "$B_REF:$REF" "$B_COST:$COST" "$B_PROBE:$PROBE" "$B_AGENT:$AGENT" "$B_AGENTSVC:$AGENTSVC" "$B_REPAIR:$REPAIR" "$B_CARD:$CARD" "$B_DICLIENT:$DICLIENT" "$B_CLOUDRUN:$CLOUDRUN" "$B_SAFETY:$SAFETY" "$B_CSCLIENT:$CSCLIENT" "$B_EVAL:$EVAL" "$B_REGATE:$REGATE" "$B_BICEPCHK:$BICEPCHK" "$B_PIDX:$PIDX" "$B_DEGRADE:$DEGRADE" "$B_RECORD:$RECORD" "$B_RSTATS:$RSTATS"; do
  cmp -s "${pair%%:*}" "${pair##*:}" || { echo "警告:${pair##*:} 有殘留!"; exit 1; }
done
rm -f "${BACKUPS[@]}"
echo "還原驗證:無殘留(二十二檔與執行前副本一致);killed=$killed survived=$survived"
[ "$survived" -eq 0 ]
