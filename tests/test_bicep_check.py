"""SPEC-ah:`tools/bicep_check.py` 每條斷言都能失敗(合成最小模板;真模板由 CI bicep job 編譯後檢查)。"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from tools import bicep_check as bc
from tools import cloud_run as cr


def _valid() -> dict[str, Any]:
    event_job = {"type": "Microsoft.App/jobs", "properties": {
        "configuration": {"triggerType": "Event", "replicaRetryLimit": 0, "eventTriggerConfig": {"scale": {
            "maxExecutions": 1, "rules": [{"type": "azure-queue", "identity": "[x]"}]}}},
        "template": {"containers": [{"args": "[concat(createArray('--from-queue'), variables('jobArgs'))]"}]}}}
    manual_job = {"type": "Microsoft.App/jobs", "properties": {"configuration": {"triggerType": "Manual"},
                                                               "template": {"containers": [{"args": "[x]"}]}}}
    module = {"type": "Microsoft.Resources/deployments", "properties": {"template": {
        "variables": {"roleQueueProcessor": bc.ROLE_QUEUE_PROCESSOR, "roleQueueReader": bc.ROLE_QUEUE_READER},
        "resources": {
            "q": {"type": "Microsoft.Storage/storageAccounts/queueServices/queues",
                  "name": f"[x]/default/{cr.QUEUE_NAME}"},
            "sub": {"type": "Microsoft.EventGrid/systemTopics/eventSubscriptions", "properties": {
                "destination": {"endpointType": "StorageQueue"},
                "filter": {"includedEventTypes": ["Microsoft.Storage.BlobCreated"],
                           "subjectBeginsWith": "/blobServices/default/containers/claims-inbox/blobs/requests/",
                           "subjectEndsWith": ".json"}}},
            "ej": event_job, "mj": manual_job,
            "ra1": {"type": "Microsoft.Authorization/roleAssignments",
                    "properties": {"roleDefinitionId": "[variables('roleQueueProcessor')]"}},
            "ra2": {"type": "Microsoft.Authorization/roleAssignments",
                    "properties": {"roleDefinitionId": "[variables('roleQueueReader')]"}},
            "al": {"type": "Microsoft.Insights/scheduledQueryRules",
                   "properties": {"query": 'x | where Log_s has ": failed exit="'}},
        }}}}
    guard = ("[if(and(or(parameters('enableReview'), parameters('enableJobAlert')),"
             " empty(parameters('reviewEmailTo'))), fail('x'), parameters('reviewEmailTo'))]")
    logicapp = {"type": "Microsoft.Resources/deployments", "name": "logicapp",
                "properties": {"parameters": {"reviewEmailTo": {"value": bc.EMAIL_GUARD_REF}}}}
    monitoring = {"type": "Microsoft.Resources/deployments", "name": "monitoring",
                  "properties": {"parameters": {"alertEmail": {"value": bc.EMAIL_GUARD_REF}}}}
    return {"parameters": {"enableMonitoring": {"defaultValue": False}, "enableEventTrigger": {"defaultValue": False},
                           "enableReviewAction": {"defaultValue": False}},
            "variables": {"reviewEmail": guard},
            "resources": {"m": module, "logicapp": logicapp, "monitoring": monitoring, "ra": _review_action()}}


Q = "''"  # 0.36.1 編譯輸出中,運算式字串內的單引號以兩個單引號表示


def _review_action() -> dict[str, Any]:
    """SPEC-am 覆核閉環模組最小合法形(結構仿 0.36.1 編譯輸出:字串內單引號為 ''、表單 body 經 format())。"""
    uri = f"[format('@{{{{concat({Q}https://{{0}}/runs/reviews-inbox/{Q}, guid(), {Q}.json{Q})}}}}', parameters('s'))]"
    submit = {"type": "Microsoft.Logic/workflows", "name": "[format('logic-{0}-review-submit', parameters('b'))]",
              "properties": {"definition": {
                  "triggers": {"manual": {"type": "Request", "inputs": {"method": "POST"}}},
                  "actions": {"Put": {"type": "Http", "inputs": {
                      "uri": uri, "authentication": {"type": "ManagedServiceIdentity"}}},
                      "Ok": {"type": "Response"}}}}}
    q_run = f"uriComponent(coalesce(triggerOutputs()?[{Q}queries{Q}]?[{Q}run{Q}], {Q}{Q}))"
    q_case = f"uriComponent(coalesce(triggerOutputs()?[{Q}queries{Q}]?[{Q}case{Q}], {Q}{Q}))"
    body = f"[format('@{{{{concat({Q}<p>{Q}, {q_run}, {Q}<form>{Q}, {q_case})}}}}')]"
    form = {"type": "Microsoft.Logic/workflows", "name": "[format('logic-{0}-review-form', parameters('b'))]",
            "properties": {"definition": {
                "triggers": {"manual": {"type": "Request", "inputs": {"method": "GET"}}},
                "actions": {"Form": {"type": "Response", "inputs": {"body": body}}}}}}
    role = {"type": "Microsoft.Authorization/roleAssignments",
            "scope": "[format('Microsoft.Storage/storageAccounts/{0}/blobServices/{1}/containers/{2}', 'a', 'd', 'r')]",
            "properties": {"roleDefinitionId": "[subscriptionResourceId('x', variables('roleBlobDataContributor'))]"}}
    return {"type": "Microsoft.Resources/deployments", "name": "reviewaction", "properties": {"template": {
        "variables": {"roleBlobDataContributor": bc.ROLE_BLOB_DATA_CONTRIBUTOR},
        "resources": {"submit": submit, "role": role, "form": form}}}}


def _ra(t: dict[str, Any], key: str) -> dict[str, Any]:
    return t["resources"]["ra"]["properties"]["template"]["resources"][key]


def _res(t: dict[str, Any], key: str) -> dict[str, Any]:
    return t["resources"]["m"]["properties"]["template"]["resources"][key]


def test_valid_template_passes():
    assert bc.check(_valid()) == []


def _set_param(t, v): t["parameters"]["enableEventTrigger"]["defaultValue"] = v  # noqa: E704
def _del_queue(t): del t["resources"]["m"]["properties"]["template"]["resources"]["q"]  # noqa: E704
def _dup_sub(t): t["resources"]["m"]["properties"]["template"]["resources"]["sub2"] = _res(t, "sub")  # noqa: E704
def _types(t): _res(t, "sub")["properties"]["filter"]["includedEventTypes"].append("Microsoft.Storage.BlobDeleted")  # noqa: E704
def _prefix(t): _res(t, "sub")["properties"]["filter"]["subjectBeginsWith"] = "/blobServices/default/containers/claims-inbox/"  # noqa: E501,E704
def _suffix(t): _res(t, "sub")["properties"]["filter"]["subjectEndsWith"] = ""  # noqa: E704
def _dest(t): _res(t, "sub")["properties"]["destination"]["endpointType"] = "WebHook"  # noqa: E704
def _retry(t): _res(t, "ej")["properties"]["configuration"]["replicaRetryLimit"] = 1  # noqa: E704
def _maxex(t): _res(t, "ej")["properties"]["configuration"]["eventTriggerConfig"]["scale"]["maxExecutions"] = 10  # noqa: E704
def _rule_type(t): _res(t, "ej")["properties"]["configuration"]["eventTriggerConfig"]["scale"]["rules"][0]["type"] = "cron"  # noqa: E501,E704
def _rule_key(t):  # noqa: E704
    r = _res(t, "ej")["properties"]["configuration"]["eventTriggerConfig"]["scale"]["rules"][0]
    del r["identity"]
    r["auth"] = [{"secretRef": "conn"}]
def _no_flag(t): _res(t, "ej")["properties"]["template"]["containers"][0]["args"] = "[variables('jobArgs')]"  # noqa: E704
def _no_event(t): _res(t, "ej")["properties"]["configuration"]["triggerType"] = "Manual"  # noqa: E704
def _manual_flag(t): _res(t, "mj")["properties"]["template"]["containers"][0]["args"] = "--from-queue"  # noqa: E704
def _role(t): _res(t, "ra2")["properties"]["roleDefinitionId"] = "[variables('roleBlobContributor')]"  # noqa: E704
def _forbidden(t): t["resources"]["m"]["properties"]["template"]["variables"]["x"] = bc.FORBIDDEN_ROLES[1]  # noqa: E704
def _anchor(t): _res(t, "al")["properties"]["query"] = "x | where Log_s has \"failed\""  # noqa: E704


@pytest.mark.parametrize("mutate,code", [
    (lambda t: _set_param(t, True), "①"), (lambda t: _set_param(t, None), "①"), (_del_queue, "②"), (_dup_sub, "②"),
    (_types, "②"), (_prefix, "②"), (_suffix, "②"), (_dest, "②"), (_retry, "③"), (_maxex, "③"), (_rule_type, "③"),
    (_rule_key, "③"), (_no_flag, "③"), (_no_event, "③"), (_manual_flag, "③"), (_role, "④"), (_forbidden, "④"),
    (_anchor, "⑤"),
])
def test_each_violation_detected(mutate, code):
    t = copy.deepcopy(_valid())
    mutate(t)
    errs = bc.check(t)
    assert errs and all(e.startswith(code) for e in errs), errs


def test_anchor_matches_cloud_run_output(tmp_path, capsys):
    """告警錨點必須真的出現在 cloud_run 失敗輸出(管線非零)。"""
    from tests.test_cloud_run import FakeStore, _fake_run
    cr.main([], store=FakeStore(), run=_fake_run({}, rc=1), work_dir=tmp_path, sha="abc1234def")
    assert bc.FAILED_ANCHOR in capsys.readouterr().out


def test_main_exit_codes(tmp_path, capsys):
    good, bad = tmp_path / "g.json", tmp_path / "b.json"
    good.write_text(json.dumps(_valid()), encoding="utf-8")
    t = _valid()
    _retry(t)
    bad.write_text(json.dumps(t), encoding="utf-8")
    assert bc.main([str(good)]) == 0 and bc.main([str(bad)]) == 1 and bc.main([]) == 2


def test_email_guard_variable_must_fail_on_empty():
    t = _valid()
    t["variables"]["reviewEmail"] = "[parameters('reviewEmailTo')]"
    assert any(e.startswith("⑥ reviewEmail") for e in bc.check(t))


@pytest.mark.parametrize("module,param", sorted(bc.EMAIL_MODULE_PARAMS.items()))
def test_email_modules_must_use_guard(module, param):
    """只違反一個條件:守門變數存在,但該模組直接取參數(繞過守門)。"""
    t = _valid()
    t["resources"][module]["properties"]["parameters"][param]["value"] = "[parameters('reviewEmailTo')]"
    assert bc.check(t) == [f"⑥ 模組 {module}.{param} 必須引用守門變數 reviewEmail"]


def _ra_default_on(t: dict[str, Any]) -> None:
    t["parameters"]["enableReviewAction"]["defaultValue"] = True


def _ra_submit_get(t: dict[str, Any]) -> None:
    _ra(t, "submit")["properties"]["definition"]["triggers"]["manual"]["inputs"]["method"] = "GET"


def _ra_uri_user_input(t: dict[str, Any]) -> None:
    a = _ra(t, "submit")["properties"]["definition"]["actions"]["Put"]["inputs"]
    a["uri"] = a["uri"].replace("guid()", f"triggerFormDataValue({Q}case{Q})")


def _ra_no_msi(t: dict[str, Any]) -> None:
    _ra(t, "submit")["properties"]["definition"]["actions"]["Put"]["inputs"]["authentication"] = {}


def _ra_form_writes(t: dict[str, Any]) -> None:
    _ra(t, "form")["properties"]["definition"]["actions"]["Put"] = {"type": "Http", "inputs": {}}


def _ra_form_post(t: dict[str, Any]) -> None:
    _ra(t, "form")["properties"]["definition"]["triggers"]["manual"]["inputs"]["method"] = "POST"


def _ra_raw_query(t: dict[str, Any]) -> None:
    a = _ra(t, "form")["properties"]["definition"]["actions"]["Form"]["inputs"]
    wrapped = f"uriComponent(coalesce(triggerOutputs()?[{Q}queries{Q}]?[{Q}case{Q}], {Q}{Q}))"
    a["body"] = a["body"].replace(wrapped, f"coalesce(triggerOutputs()?[{Q}queries{Q}]?[{Q}case{Q}], {Q}{Q})")


def _ra_account_scope(t: dict[str, Any]) -> None:
    _ra(t, "role")["scope"] = "[format('Microsoft.Storage/storageAccounts/{0}', 'a')]"


def _ra_missing(t: dict[str, Any]) -> None:
    del t["resources"]["ra"]


@pytest.mark.parametrize("mutate,code", [
    (_ra_default_on, "⑦ enableReviewAction"), (_ra_submit_get, "⑦ 送出端"), (_ra_uri_user_input, "⑦ 送出端"),
    (_ra_no_msi, "⑦ 送出端"), (_ra_form_writes, "⑦ 表單頁"), (_ra_form_post, "⑦ 表單頁"), (_ra_raw_query, "⑦ 表單頁"),
    (_ra_account_scope, "⑦ 角色"), (_ra_missing, "⑦ 缺"),
])
def test_review_action_violation_detected(mutate, code):
    """固定解法 33:每個失敗模板只違反一條 ⑦ 斷言,且必須被該條抓到。"""
    t = _valid()
    mutate(t)
    errs = bc.check(t)
    assert errs and all(e.startswith(code) for e in errs), errs


def test_review_action_user_input_alongside_guid_detected():
    """路徑同時含 guid() 與使用者輸入(如以案號當目錄)仍是路徑注入面,只違反這一條。"""
    t = _valid()
    a = _ra(t, "submit")["properties"]["definition"]["actions"]["Put"]["inputs"]
    a["uri"] = a["uri"].replace("guid()", f"triggerFormDataValue({Q}case{Q}), {Q}/{Q}, guid()")
    errs = bc.check(t)
    assert errs == ["⑦ 送出端 Blob 路徑必須由 guid() 產生,不得含使用者輸入"]
