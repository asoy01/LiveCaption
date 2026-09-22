#!/usr/bin/env python3
"""Rebuild the readable form (`.md`) from a meeting record (`.jsonl`).

The engine writes the `.md` when it ends. **It does not when the power goes
out.** That is when you use this. The `.jsonl` is written and flushed one
sentence at a time, so everything up to the moment it died is still there.

    pixi run python scripts/transcript_to_md.py                       # fix the newest one
    pixi run python scripts/transcript_to_md.py local/transcripts/live-caption_2026-09-08_143012.jsonl
    pixi run python scripts/transcript_to_md.py --all                 # every one that has no .md

The format is produced by calling the engine (`render()` in
`src/live_caption/transcript.py`). **Do not copy it.** That was done once, and
the two drifted apart.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from live_caption import config  # noqa: E402
from live_caption import transcript as transcript_mod  # noqa: E402


def convert(path: Path, force: bool = False) -> bool:
    md_path = path.with_suffix(".md")
    if md_path.exists() and not force:
        print(f"  飛ばす（既にある）: {md_path.name}")
        return False
    meta, records = transcript_mod.load(path)
    if not records:
        print(f"  飛ばす（確定した文が無い）: {path.name}")
        return False
    md_path.write_text(transcript_mod.render(meta, records), encoding="utf-8")
    print(f"  書いた: {md_path}  （{len(records)} 文）")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", nargs="?", help="変換する .jsonl（省略すると最新のもの）")
    p.add_argument("--all", action="store_true", help=".md が無いものを全部変換する")
    p.add_argument("--force", action="store_true", help="既に .md があっても書き直す")
    p.add_argument("--dir", default=config.TRANSCRIPT_DIR, help="記録の置き場")
    args = p.parse_args()

    if args.path:
        targets = [Path(args.path)]
    else:
        # **Filter on the prefix.** The place where records are kept can be
        # changed, so unrelated .jsonl files may sit in the same folder.
        found = sorted(Path(args.dir).glob(f"{config.TRANSCRIPT_PREFIX}*.jsonl"))
        if not found:
            print(f"記録が無い: {args.dir}")
            return 1
        targets = found if args.all else found[-1:]

    print(f"{len(targets)} 件")
    done = sum(convert(t, force=args.force or bool(args.path)) for t in targets)
    print(f"変換した: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
