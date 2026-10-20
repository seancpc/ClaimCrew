"""SPEC-t T4:Container Apps Job 入口——無人值守跑一輪管線,輸出與 DI 快取走 Blob,驗證走託管身分。

順序(pre-mortem 對策:先留證據再做事):
  ① 寫 runs/<run_id>/run_meta.json(git SHA、UTC 時間、參數;status=running)
  ② 自 di-cache 容器拉 DI 快取到本機(F0 月額度 500 頁,一輪 213 頁;無快取雲端一個月只能跑兩輪)
  ③ run_pipeline --all --strategy full --out <work>/out --di-cache <work>/cache + 透傳參數(如 --review)
  ④ 輸出目錄整個上傳到 runs/<run_id>/;⑤ 快取新增檔回推(不覆蓋既有);⑥ run_meta 改 succeeded/failed + exit_code
任一步例外都記進 run_meta 並以非零退出;不吞錯、不無聲。
環境:CLAIMCREW_AUTH=entra(由 Job 設定)、CLAIMCREW_STORAGE_ACCOUNT、GIT_SHA(映像建置時注入)、REVIEW_WEBHOOK_URL(secret)。
SPEC-ah `--from-queue`:取佇列一則 Event Grid BlobCreated 事件 → 讀 claims-inbox/requests/*.json 的 case_ids
→ 只跑那些案(訊息先刪後跑)。
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sys
import tempfile
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
print("cloud_run boot", flush=True)

RUNS_CONTAINER = "runs"
CACHE_CONTAINER = "di-cache"
META_NAME = "run_meta.json"
STORAGE_ENV = "CLAIMCREW_STORAGE_ACCOUNT"
BASE_ARGS = ["--all", "--strategy", "full"]
# SPEC-ah 事件觸發:claims-inbox/requests/*.json → Event Grid → Storage Queue → Job(--from-queue)
QUEUE_ENV = "CLAIMCREW_QUEUE_NAME"
QUEUE_NAME = "claim-requests"
INBOX_CONTAINER = "claims-inbox"
REQUEST_PREFIX = "requests/"
_CASE_ID = re.compile(r"C\d{3}")


class BlobStore(Protocol):
    def upload(self, container: str, name: str, data: bytes, *, overwrite: bool = True) -> bool: ...
    def list_names(self, container: str, prefix: str = "") -> list[str]: ...
    def download(self, container: str, name: str) -> bytes: ...


class AzureBlobStore:
    """azure-storage-blob + DefaultAzureCredential(Job 的使用者指派身分 / 桌機 az login)。"""

    def __init__(self, account: str | None = None) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        acct = account or os.environ[STORAGE_ENV]
        self._svc = BlobServiceClient(f"https://{acct}.blob.core.windows.net", credential=DefaultAzureCredential())

    def upload(self, container: str, name: str, data: bytes, *, overwrite: bool = True) -> bool:
        from azure.core.exceptions import ResourceExistsError

        try:
            self._svc.get_blob_client(container, name).upload_blob(data, overwrite=overwrite)
            return True
        except ResourceExistsError:
            return False

    def list_names(self, container: str, prefix: str = "") -> list[str]:
        return sorted(b.name for b in self._svc.get_container_client(container).list_blobs(name_starts_with=prefix))

    def download(self, container: str, name: str) -> bytes:
        return bytes(self._svc.get_blob_client(container, name).download_blob().readall())


class MessageQueue(Protocol):
    def receive(self) -> tuple[str, str, str] | None: ...  # (message_id, pop_receipt, text)
    def delete(self, msg_id: str, pop_receipt: str) -> None: ...


class AzureQueue:
    """Queue REST(標準庫 urllib + DefaultAzureCredential bearer;不新增 azure-storage-queue 相依)。"""

    API_VERSION = "2021-12-02"

    def __init__(self, account: str | None = None, queue: str | None = None) -> None:
        from azure.identity import DefaultAzureCredential

        acct = account or os.environ[STORAGE_ENV]
        self._base = f"https://{acct}.queue.core.windows.net/{queue or os.environ.get(QUEUE_ENV, QUEUE_NAME)}/messages"
        self._cred = DefaultAzureCredential()

    def _call(self, method: str, url: str) -> bytes:
        import urllib.request

        token = self._cred.get_token("https://storage.azure.com/.default").token
        req = urllib.request.Request(url, method=method, headers={
            "Authorization": f"Bearer {token}", "x-ms-version": self.API_VERSION})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — 固定 https 端點
            return bytes(resp.read())

    def receive(self) -> tuple[str, str, str] | None:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(self._call("GET", f"{self._base}?numofmessages=1&visibilitytimeout=300"))
        msg = root.find("QueueMessage")
        if msg is None:
            return None
        return (msg.findtext("MessageId", ""), msg.findtext("PopReceipt", ""), msg.findtext("MessageText", ""))

    def delete(self, msg_id: str, pop_receipt: str) -> None:
        from urllib.parse import quote

        self._call("DELETE", f"{self._base}/{msg_id}?popreceipt={quote(pop_receipt, safe='')}")


def parse_event(text: str) -> tuple[str, str]:
    """Event Grid → Storage Queue 訊息 → (container, blob name)。訊息可能為 base64 或純 JSON、單一事件或陣列。"""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        try:
            obj = json.loads(base64.b64decode(text, validate=True))
        except (ValueError, binascii.Error) as e:
            raise ValueError(f"訊息非 JSON/base64 JSON: {e}") from e
    if isinstance(obj, list):
        if not obj:
            raise ValueError("空事件陣列")
        obj = obj[0]
    url = obj.get("data", {}).get("url") if isinstance(obj, dict) else None
    if not isinstance(url, str):
        raise ValueError("事件缺 data.url")
    parts = unquote(urlparse(url).path).lstrip("/").split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"url 非 container/blob: {url}")
    return parts[0], parts[1]


def parse_request(body: bytes) -> list[str]:
    """請求檔 `{"case_ids": ["C001", ...]}`;案號必須完全符合 C\\d{3}(擋 `--all` 等旗標注入)。"""
    obj = json.loads(body)
    ids = obj.get("case_ids") if isinstance(obj, dict) else None
    if not isinstance(ids, list) or not ids or not all(isinstance(c, str) and _CASE_ID.fullmatch(c) for c in ids):
        raise ValueError(f"case_ids 須為非空的 C\\d{{3}} 清單: {ids!r}")
    return list(ids)


def run_id(now: datetime, sha: str) -> str:
    return f"{now.strftime('%Y%m%dT%H%M')}-{sha[:7]}"


def git_sha() -> str:
    return os.environ.get("GIT_SHA", "unknown")


def pull_cache(store: BlobStore, cache_dir: Path) -> int:
    n = 0
    for name in store.list_names(CACHE_CONTAINER):
        dest = cache_dir / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(store.download(CACHE_CONTAINER, name))
        n += 1
    return n


def push_cache(store: BlobStore, cache_dir: Path) -> int:
    """只回推 Blob 上沒有的檔(overwrite=False):快取鍵為 PDF sha256,同鍵即同結果,既有者不動。"""
    existing = set(store.list_names(CACHE_CONTAINER))
    n = 0
    for p in sorted(cache_dir.rglob("*.json")):
        name = p.relative_to(cache_dir).as_posix()
        if name in existing:
            continue
        n += store.upload(CACHE_CONTAINER, name, p.read_bytes(), overwrite=False)
    return n


def upload_dir(store: BlobStore, prefix: str, out_dir: Path) -> list[str]:
    names = []
    for p in sorted(out_dir.rglob("*")):
        if p.is_file():
            name = f"{prefix}/{p.relative_to(out_dir).as_posix()}"
            store.upload(RUNS_CONTAINER, name, p.read_bytes())
            names.append(name)
    return names


def _write_meta(store: BlobStore, prefix: str, meta: dict[str, Any]) -> None:
    body = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")
    store.upload(RUNS_CONTAINER, f"{prefix}/{META_NAME}", body)


def _say(msg: str) -> None:
    print(f"[diag] {msg}", flush=True)


def diag(*, store: BlobStore | None = None, token_provider: Callable[[], str] | None = None, hold: int = 0) -> int:
    """逐階段自我檢查(2026-10-04 首次 Job 9 秒無聲失敗後補):印每階段結果、第一個失敗即以該階段編號退出;
    `hold` 秒停住讓 `az containerapp job logs show --follow` 來得及串流(主控台日誌入 Log Analytics 有延遲)。
    不印任何 token 值。"""
    import time

    keys = sorted(k for k in os.environ if k.startswith(("CLAIMCREW", "AZURE_", "GIT_SHA", "IDENTITY_", "MSI_"))
                  or k == "REVIEW_WEBHOOK_URL")
    _say(f"env python={sys.version.split()[0]} cwd={os.getcwd()} uid={os.getuid()} keys={keys}")
    stages: list[tuple[str, Callable[[], str]]] = []

    def _store() -> BlobStore:
        nonlocal store
        if store is None:
            store = AzureBlobStore()
        return store
    stages.append(("blob-store", lambda: f"ok account={os.environ.get(STORAGE_ENV, '?')}" if _store() else "ok"))
    stages.append(("list-runs", lambda: f"{len(_store().list_names(RUNS_CONTAINER))}"))
    stages.append(("list-cache", lambda: f"{len(_store().list_names(CACHE_CONTAINER))}"))

    def _token() -> str:
        tp = token_provider
        if tp is None:
            from app.pipeline.auth import entra_token_provider

            tp = entra_token_provider()
        return f"ok len={len(tp())}"
    stages.append(("cognitive-token", _token))
    for i, (name, fn) in enumerate(stages, start=2):
        try:
            _say(f"{name} {fn()}")
        except Exception as e:  # noqa: BLE001 — 診斷模式要把例外印成一行
            _say(f"{name} FAIL {type(e).__name__}: {str(e)[:400]}")
            if hold:
                time.sleep(hold)
            return i
    if hold:
        _say(f"hold {hold}s")
        time.sleep(hold)
    _say("all stages ok")
    return 0


def main(argv: list[str] | None = None, *, store: BlobStore | None = None,
         run: Callable[[list[str]], int] | None = None, now: datetime | None = None,
         work_dir: Path | None = None, sha: str | None = None,
         token_provider: Callable[[], str] | None = None, queue: MessageQueue | None = None) -> int:
    extra = list(sys.argv[1:] if argv is None else argv)
    print(f"cloud_run start sha={sha or git_sha()} args={extra}", flush=True)
    # --diag / --hold 亦可由環境變數觸發:az containerapp job start --args 吃不下 -- 開頭的值,--env-vars 可以
    if "--diag" in extra or os.environ.get("CLAIMCREW_DIAG") == "1":
        hold_env = int(os.environ.get("CLAIMCREW_HOLD", "0"))
        hold = int(extra[extra.index("--hold") + 1]) if "--hold" in extra else hold_env
        return diag(store=store, token_provider=token_provider, hold=hold)
    store = store or AzureBlobStore()
    if run is None:
        from tools.run_pipeline import main as run_pipeline_main

        run = run_pipeline_main
    started = now or datetime.now(UTC)
    sha = sha or git_sha()
    if "--from-queue" in extra:
        extra.remove("--from-queue")
        return _from_queue(queue or AzureQueue(), store, run, extra, started, sha, work_dir)
    return _execute(store, run, [*BASE_ARGS], extra, started, sha, work_dir)


def _from_queue(queue: MessageQueue, store: BlobStore, run: Callable[[list[str]], int], extra: list[str],
                started: datetime, sha: str, work_dir: Path | None) -> int:
    """一次執行只取一則訊息;先刪後跑(至多一次:崩潰不會被 KEDA 重觸發而重複計 LLM 費)。"""
    msg = queue.receive()
    if msg is None:
        print("cloud_run: queue empty", flush=True)
        return 0
    msg_id, receipt, text = msg
    queue.delete(msg_id, receipt)
    request = ""
    try:
        container, name = parse_event(text)
        request = f"{container}/{name}"
        if container != INBOX_CONTAINER or not name.startswith(REQUEST_PREFIX) or not name.endswith(".json"):
            raise ValueError(f"非請求位置: {request}")
        case_ids = parse_request(store.download(container, name))
    except Exception as e:  # noqa: BLE001 — 壞請求(含 blob 不存在)一律留 failed meta、不跑管線
        rid = run_id(started, sha)
        meta = {"run_id": rid, "git_sha": sha, "started_utc": started.isoformat(), "request": request,
                "status": "failed", "exit_code": 2, "error": f"bad request: {e}"[:1500]}
        _write_meta(store, f"runs/{rid}", meta)
        print(f"cloud_run {rid}: failed exit=2 bad request {request}", flush=True)
        return 2
    return _execute(store, run, ["--strategy", "full", *case_ids], extra, started, sha, work_dir, request=request)


def _execute(store: BlobStore, run: Callable[[list[str]], int], base_args: list[str], extra: list[str],
             started: datetime, sha: str, work_dir: Path | None, *, request: str | None = None) -> int:
    rid = run_id(started, sha)
    prefix = f"runs/{rid}"
    work = work_dir or Path(tempfile.mkdtemp(prefix="claimcrew-"))
    out_dir, cache_dir = work / "out", work / "cache"
    pipeline_args = [*base_args, "--out", str(out_dir), "--di-cache", str(cache_dir), "--run-tag", rid, *extra]
    meta: dict[str, Any] = {"run_id": rid, "git_sha": sha, "started_utc": started.isoformat(),
                            "pipeline_args": pipeline_args, "status": "running", "exit_code": None,
                            "cache_pulled": 0, "cache_pushed": 0, "uploaded": 0, "error": ""}
    if request is not None:
        meta["request"] = request
    _write_meta(store, prefix, meta)  # ① 先留證據
    rc = 1
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        meta["cache_pulled"] = pull_cache(store, cache_dir)
        try:
            rc = run(pipeline_args)
        except Exception:  # noqa: BLE001 — 管線例外也要留 meta 與輸出
            meta["error"] = traceback.format_exc()[-1500:]
            rc = 1
        if out_dir.is_dir():
            meta["uploaded"] = len(upload_dir(store, prefix, out_dir))
        meta["cache_pushed"] = push_cache(store, cache_dir)
    except Exception:  # noqa: BLE001
        meta["error"] = (meta["error"] + "\n" + traceback.format_exc()[-1500:]).strip()
        rc = 1
    meta["status"] = "succeeded" if rc == 0 else "failed"
    meta["exit_code"] = rc
    meta["finished_utc"] = datetime.now(UTC).isoformat()
    _write_meta(store, prefix, meta)
    print(f"cloud_run {rid}: {meta['status']} exit={rc} uploaded={meta['uploaded']} "
          f"cache pulled={meta['cache_pulled']} pushed={meta['cache_pushed']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
