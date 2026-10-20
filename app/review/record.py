"""SPEC-am 覆核閉環:Logic App ② 寫入 runs/reviews-inbox/<guid>.json 的覆核紀錄。

Logic App 端不驗證、不以使用者輸入組路徑(檔名 = guid()),欄位原樣落盤;合法性一律在此離線驗證,
不合格者計入 invalid、不採計。同一 (run, 案號) 多次送出以 submitted_at 最晚者為準。
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.schemas.claim import ClaimDecision

RUN_ID_PATTERN = r"^[A-Za-z0-9_.\-]{1,64}$"  # 雲端 run_id 或桌機輸出資料夾名(摘要主旨同一標記)
CLAIM_ID_PATTERN = r"^C\d{3}$"


class ReviewRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str = Field(pattern=RUN_ID_PATTERN)
    claim_id: str = Field(pattern=CLAIM_ID_PATTERN)
    decision: ClaimDecision
    amount: Decimal | None = None
    note: str = Field(default="", max_length=500)
    submitted_at: datetime
    user_agent: str = Field(default="", max_length=500)

    @field_validator("amount", mode="before")
    @classmethod
    def _amount(cls, v: Any) -> Decimal | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            d = Decimal(str(v).strip())
        except InvalidOperation as e:
            raise ValueError(f"金額不是數字:{v!r}") from e
        if not d.is_finite() or d < 0:
            raise ValueError(f"金額須為非負數:{v!r}")
        return d


def load_reviews(reviews_dir: Path) -> tuple[list[ReviewRecord], int]:
    """回傳 (合法紀錄, 無效筆數);壞 JSON 與驗證失敗皆計無效。"""
    records: list[ReviewRecord] = []
    invalid = 0
    for f in sorted(reviews_dir.glob("*.json")):
        try:
            records.append(ReviewRecord.model_validate(json.loads(f.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, ValidationError, UnicodeDecodeError):
            invalid += 1
    return records, invalid


def latest_by_case(records: list[ReviewRecord], run_id: str) -> tuple[dict[str, ReviewRecord], int]:
    """只取該輪;同案多筆以 submitted_at 最晚者為準(與檔案順序無關),回傳 (案號 → 紀錄, 被覆蓋筆數)。"""
    mine = [r for r in records if r.run_id == run_id]
    latest: dict[str, ReviewRecord] = {}
    for r in mine:
        cur = latest.get(r.claim_id)
        if cur is None or r.submitted_at > cur.submitted_at:
            latest[r.claim_id] = r
    return latest, len(mine) - len(latest)
