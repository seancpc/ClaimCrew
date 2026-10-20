#!/usr/bin/env bash
# ClaimCrew gauntlet 入口:一鍵重跑全部檢測層(EVIDENCE 數字唯一來源)
# 一律 python3 -m 呼叫,避免 PATH 工具版本漂移(內部開發規則 B1);任一層失敗即非零退出
set -euo pipefail
echo "=== 0. 工具版本 ==="
python3 --version
python3 -m pytest --version 2>&1 | head -1
python3 -m mypy --version
python3 -m ruff --version
python3 -c "import pydantic, hypothesis; from importlib.metadata import version; print('pydantic', pydantic.VERSION, '| hypothesis', hypothesis.__version__, '| jsonschema', version('jsonschema'), '| pip-audit', version('pip-audit'), '| pytest-randomly', version('pytest-randomly'))"
# 工具設定單一來源:pyproject.toml(mypy strict、ruff 規則集、coverage 來源)
echo "=== 1. 測試套件(隨機順序)+ coverage ==="
python3 -m pytest -q -p randomly --cov --cov-report=term-missing
echo "=== 2. 型別(strict)==="
python3 -m mypy
echo "=== 3. Lint ==="
python3 -m ruff check .
echo "=== 4. 供應鏈審計(釘選依賴)==="
python3 -m pip_audit -r requirements.txt -r requirements-dev.txt
echo "=== 5. 手動變異(schema 轉換層,6 mutants)==="
bash tools/mutation_schema.sh
echo "=== 5b. 手動變異(Gate 檢核 / 計算明細 / runner 加總,12 mutants + property 子集 4)==="
bash tools/mutation_gate.sh
bash tools/mutation_gate.sh property
echo "=== 6. Eval 指標管線自檢(真值當預測必須滿分)==="
python3 -m eval.run_eval --selfcheck
echo "=== GAUNTLET PASS ==="
