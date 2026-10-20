"""執行紀錄版本戳記(2026-10-06):桌機兩輪實測跑在舊程式卻無從察覺(runbook W21),
`_run.jsonl` 每列帶 code_version = <短 SHA>[+dirty],開頭亦印出。"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from app.pipeline import runner
from tests.test_pipeline import INDEX, FakeAOAI, FakeDI, _bundle_payload, _case, _decision_payload
from tools import run_pipeline


def test_code_version_is_short_sha_with_optional_dirty_flag():
    v = run_pipeline.code_version()
    assert re.fullmatch(r"[0-9a-f]{7,12}(\+dirty)?", v), v


def test_code_version_marks_dirty_when_tracked_file_modified(monkeypatch: pytest.MonkeyPatch):
    outs = {("rev-parse",): "abc1234\n", ("status",): " M app/x.py\n"}
    monkeypatch.setattr(run_pipeline, "_git", lambda *a: outs[(a[0],)])
    assert run_pipeline.code_version() == "abc1234+dirty"


def test_code_version_unknown_when_git_unavailable(monkeypatch: pytest.MonkeyPatch):
    def boom(*a: str) -> str:
        raise subprocess.CalledProcessError(128, "git")
    monkeypatch.setattr(run_pipeline, "_git", boom)
    monkeypatch.delenv("GIT_SHA", raising=False)
    assert run_pipeline.code_version() == "unknown"


def test_code_version_falls_back_to_git_sha_env_in_image(monkeypatch: pytest.MonkeyPatch):
    """雲端映像無 .git:改讀建置時注入的 GIT_SHA(2026-10-08 v16 日誌印 unknown,run_meta 卻有 SHA)。"""
    def boom(*a: str) -> str:
        raise FileNotFoundError("git")
    monkeypatch.setattr(run_pipeline, "_git", boom)
    monkeypatch.setenv("GIT_SHA", "62eb96d")
    assert run_pipeline.code_version() == "62eb96d"
    monkeypatch.setenv("GIT_SHA", "unknown")   # Dockerfile ARG 預設值
    assert run_pipeline.code_version() == "unknown"


def test_run_record_carries_code_version(tmp_path: Path):
    cid = "C001"
    aoai = FakeAOAI(_bundle_payload(cid), _decision_payload(cid, "POL-A", "第7條"))
    runner.run_case(_case(cid), "full", FakeDI(_case(cid)), aoai, None, INDEX, tmp_path, code_version="abc1234")
    row = json.loads((tmp_path / "_run.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["code_version"] == "abc1234"
