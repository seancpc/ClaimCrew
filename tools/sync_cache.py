"""SPEC-t T4:DI 磁碟快取 ↔ Blob `di-cache` 容器同步(桌機把 check\\di_cache 推上去,雲端 Job 啟動時拉下來)。

用法(桌機;az login 身分需 Storage Blob Data Contributor):
  $env:CLAIMCREW_STORAGE_ACCOUNT = (az storage account list -g rg-claimcrew --query "[0].name" -o tsv)
  python tools\\sync_cache.py push check\\di_cache      # 只上傳 Blob 上沒有的檔,不覆蓋
  python tools\\sync_cache.py pull check\\di_cache      # 下載全部到本機
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.cloud_run import AzureBlobStore, BlobStore, pull_cache, push_cache  # noqa: E402


def main(argv: list[str] | None = None, *, store: BlobStore | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["push", "pull"])
    ap.add_argument("cache_dir")
    args = ap.parse_args(argv)
    store = store or AzureBlobStore()
    d = Path(args.cache_dir)
    if args.action == "push":
        print(f"push:上傳 {push_cache(store, d)} 個新檔(既有不覆蓋)")
    else:
        d.mkdir(parents=True, exist_ok=True)
        print(f"pull:下載 {pull_cache(store, d)} 個檔到 {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
