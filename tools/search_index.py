"""條款庫入 Azure AI Search(Free tier,hybrid = BM25 + 向量)——建索引 / 灌資料 / 查詢 / 72 案召回自測。

★ REST 細節(Search 2024-07-01、Azure OpenAI embeddings 2024-10-21)依訓練知識撰寫,首跑以真回應校正。

設計(SPEC 決策 5 / ADR-005;2026-10-01 方案 A):
- 一條條款一份文件,id = `POL-A-7` / `POL-A-app1`;`clause_no` 保留索引鍵原樣(第7條 / 附錄一)供 Gate 反查
- content 走 BM25(繁中分析器),content_vector 走 text-embedding-3-small(1536 維)
- 索引是「產物」不入版控;重建 = 本腳本再跑一次(拆建原則)

用法(桌機,venv 內;金鑰只進環境變數):
    $env:SEARCH_KEY = (az search admin-key show --service-name srch-claimcrew -g rg-claimcrew --query primaryKey -o tsv)
    $env:AOAI_KEY   = (az cognitiveservices account keys list -n oai-claimcrew -g rg-claimcrew --query key1 -o tsv)
    python tools/search_index.py create            # 建(或重建)索引並灌入 100 條
    python tools/search_index.py query "出院後十四日內再住院" --policy POL-A -k 5
    python tools/search_index.py selftest -k 5     # 72 案:以文件內容組查詢,對真值 clause_basis 算 recall@k
    python tools/search_index.py selftest -k 10 --core   # 策略 core+search(核心集 ∪ top-10)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate.policy_index import Clause, core_clause_nos, load_policy_index  # noqa: E402
from app.pipeline.search_client import (  # noqa: E402
    EMBED_DIM,
    INDEX_NAME,
    embed,
    hybrid_search,
    search_rest,
)

CLAIMS_DIR = ROOT / "data" / "claims"


# ---------- 文件 ----------
def doc_id(policy_code: str, clause_no: str) -> str:
    """索引鍵只准英數、底線、連字號:第7條 → POL-A-7;附錄一 → POL-A-app1。"""
    m = re.match(r"^第(\d+)條$", clause_no)
    if m:
        return f"{policy_code}-{m.group(1)}"
    m = re.match(r"^附錄(.+)$", clause_no)
    if m:
        from app.gate.policy_index import cn_to_int
        return f"{policy_code}-app{cn_to_int(m.group(1))}"
    raise ValueError(f"未知條號格式:{clause_no}")


def clause_docs() -> list[dict[str, Any]]:
    docs = []
    for code, clauses in load_policy_index().items():
        for c in clauses.values():
            docs.append({
                "id": doc_id(code, c.clause_no),
                "policy_code": c.policy_code,
                "clause_no": c.clause_no,
                "title": c.title,
                "content": c.text,
            })
    return docs


INDEX_SCHEMA: dict[str, Any] = {
    "name": INDEX_NAME,
    "fields": [
        {"name": "id", "type": "Edm.String", "key": True, "filterable": True},
        {"name": "policy_code", "type": "Edm.String", "filterable": True, "facetable": True},
        {"name": "clause_no", "type": "Edm.String", "filterable": True},
        {"name": "title", "type": "Edm.String", "searchable": True, "analyzer": "zh-Hant.microsoft"},
        {"name": "content", "type": "Edm.String", "searchable": True, "analyzer": "zh-Hant.microsoft"},
        {
            "name": "content_vector", "type": "Collection(Edm.Single)", "searchable": True,
            "dimensions": EMBED_DIM, "vectorSearchProfile": "vp-hnsw",
        },
    ],
    "vectorSearch": {
        "algorithms": [{"name": "hnsw", "kind": "hnsw"}],
        "profiles": [{"name": "vp-hnsw", "algorithm": "hnsw"}],
    },
}


def create_index() -> None:
    docs = clause_docs()
    print(f"條款文件 {len(docs)} 份;建立索引 {INDEX_NAME} …")
    search_rest(f"/indexes/{INDEX_NAME}", "PUT", INDEX_SCHEMA)
    t0 = time.time()
    vectors = embed([f"{d['title']}。{d['content']}" for d in docs])
    print(f"embedding {len(vectors)} 份,{time.time() - t0:.1f}s,維度 {len(vectors[0])}")
    for d, v in zip(docs, vectors, strict=True):
        d["content_vector"] = v
        d["@search.action"] = "mergeOrUpload"
    for i in range(0, len(docs), 50):
        resp = search_rest(f"/indexes/{INDEX_NAME}/docs/index", "POST", {"value": docs[i:i + 50]})
        bad = [r for r in resp.get("value", []) if not r.get("status")]
        if bad:
            raise SystemExit(f"灌入失敗 {len(bad)} 筆:{bad[:3]}")
    for _ in range(20):  # 索引非同步可見,輪詢至文件數到齊(首跑實測 3 秒後仍回 0)
        time.sleep(3)
        stats = search_rest(f"/indexes/{INDEX_NAME}/stats")
        if stats.get("documentCount", 0) >= len(docs):
            break
    print(f"索引文件數 {stats.get('documentCount')},儲存 {stats.get('storageSize')} bytes")


def search(query: str, policy: str | None, k: int) -> list[dict[str, Any]]:
    return hybrid_search(query, policy, k)


# ---------- 自測 ----------
def case_query(claim: dict[str, Any]) -> tuple[str | None, str]:
    """以文件可見內容組查詢(不用真值),回傳 (policy_code 或 None, query)。"""
    d = claim["documents"]
    dc, rc, ap = d.get("diagnosis_certificate") or {}, d.get("medical_receipt") or {}, d.get("claim_application") or {}
    pn = ap.get("policy_number")
    policy = str(pn).rsplit("-", 2)[0] if pn else None  # POL-A-2026-01 → POL-A;申請書缺件/保單未知 → 不過濾
    parts = [ap.get("claim_type", ""), dc.get("diagnosis", ""), dc.get("treatment_summary", "")]
    parts += [it["item_name"] for it in rc.get("items") or []]  # 缺件案(診斷書/收據為 null)只用現有文件
    if d.get("incident_statement"):
        parts.append(d["incident_statement"]["statement_text"])
    return policy, ";".join(p for p in parts if p)


def selftest(k: int, index: dict[str, dict[str, Clause]], core: bool = False) -> int:
    """core=True:策略 core+search——固定附上該保單的結構性核心集,再聯集檢索 top-k。"""
    rows = []
    for case_dir in sorted(CLAIMS_DIR.iterdir()):
        truth_f, claim_f = case_dir / "truth.json", case_dir / "claim.json"
        if not (truth_f.is_file() and claim_f.is_file()):
            continue
        truth = json.loads(truth_f.read_text(encoding="utf-8"))
        basis = set(truth.get("clause_basis", []))
        if not basis:
            continue
        policy, q = case_query(json.loads(claim_f.read_text(encoding="utf-8")))
        hits = search(q, policy, k)
        got = {f"{h['policy_code']} {h['clause_no']}" for h in hits}
        if core and policy:
            got |= {f"{policy} {cn}" for cn in core_clause_nos(index[policy])}
        for h in hits:  # 回傳原文必須等於條款庫原文(Gate 引文比對的前提)
            if h["content"] != index[h["policy_code"]][h["clause_no"]].text:
                raise SystemExit(f"{h['id']} 內容與條款庫不一致")
        recall = len(basis & got) / len(basis)
        rows.append((truth["claim_id"], policy or "?", len(basis), recall, sorted(basis - got)))
    macro = sum(r[3] for r in rows) / len(rows)
    label = f"core+top{k}" if core else f"recall@{k}"
    print(f"{'case':<6}{'policy':<7}{'n':>3}  {label:<12}漏掉")
    for cid, pol, n, r, miss in rows:
        print(f"{cid:<6}{pol:<7}{n:>3}  {r:<12.2f}{' '.join(m.split(' ', 1)[1] for m in miss)}")
    print(f"\n案數 {len(rows)};macro {label} = {macro:.4f};全對 {sum(1 for r in rows if r[3] == 1.0)} 案")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create")
    q = sub.add_parser("query")
    q.add_argument("text")
    q.add_argument("--policy")
    q.add_argument("-k", type=int, default=5)
    st = sub.add_parser("selftest")
    st.add_argument("-k", type=int, default=5)
    st.add_argument("--core", action="store_true", help="策略 core+search:核心集 ∪ top-k")
    args = ap.parse_args()

    if args.cmd == "create":
        create_index()
    elif args.cmd == "query":
        for h in search(args.text, args.policy, args.k):
            print(f"{h['@search.score']:.3f}  {h['policy_code']} {h['clause_no']}({h['title']}) {h['content'][:40]}…")
    else:
        return selftest(args.k, load_policy_index(), core=args.core)
    return 0


if __name__ == "__main__":
    sys.exit(main())
