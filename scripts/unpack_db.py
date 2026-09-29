"""Unpack the gzipped database during the Docker build, and fail loudly if the file is a
Git LFS pointer (the host checked out the repo without pulling LFS objects)."""
import gzip
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1] if len(sys.argv) > 1 else "/data/nba_serve.db.gz")
dst = src.with_suffix("")  # strip .gz
head = src.read_bytes()[:64]
if head.startswith(b"version https://git-lfs"):
    sys.exit(f"{src} is a Git LFS pointer, not the database. Enable Git LFS on the host "
             "(Render does this for LFS-tracked files) or run `git lfs pull` before building.")
if head[:2] != b"\x1f\x8b":
    sys.exit(f"{src} is not a gzip file")
with gzip.open(src, "rb") as f, open(dst, "wb") as g:
    shutil.copyfileobj(f, g, 1 << 20)
if dst.read_bytes()[:16] != b"SQLite format 3\x00":
    sys.exit(f"{dst} is not a SQLite database")
src.unlink()
print(f"unpacked {dst} ({dst.stat().st_size / 1e6:.0f} MB)")
