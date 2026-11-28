"""Lossless, bounded compressed history shards; legacy JSON is read-only."""
import gzip
import json
from pathlib import Path

SHARD_BYTES = 4 * 1024 * 1024

def load_history(root=Path(".")):
    root = Path(root)
    directory = root / "mlplus_player_history"
    manifest = directory / "manifest.json"
    if manifest.exists():
        meta = json.loads(manifest.read_text())
        rows = []
        for name in meta["files"]:
            if Path(name).name != name:
                raise ValueError("Invalid history shard path")
            rows.extend(json.loads(gzip.decompress((directory / name).read_bytes())))
        if len(rows) != meta["records"]:
            raise ValueError("Incomplete MLPlus player history")
        return rows
    legacy = root / "mlplus_player_history.json"
    return json.loads(legacy.read_text()) if legacy.exists() else []

def save_history(rows, root=Path(".")):
    directory = Path(root) / "mlplus_player_history"
    directory.mkdir(exist_ok=True, parents=True)
    names, batch, size = [], [], 2
    def flush():
        nonlocal batch, size
        if not batch:
            return
        name = f"part-{len(names):06d}.json.gz"
        raw = b"[" + b",".join(batch) + b"]"
        (directory/name).write_bytes(gzip.compress(raw, mtime=0))
        names.append(name)
        batch, size = [], 2
    for row in rows:
        raw = json.dumps(row, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode()
        if len(raw)+2 > SHARD_BYTES:
            raise ValueError("Single history record exceeds safe shard limit")
        if size+len(raw)+1 > SHARD_BYTES:
            flush()
        batch.append(raw)
        size += len(raw)+1
    flush()
    manifest = directory/"manifest.json"
    temporary = directory/"manifest.tmp"
    temporary.write_text(json.dumps({"version":1,"records":len(rows),"files":names},separators=(",",":"))+"\n")
    temporary.replace(manifest)
