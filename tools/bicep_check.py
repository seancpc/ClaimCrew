"""SPEC-ah:編譯後 ARM JSON 的結構斷言(CI bicep job:`bicep build --outfile` 後執行)。

`bicep build` 0 warning 只證明型別合法;本檔守住 SPEC-ah 的行為不變式:
  ① 新開關預設關(enableMonitoring / enableEventTrigger = false)
  ② Event Grid 訂閱只收 claims-inbox/requests/*.json 的 BlobCreated,投遞至與程式一致的佇列名
  ③ 事件 Job:Event 觸發、maxExecutions 1、retry 0、KEDA 規則以身分(無金鑰)讀同一佇列、帶 --from-queue
  ④ 佇列角色只有 Message Processor + Reader(不授帳戶層 Contributor 類角色)
  ⑤ 告警查詢錨點字串與 cloud_run 輸出一致
  ⑦ SPEC-am 覆核閉環:預設關;送出端 POST、Blob 路徑由 guid() 產生且受控身分驗證;表單頁 GET、只回應不寫入、
     回填查詢參數一律 uriComponent();送出端角色範圍限 runs 容器
  ⑥ 寄信開關開啟而收件人為空 → fail();覆核卡片與告警模組皆經此守門變數取收件人(2026-10-10 reviewEmailTo 空值)
用法:python3 tools/bicep_check.py <main.json>
"""
from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.cloud_run import INBOX_CONTAINER, QUEUE_NAME, REQUEST_PREFIX  # noqa: E402

ROLE_QUEUE_PROCESSOR = "8a0f0c08-91a1-4084-bc3d-661d67233fed"
ROLE_QUEUE_READER = "19e7f393-937e-4f77-808e-94535e297925"
FAILED_ANCHOR = ": failed exit="
EMAIL_GUARD_REF = "[variables('reviewEmail')]"
EMAIL_MODULE_PARAMS = {"logicapp": "reviewEmailTo", "monitoring": "alertEmail"}
ROLE_BLOB_DATA_CONTRIBUTOR = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"
QUERY_REF = "triggerOutputs()?['queries']"
# Storage Account Contributor / Storage Queue Data Contributor:權限過大(可刪佇列、改帳戶)
FORBIDDEN_ROLES = ("17d1049b-9a84-46fb-8f53-869881c3d3ab", "974c5e8b-45b9-4653-ba55-5f855dd0fb88")


