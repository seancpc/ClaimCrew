#!/usr/bin/env bash
# claim schema 轉換層手動變異測試(6 mutants;植入→跑套件→git 還原)
# 啟動前殘留偵測:原字串必須恰好各出現一次(內部開發規則 固定解法 15)
set -uo pipefail
TARGET=app/schemas/claim.py
check() {
  n=$(grep -cF "$1" "$TARGET")
  [ "$n" -eq "$2" ] || { echo "殘留偵測失敗:'$1' 出現 $n 次(應為 $2),先 git diff $TARGET"; exit 1; }
}
check 'node["additionalProperties"] = False' 1
check 'prop.pop("anyOf")' 1
check 'le=1.0' 2
# 還原用「工作區副本」而非 git checkout:後者會把尚未 commit 的修改一併抹掉(2026-09-02 實際發生)
BACKUP=$(mktemp)
cp "$TARGET" "$BACKUP"
restore() { cp "$BACKUP" "$TARGET"; }
trap 'restore; rm -f "$BACKUP"' EXIT INT TERM HUP
run_mutant() {
  sed -i "$2" "$TARGET"
  if python3 -m pytest tests/ -q >/dev/null 2>&1; then echo "SURVIVED: $1"; else echo "KILLED:   $1"; fi
  restore
}
run_mutant "M1 additionalProperties=True" 's/node\["additionalProperties"\] = False/node["additionalProperties"] = True/'
run_mutant "M2 required 少最後一欄" 's/node\["required"\] = list(node\["properties"\].keys())/node["required"] = list(node["properties"].keys())[:-1]/'
run_mutant "M3 不遞迴進 array items" 's/_enforce_subset(node\["items"\])/pass/'
run_mutant "M4 anyOf 未移除" 's/prop.pop("anyOf")/pass/'
run_mutant "M5 信心上限 1.0→2.0" 's/le=1.0/le=2.0/'
run_mutant "M6 anyOf 判斷子集改相等" 's/<= {"number", "string"}/== {"number"}/'
cmp -s "$BACKUP" "$TARGET" && echo "還原驗證:無殘留(與執行前副本一致)" || { echo "警告:有殘留!"; exit 1; }
