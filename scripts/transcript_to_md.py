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
        print(f"  Skipped (the .md is already there): {md_path.name}")
        return False
    meta, records = transcript_mod.load(path)
    if not records:
        print(f"  Skipped (no final sentences): {path.name}")
        return False
    md_path.write_text(transcript_mod.render(meta, records), encoding="utf-8")
    print(f"  Wrote: {md_path}  ({len(records)} sentences)")
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("path", nargs="?", help="the .jsonl to convert (default: the newest one)")
    p.add_argument("--all", action="store_true", help="convert every record that has no .md")
    p.add_argument("--force", action="store_true", help="rewrite the .md even if it is already there")
    p.add_argument("--dir", default=config.TRANSCRIPT_DIR, help="where the meeting records are kept")
    args = p.parse_args()

    if args.path:
        targets = [Path(args.path)]
    else:
        # **Filter on the prefix.** The place where records are kept can be
        # changed, so unrelated .jsonl files may sit in the same folder.
        found = sorted(Path(args.dir).glob(f"{config.TRANSCRIPT_PREFIX}*.jsonl"))
        if not found:
            print(f"No meeting records in: {args.dir}")
            return 1
        targets = found if args.all else found[-1:]

    print(f"{len(targets)} files")
    done = sum(convert(t, force=args.force or bool(args.path)) for t in targets)
    print(f"Converted: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