def iter_resources(template: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """含巢狀 module(Microsoft.Resources/deployments 的 properties.template)。"""
    res = template.get("resources", [])
    items = res.values() if isinstance(res, dict) else res
    for r in items:
        yield r
        inner = r.get("properties", {}).get("template")
        if r.get("type") == "Microsoft.Resources/deployments" and isinstance(inner, dict):
            yield from iter_resources(inner)


def _of_type(template: dict[str, Any], rtype: str) -> list[dict[str, Any]]:
    return [r for r in iter_resources(template) if r.get("type", "").lower() == rtype.lower()]


def check(template: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    params = template.get("parameters", {})
    for p in ("enableMonitoring", "enableEventTrigger"):
        if params.get(p, {}).get("defaultValue") is not False:
            errs.append(f"① {p} 預設值必須為 false")

    queues = _of_type(template, "Microsoft.Storage/storageAccounts/queueServices/queues")
    if not any(QUEUE_NAME in str(q.get("name")) for q in queues):
        errs.append(f"② 缺佇列 {QUEUE_NAME}")

    subs = _of_type(template, "Microsoft.EventGrid/systemTopics/eventSubscriptions")
    if len(subs) != 1:
        errs.append(f"② Event Grid 訂閱應恰 1 個,實際 {len(subs)}")
    for s in subs:
        f = s.get("properties", {}).get("filter", {})
        if f.get("includedEventTypes") != ["Microsoft.Storage.BlobCreated"]:
            errs.append("② 訂閱只能收 BlobCreated")
        if f.get("subjectBeginsWith") != f"/blobServices/default/containers/{INBOX_CONTAINER}/blobs/{REQUEST_PREFIX}":
            errs.append("② subjectBeginsWith 與 cloud_run 請求位置不一致")
        if f.get("subjectEndsWith") != ".json":
            errs.append("② subjectEndsWith 必須為 .json")
        if s.get("properties", {}).get("destination", {}).get("endpointType") != "StorageQueue":
            errs.append("② 投遞目標必須為 StorageQueue")

    jobs = _of_type(template, "Microsoft.App/jobs")
    event_jobs = [j for j in jobs if j.get("properties", {}).get("configuration", {}).get("triggerType") == "Event"]
    if len(event_jobs) != 1:
        errs.append(f"③ 事件 Job 應恰 1 個,實際 {len(event_jobs)}")
    for j in event_jobs:
        cfg = j["properties"]["configuration"]
        if cfg.get("replicaRetryLimit") != 0:
            errs.append("③ 事件 Job replicaRetryLimit 必須為 0")
        scale = cfg.get("eventTriggerConfig", {}).get("scale", {})
        if scale.get("maxExecutions") != 1:
            errs.append("③ 事件 Job maxExecutions 必須為 1")
        rules = scale.get("rules", [])
        if len(rules) != 1 or rules[0].get("type") != "azure-queue":
            errs.append("③ 事件 Job 必須恰一條 azure-queue 規則")
        for r in rules:
            if not r.get("identity") or r.get("auth"):
                errs.append("③ KEDA 規則必須以身分驗證、不得用 secret auth")
        containers = j["properties"].get("template", {}).get("containers", [])
        if not containers or "--from-queue" not in json.dumps(containers[0].get("args")):
            errs.append("③ 事件 Job 容器參數必須含 --from-queue")
    manual = [j for j in jobs if j.get("properties", {}).get("configuration", {}).get("triggerType") == "Manual"]
    if len(manual) != 1 or "--from-queue" in json.dumps(manual[0]):
        errs.append("③ 手動 Job 必須保留且不帶 --from-queue")

    # 角色 ID 編譯後在模組 variables,指派以 variables('<名>') 引用 → 兩者都要對上
    role_text = json.dumps(_of_type(template, "Microsoft.Authorization/roleAssignments"))
    all_text = json.dumps(template)
    for role, var in ((ROLE_QUEUE_PROCESSOR, "roleQueueProcessor"), (ROLE_QUEUE_READER, "roleQueueReader")):
        if role not in all_text or f"variables('{var}')" not in role_text:
            errs.append(f"④ 缺佇列角色 {var}")
    for role in FORBIDDEN_ROLES:
        if role in all_text:
            errs.append(f"④ 不得授予帳戶/佇列 Contributor 類角色 {role}")

    rules_q = _of_type(template, "Microsoft.Insights/scheduledQueryRules")
    if not any(FAILED_ANCHOR in json.dumps(r) for r in rules_q):
        errs.append(f"⑤ 告警查詢缺錨點字串 {FAILED_ANCHOR!r}")

    guard = str(template.get("variables", {}).get("reviewEmail", ""))
    if "fail(" not in guard or "enableReview" not in guard or "enableJobAlert" not in guard:
        errs.append("⑥ reviewEmail 守門變數須在 enableReview / enableJobAlert 開啟且收件人為空時 fail()")
    deployments = {r.get("name"): r for r in _of_type(template, "Microsoft.Resources/deployments")}
    for name, param in EMAIL_MODULE_PARAMS.items():
        value = deployments.get(name, {}).get("properties", {}).get("parameters", {}).get(param, {}).get("value")
        if value != EMAIL_GUARD_REF:
            errs.append(f"⑥ 模組 {name}.{param} 必須引用守門變數 reviewEmail")
    errs += _check_review_action(template)
    return errs


def _workflow(resources: list[dict[str, Any]], suffix: str) -> dict[str, Any] | None:
    return next((r for r in resources if r.get("type") == "Microsoft.Logic/workflows"
                 and suffix in str(r.get("name"))), None)


def _check_review_action(template: dict[str, Any]) -> list[str]:
    """⑦ SPEC-am:編譯輸出中運算式字串內的單引號為 '',比對前一律還原為 '。"""
    errs: list[str] = []
    if template.get("parameters", {}).get("enableReviewAction", {}).get("defaultValue") is not False:
        errs.append("⑦ enableReviewAction 預設值必須為 false")
    mod = {r.get("name"): r for r in _of_type(template, "Microsoft.Resources/deployments")}.get("reviewaction")
    inner = mod.get("properties", {}).get("template") if mod else None
    if not isinstance(inner, dict):
        return errs + ["⑦ 缺 reviewaction 模組"]
    res = inner.get("resources", [])
    items = list(res.values()) if isinstance(res, dict) else list(res)
    submit, form = _workflow(items, "review-submit"), _workflow(items, "review-form")
    if submit is None or form is None:
        return errs + ["⑦ 缺覆核送出端或表單頁 workflow"]
    sd = submit["properties"]["definition"]
    puts = [a for a in sd.get("actions", {}).values() if a.get("type") == "Http"]
    if sd["triggers"]["manual"].get("inputs", {}).get("method") != "POST":
        errs.append("⑦ 送出端觸發必須為 POST")
    if len(puts) != 1:
        errs.append("⑦ 送出端必須恰有一個 Http 寫入動作")
    for a in puts:
        uri = str(a.get("inputs", {}).get("uri", "")).replace("''", "'")
        if "guid()" not in uri or "triggerFormDataValue" in uri or "queries" in uri:
            errs.append("⑦ 送出端 Blob 路徑必須由 guid() 產生,不得含使用者輸入")
        if a.get("inputs", {}).get("authentication", {}).get("type") != "ManagedServiceIdentity":
            errs.append("⑦ 送出端必須以受控身分驗證寫入")
    fd = form["properties"]["definition"]
    if fd["triggers"]["manual"].get("inputs", {}).get("method") != "GET":
        errs.append("⑦ 表單頁觸發必須為 GET")
    actions = list(fd.get("actions", {}).values())
    if any(a.get("type") != "Response" for a in actions):
        errs.append("⑦ 表單頁只能回應,不得有寫入 / 外呼動作")
    body = "".join(str(a.get("inputs", {}).get("body", "")) for a in actions).replace("''", "'")
    refs = body.count(QUERY_REF)
    if refs == 0 or body.count(f"uriComponent(coalesce({QUERY_REF}") != refs:
        errs.append("⑦ 表單頁回填查詢參數必須全部經 uriComponent()")
    roles = [r for r in items if r.get("type") == "Microsoft.Authorization/roleAssignments"]
    blob_var = any(v == ROLE_BLOB_DATA_CONTRIBUTOR for v in inner.get("variables", {}).values())
    if not blob_var or len(roles) != 1 or "/containers/" not in str(roles[0].get("scope", "")):
        errs.append("⑦ 角色:送出端只能有一個 Blob Data Contributor 指派,範圍限容器")
    return errs


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("用法:python3 tools/bicep_check.py <main.json>", file=sys.stderr)
        return 2
    errs = check(json.loads(Path(args[0]).read_text(encoding="utf-8")))
    for e in errs:
        print(f"FAIL {e}")
    print("bicep_check:", "OK" if not errs else f"{len(errs)} 項不符")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
