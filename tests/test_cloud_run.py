"""SPEC-t T4:雲端 Job 入口 `tools/cloud_run.py`——
run_meta 先寫、拉 DI 快取、跑管線、整目錄上傳、回推快取不覆蓋、失敗非零。"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tools import cloud_run as cr


class FakeStore:
    """以 (container, name) → bytes 模擬 Blob;記錄每次 upload 順序。"""

    def __init__(self) -> None:
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.uploads: list[tuple[str, str]] = []

    def upload(self, container: str, name: str, data: bytes, *, overwrite: bool = True) -> bool:
        if not overwrite and (container, name) in self.blobs:
            return False
        self.blobs[(container, name)] = data
        self.uploads.append((container, name))
        return True

    def list_names(self, container: str, prefix: str = "") -> list[str]:
        return sorted(n for c, n in self.blobs if c == container and n.startswith(prefix))

    def download(self, container: str, name: str) -> bytes:
        return self.blobs[(container, name)]


def _fake_run(out_dir_holder: dict[str, Path], rc: int = 0, files: dict[str, str] | None = None):
    def run(argv: list[str]) -> int:
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        out_dir_holder["out"] = out
        out_dir_holder["argv"] = argv  # type: ignore[assignment]
        for name, text in (files or {"C001.json": "{}", "_run.jsonl": "{}\n"}).items():
            (out / name).write_text(text, encoding="utf-8")
        return rc
    return run


def test_t4_run_id_format():
    assert cr.run_id(datetime(2026, 10, 4, 1, 12, 59, tzinfo=UTC), "abc1234def") == "20261004T0112-abc1234"


def test_t4_meta_written_before_pipeline_and_final_status(tmp_path, monkeypatch):
    store = FakeStore()
    holder: dict[str, Any] = {}
    seen_meta_before: list[bool] = []

    def run(argv: list[str]) -> int:
        seen_meta_before.append(any(n.endswith("run_meta.json") for _, n in store.uploads))
        return _fake_run(holder)(argv)
    monkeypatch.setenv("GIT_SHA", "deadbeefcafe")
    rc = cr.main(["--review"], store=store, run=run, now=datetime(2026, 10, 4, 1, 12, tzinfo=UTC), work_dir=tmp_path)
    assert rc == 0 and seen_meta_before == [True]
    meta = json.loads(store.download(cr.RUNS_CONTAINER, "runs/20261004T0112-deadbee/run_meta.json"))
    assert meta["git_sha"] == "deadbeefcafe" and meta["status"] == "succeeded" and meta["exit_code"] == 0
    assert meta["pipeline_args"][:4] == ["--all", "--strategy", "full", "--out"] and "--review" in meta["pipeline_args"]
    assert "--di-cache" in holder["argv"]


def test_t4_outputs_uploaded_including_run_log_and_digest(tmp_path):
    store = FakeStore()
    holder: dict[str, Any] = {}
    files = {"C001.json": "{}", "C001.bundle.json": "{}", "_run.jsonl": "{}\n", "_review_digest.html": "<html>"}
    rc = cr.main([], store=store, run=_fake_run(holder, files=files), now=datetime(2026, 1, 1, tzinfo=UTC),
                 work_dir=tmp_path, sha="0000000")
    assert rc == 0
    names = store.list_names(cr.RUNS_CONTAINER, "runs/20260101T0000-0000000/")
    assert names == sorted(["runs/20260101T0000-0000000/" + n for n in [*files, "run_meta.json"]])


def test_t4_cache_pull_then_push_without_overwrite(tmp_path):
    store = FakeStore()
    store.upload(cr.CACHE_CONTAINER, "diagnosis-cert-v3/aaa.json", b'{"old":1}')
    holder: dict[str, Any] = {}

    def run(argv: list[str]) -> int:
        cache = Path(argv[argv.index("--di-cache") + 1])
        assert (cache / "diagnosis-cert-v3" / "aaa.json").read_bytes() == b'{"old":1}'  # 已拉下
        (cache / "diagnosis-cert-v3" / "aaa.json").write_bytes(b'{"changed":1}')  # 本機改動不得回推覆蓋
        (cache / "prebuilt-layout" / "bbb.json").parent.mkdir(parents=True, exist_ok=True)
        (cache / "prebuilt-layout" / "bbb.json").write_bytes(b'{"new":1}')
        return _fake_run(holder)(argv)
    rc = cr.main([], store=store, run=run, now=datetime(2026, 1, 1, tzinfo=UTC), work_dir=tmp_path, sha="1111111")
    assert rc == 0
    assert store.download(cr.CACHE_CONTAINER, "diagnosis-cert-v3/aaa.json") == b'{"old":1}'
    assert store.download(cr.CACHE_CONTAINER, "prebuilt-layout/bbb.json") == b'{"new":1}'
    meta = json.loads(store.download(cr.RUNS_CONTAINER, "runs/20260101T0000-1111111/run_meta.json"))
    assert meta["cache_pulled"] == 1 and meta["cache_pushed"] == 1


def test_t4_pipeline_failure_still_uploads_and_returns_nonzero(tmp_path):
    store = FakeStore()
    holder: dict[str, Any] = {}
    rc = cr.main([], store=store, run=_fake_run(holder, rc=1), now=datetime(2026, 1, 1, tzinfo=UTC),
                 work_dir=tmp_path, sha="2222222")
    assert rc == 1
    meta = json.loads(store.download(cr.RUNS_CONTAINER, "runs/20260101T0000-2222222/run_meta.json"))
    assert meta["status"] == "failed" and meta["exit_code"] == 1
    assert (cr.RUNS_CONTAINER, "runs/20260101T0000-2222222/_run.jsonl") in store.blobs


def test_t4_exception_in_pipeline_is_recorded_not_raised(tmp_path):
    store = FakeStore()

    def boom(argv: list[str]) -> int:
        raise RuntimeError("401 Unauthorized from DI")
    rc = cr.main([], store=store, run=boom, now=datetime(2026, 1, 1, tzinfo=UTC), work_dir=tmp_path, sha="3333333")
    assert rc == 1
    meta = json.loads(store.download(cr.RUNS_CONTAINER, "runs/20260101T0000-3333333/run_meta.json"))
    assert meta["status"] == "failed" and "401" in meta["error"]


def test_t4_sync_cache_cli_push_and_pull(tmp_path):
    from tools import sync_cache as sc
    store = FakeStore()
    local = tmp_path / "cache"
    (local / "m1").mkdir(parents=True)
    (local / "m1" / "x.json").write_bytes(b"1")
    assert sc.main(["push", str(local)], store=store) == 0 and store.list_names(cr.CACHE_CONTAINER) == ["m1/x.json"]
    assert sc.main(["push", str(local)], store=store) == 0 and len(store.uploads) == 1  # 第二次不重傳
    dest = tmp_path / "pull"
    assert sc.main(["pull", str(dest)], store=store) == 0 and (dest / "m1" / "x.json").read_bytes() == b"1"


def test_t4_run_pipeline_main_accepts_argv(tmp_path):
    from tools import run_pipeline as rp
    with pytest.raises(SystemExit):
        rp.main(["--help"])


# ---------- --diag(2026-10-04 首次 Job 9 秒無聲失敗、主控台日誌未入 Log Analytics 後補)----------
def test_t4_diag_reports_each_stage_and_stops_at_first_failure(capsys):
    store = FakeStore()
    store.upload(cr.CACHE_CONTAINER, "m/x.json", b"1")
    rc = cr.diag(store=store, token_provider=lambda: "s3cr3t-jwt-value", hold=0)
    out = capsys.readouterr().out
    assert rc == 0
    for stage in ("env", "blob-store", "list-runs", "list-cache 1", "cognitive-token ok"):
        assert stage in out
    assert "s3cr3t" not in out  # token 值不得印出

    def bad_token() -> str:
        raise RuntimeError("IMDS 401 nope")
    rc2 = cr.diag(store=store, token_provider=bad_token, hold=0)
    out2 = capsys.readouterr().out
    assert rc2 == 5 and "cognitive-token FAIL" in out2 and "IMDS 401" in out2


def test_t4_main_dispatches_diag_without_running_pipeline(tmp_path):
    store = FakeStore()
    called: list[Any] = []
    rc = cr.main(["--diag"], store=store, run=lambda a: called.append(a) or 0, now=datetime(2026, 1, 1, tzinfo=UTC),
                 work_dir=tmp_path, sha="4444444", token_provider=lambda: "t")
    assert rc == 0 and called == [] and store.list_names(cr.RUNS_CONTAINER) == []


def test_t4_diag_can_be_triggered_by_env(tmp_path, monkeypatch):
    """az containerapp job start --args 無法傳 -- 開頭的值(被 az 自己的 argparse 吃掉),改以 env 觸發。"""
    store = FakeStore()
    monkeypatch.setenv("CLAIMCREW_DIAG", "1")
    monkeypatch.setenv("CLAIMCREW_HOLD", "0")
    called: list[Any] = []
    rc = cr.main([], store=store, run=lambda a: called.append(a) or 0, now=datetime(2026, 1, 1, tzinfo=UTC),
                 work_dir=tmp_path, sha="5555555", token_provider=lambda: "t")
    assert rc == 0 and called == [] and store.list_names(cr.RUNS_CONTAINER) == []
