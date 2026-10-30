"""SPEC-ah:事件觸發模式 `cloud_run --from-queue`——
Blob `claims-inbox/requests/*.json` → Event Grid → Storage Queue → Job;一則訊息一次執行、先刪後跑(至多一次)、
壞請求不跑管線但留 failed meta。"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.test_cloud_run import FakeStore, _fake_run
from tools import cloud_run as cr

NOW = datetime(2026, 10, 7, 3, 4, tzinfo=UTC)
URL = "https://stx.blob.core.windows.net/claims-inbox/requests/r1.json"


class FakeQueue:
    def __init__(self, texts: list[str]) -> None:
        self.msgs = [(f"id{i}", f"pr{i}", t) for i, t in enumerate(texts)]
        self.deleted: list[tuple[str, str]] = []
        self.log: list[str] = []

    def receive(self) -> tuple[str, str, str] | None:
        self.log.append("receive")
        return self.msgs.pop(0) if self.msgs else None

    def delete(self, msg_id: str, pop_receipt: str) -> None:
        self.log.append("delete")
        self.deleted.append((msg_id, pop_receipt))


def _event(url: str = URL, *, b64: bool = True, as_list: bool = False) -> str:
    ev: Any = {"eventType": "Microsoft.Storage.BlobCreated", "data": {"url": url}}
    text = json.dumps([ev] if as_list else ev)
    return base64.b64encode(text.encode()).decode() if b64 else text


def _store_with(body: Any, name: str = "requests/r1.json") -> FakeStore:
    s = FakeStore()
    s.blobs[("claims-inbox", name)] = json.dumps(body).encode() if not isinstance(body, bytes) else body
    return s


def _go(queue: FakeQueue, store: FakeStore, tmp_path: Path, rc: int = 0) -> tuple[int, dict[str, Any]]:
    holder: dict[str, Any] = {}
    run = _fake_run(holder, rc=rc)
    calls: list[list[str]] = []

    def spy(argv: list[str]) -> int:
        calls.append(argv)
        return run(argv)
    code = cr.main(["--from-queue", "--pace", "5"], store=store, queue=queue, run=spy, now=NOW,
                   work_dir=tmp_path, sha="abc1234def")
    holder["calls"] = calls
    return code, holder


def _meta(store: FakeStore) -> dict[str, Any]:
    names = [n for c, n in store.blobs if c == "runs" and n.endswith("run_meta.json")]
    assert len(names) == 1, names
    return json.loads(store.blobs[("runs", names[0])])


# --- 事件解析 ---
@pytest.mark.parametrize("b64,as_list", [(True, False), (False, False), (True, True), (False, True)])
def test_ah_parse_event_variants(b64, as_list):
    assert cr.parse_event(_event(b64=b64, as_list=as_list)) == ("claims-inbox", "requests/r1.json")


def test_ah_parse_event_url_decoded():
    assert cr.parse_event(_event(URL.replace("r1", "r%201"))) == ("claims-inbox", "requests/r 1.json")


@pytest.mark.parametrize("text", ["not json", base64.b64encode(b"[]").decode(), json.dumps({"data": {}}),
                                  json.dumps({"data": {"url": "https://stx.blob.core.windows.net/"}})])
def test_ah_parse_event_bad(text):
    with pytest.raises(ValueError):
        cr.parse_event(text)


# --- 請求檔 ---
def test_ah_parse_request_ok():
    assert cr.parse_request(json.dumps({"case_ids": ["C073", "C001"]}).encode()) == ["C073", "C001"]


@pytest.mark.parametrize("body", [b"{", b"[]", json.dumps({}).encode(), json.dumps({"case_ids": []}).encode(),
                                  json.dumps({"case_ids": "C001"}).encode(), json.dumps({"case_ids": ["C1"]}).encode(),
                                  json.dumps({"case_ids": ["C0011"]}).encode(),
                                  json.dumps({"case_ids": ["--all"]}).encode(),
                                  json.dumps({"case_ids": [1]}).encode()])
def test_ah_parse_request_bad(body):
    with pytest.raises(ValueError):
        cr.parse_request(body)


# --- 端到端(假佇列/假 Blob/假管線)---
def test_ah_empty_queue_noop(tmp_path):
    store = FakeStore()
    code, h = _go(FakeQueue([]), store, tmp_path)
    assert code == 0 and h["calls"] == [] and store.uploads == []


def test_ah_happy_path_runs_requested_cases_only(tmp_path):
    q, store = FakeQueue([_event()]), _store_with({"case_ids": ["C073", "C001"]})
    code, h = _go(q, store, tmp_path)
    assert code == 0 and len(h["calls"]) == 1
    argv = h["calls"][0]
    assert "--all" not in argv and argv[:4] == ["--strategy", "full", "C073", "C001"]
    assert "--pace" in argv and "--from-queue" not in argv
    meta = _meta(store)
    assert meta["status"] == "succeeded" and meta["request"] == "claims-inbox/requests/r1.json"
    assert q.deleted == [("id0", "pr0")]


def test_ah_delete_before_run(tmp_path):
    """至多一次:訊息在跑管線前刪除,容器崩潰也不會被 KEDA 重新觸發而重複計費。"""
    q, store = FakeQueue([_event()]), _store_with({"case_ids": ["C001"]})
    order: list[str] = []

    def run(argv: list[str]) -> int:
        order.append("deleted" if q.deleted else "not-deleted")
        return 0
    cr.main(["--from-queue"], store=store, queue=q, run=run, now=NOW, work_dir=tmp_path, sha="abc1234def")
    assert order == ["deleted"]


def test_ah_pipeline_failure_still_deleted_and_failed_meta(tmp_path):
    q, store = FakeQueue([_event()]), _store_with({"case_ids": ["C001"]})
    code, _ = _go(q, store, tmp_path, rc=1)
    assert code == 1 and q.deleted == [("id0", "pr0")] and _meta(store)["status"] == "failed"


def test_ah_one_message_per_execution(tmp_path):
    q, store = FakeQueue([_event(), _event()]), _store_with({"case_ids": ["C001"]})
    _go(q, store, tmp_path)
    assert q.log.count("receive") == 1 and len(q.msgs) == 1


@pytest.mark.parametrize("url", [URL.replace("claims-inbox", "runs"), URL.replace("requests/", "other/"),
                                 URL.replace(".json", ".pdf")])
def test_ah_wrong_location_rejected(tmp_path, url):
    """有效請求檔就放在錯誤位置:只有位置檢查能擋(不能靠 download 失敗)。"""
    container, name = cr.parse_event(_event(url))
    store = FakeStore()
    store.blobs[(container, name)] = json.dumps({"case_ids": ["C001"]}).encode()
    q = FakeQueue([_event(url)])
    code, h = _go(q, store, tmp_path)
    assert code == 2 and h["calls"] == [] and q.deleted == [("id0", "pr0")]
    meta = _meta(store)
    assert meta["status"] == "failed" and "bad request" in meta["error"]


def test_ah_bad_request_body_not_run(tmp_path, capsys):
    q, store = FakeQueue([_event()]), _store_with({"case_ids": ["--all"]})
    code, h = _go(q, store, tmp_path)
    assert code == 2 and h["calls"] == [] and q.deleted == [("id0", "pr0")]
    assert _meta(store)["status"] == "failed"
    assert ": failed exit=2" in capsys.readouterr().out  # 告警查詢的錨點字串


def test_ah_missing_blob_not_run(tmp_path):
    q, store = FakeQueue([_event()]), FakeStore()
    code, h = _go(q, store, tmp_path)
    assert code == 2 and h["calls"] == [] and _meta(store)["status"] == "failed"


def test_ah_manual_mode_unchanged(tmp_path):
    """不變式:無 --from-queue 時不碰佇列、仍為 --all。"""
    store, holder = FakeStore(), {}
    q = FakeQueue([_event()])
    rc = cr.main([], store=store, queue=q, run=_fake_run(holder), now=NOW, work_dir=tmp_path, sha="abc1234def")
    assert rc == 0 and q.log == [] and holder["argv"][0] == "--all"
    assert "request" not in _meta(store)


def _azq(responses: list[bytes]) -> tuple[cr.AzureQueue, list[tuple[str, str]]]:
    q = object.__new__(cr.AzureQueue)
    q._base = "https://stx.queue.core.windows.net/claim-requests/messages"
    calls: list[tuple[str, str]] = []

    def call(method: str, url: str) -> bytes:
        calls.append((method, url))
        return responses.pop(0) if responses else b""
    q._call = call  # type: ignore[method-assign]
    return q, calls


def test_ah_azure_queue_receive_and_delete():
    xml = (b"<?xml version='1.0'?><QueueMessagesList><QueueMessage><MessageId>m1</MessageId>"
           b"<PopReceipt>a/b+c==</PopReceipt><MessageText>TEXT</MessageText></QueueMessage></QueueMessagesList>")
    q, calls = _azq([xml])
    assert q.receive() == ("m1", "a/b+c==", "TEXT")
    assert calls[0] == ("GET", q._base + "?numofmessages=1&visibilitytimeout=300")
    q.delete("m1", "a/b+c==")
    assert calls[1] == ("DELETE", q._base + "/m1?popreceipt=a%2Fb%2Bc%3D%3D")


def test_ah_azure_queue_empty():
    q, _ = _azq([b"<?xml version='1.0'?><QueueMessagesList />"])
    assert q.receive() is None
