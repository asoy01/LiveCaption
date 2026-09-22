"""Keep a record of the meeting.

**Write the transcription output paired with the caption translated from
it.** With the captions alone, term errors cannot be traced afterwards,
because a misrecognition appears only on the transcription side (this is what
stage 2 of `docs/test-procedure.md` collects).

For a Japanese meeting this is "the Japanese transcript and the English
caption"; for an English meeting it is "the English transcript and the
Japanese caption". **The key names are `ja` (transcript) and `en` (caption)
regardless of the direction.** Past records and
`scripts/transcript_to_md.py` read these names, so do not change them.

## Two files are produced

**The directory is `local/transcripts/` by default**
(`config.TRANSCRIPT_DIR`). **The files pile up inside the caption PC, and are
downloaded from "Meeting records" on the control page.** To change it, use
`LIVECAPTION_SAVE_DIR` in `.env` or `--save-dir`.

    local/transcripts/live-caption_2026-09-08_143012.jsonl   1 line = 1 finalized sentence. Appended as it goes
    local/transcripts/live-caption_2026-09-08_143012.md      Readable form. Written at the end

**Put `live-caption_` in the name.** The directory can be changed to another
folder, and with a name made only of a date it is impossible to tell what the
file is.

**The `.jsonl` is the original. It is written and flushed each time a
sentence is finalized.** Writing everything at the end would lose a whole
meeting if the caption app crashed. Do not gamble a one-hour meeting on that.

The `.md` is assembled at the end. It is not left behind when the power is
cut, so in that case rebuild it from the `.jsonl` with
`scripts/transcript_to_md.py`.

## Do not crash during a meeting

Captions are not stopped when a write fails. Showing captions is the real
job, and the record is a by-product. On failure, print once to the screen and
from then on keep the records in memory only.

## Leave no file when not a single sentence came out

The app is sometimes started and stopped briefly, for example to check the
cabling. If empty records pile up, the records of real meetings become hard
to find. When there is no content, delete the file this run created.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config


def _clock(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


# Characters that cannot be used in a Windows file name. Control characters
# are dropped separately.
_BAD_NAME_CHARS = '\\/:*?"<>|'
# How much of the meeting name goes into the file name. This leaves room
# under the limit on the whole path.
NAME_IN_FILE_MAX = 40


def safe_filename(name: str) -> str:
    """Turn the meeting name into part of a file name.

    **Do not drop Japanese.** The meeting name goes into the file name so
    that the file can be found later. If a name such as "定例会議" were
    dropped and only the date were left, the file name would be useless.
    Windows paths handle UTF-8 as it is. The only things dropped are
    characters that cannot be used in a path, and control characters.

    This is not the same as `web._qr_filename`. That one goes into an HTTP
    header, so it has to be limited to ASCII and Japanese disappears. The
    requirements differ, so the two are kept separate.
    """
    out = "".join(
        "_" if (c in _BAD_NAME_CHARS or ord(c) < 32) else c
        for c in str(name)
    )
    # Collapse whitespace, and drop leading and trailing spaces and dots
    # (Windows dislikes a trailing dot).
    out = "_".join(out.split()).strip("._")
    return out[:NAME_IN_FILE_MAX]


class Transcript:
    """The record for one run of the app.

    `add()` is called only by the main event loop (`app.App._post`). What is
    written is a few hundred bytes, so it is written and flushed
    synchronously.
    """

    def __init__(self, directory: Path, meta: dict | None = None,
                 label: str = "") -> None:
        self.directory = Path(directory)
        self.meta = dict(meta or {})
        self.label = label
        self.started = time.time()
        self.ended: float | None = None
        stem = config.TRANSCRIPT_PREFIX + time.strftime(
            "%Y-%m-%d_%H%M%S", time.localtime(self.started))
        # **Put the meeting name into the file name.** When the app runs
        # unattended, several records land in a day, and the time alone is
        # not enough to find one later. The name is typed by a person, so it
        # contains characters that cannot be used in a file name.
        # `safe_filename` drops them.
        if label:
            slug = safe_filename(label)
            if slug:
                stem = f"{stem}_{slug}"
        self.path = self.directory / f"{stem}.jsonl"
        self.md_path = self.directory / f"{stem}.md"
        self.records: list[dict] = []
        self.error = ""
        self.closed = False
        self._fh = None

    # --- Called from the main app -------------------------------------------

    def open(self) -> None:
        """Create the file. No exception is raised on failure (a meeting is
        not stopped for the sake of the record)."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
        except OSError as exc:
            self._failed(exc)
            return
        self._write({"type": "meta", "started": _clock(self.started), **self.meta})

    def add(
        self,
        ja: str,
        en: list[str],
        sent: int = 0,
        when: float | None = None,
        *,
        cut: str = "",
        waited: float = 0.0,
        took: float = 0.0,
        total: float = 0.0,
        spec: bool = False,
        direction: str = "",
    ) -> None:
        """Record one finalized sentence.

        **The key names are `ja` / `en`, but their meaning is "the
        transcription output" and "the caption".** `ja` is the transcription
        output itself, and is not always Japanese. When the first half of a
        meeting is in English, English goes in as it is. If the caption
        direction is `en2ja`, English goes into `ja` and Japanese goes into
        `en`. **Do not change the key names.** Past records and
        `scripts/transcript_to_md.py` read these names.

        `en` holds the lines shown as captions, and is empty when the
        translation failed. **Do not throw it away even when it is empty.**
        It is needed to pick up misrecognitions later.

        `when` is **the time the transcription was finalized**. This function
        is called after the translation finishes, so reading the clock here
        would be off by 2 to 3 seconds. What the record needs is the time the
        words were spoken, not the time they were translated, so the caller
        passes it in.

        The last four are for tuning the delay. `cut` is the reason for
        finalizing (punct / force / idle / flush), `waited` is the number of
        seconds actually waited when the sentence was finalized on silence,
        `took` is the translation, and `total` is **the measured time from
        finalizing to the first caption.**
        **Without a measurement per sentence, there is no way to decide where
        to cut time.**
        """
        at = when if when is not None else time.time()
        record = {
            "type": "line",
            "at": time.strftime("%H:%M:%S", time.localtime(at)),
            "elapsed": round(at - self.started, 1),
            "ja": ja,
            "en": list(en),
            "sent": int(sent),
        }
        if cut:
            record["cut"] = cut
        # Nothing waits except the silence wait, so writing this otherwise
        # would be meaningless.
        if waited:
            record["waited"] = round(waited, 2)
        if took:
            record["took"] = round(took, 2)
        if total:
            record["total"] = round(total, 2)
        # A sentence where the speculative translation was a hit. On a hit,
        # total comes out smaller than took.
        if spec:
            record["spec"] = True
        # The caption direction. **It can be changed in the middle of a
        # meeting, so it is kept per sentence.** Writing it once in the meta
        # line would make the record after a switch a lie.
        if direction:
            record["dir"] = direction
        self.records.append(record)
        self._write(record)

    def close(self) -> Path | None:
        """Write the `.md` and close. Return the path written. If there is
        not a single sentence, leave nothing behind.

        **It is safe to call this twice.** After it is closed at the end of a
        meeting, `App.report` calls it again on exit. Without a flag, it
        would go and delete a file that is already deleted, or rewrite the
        `.md` it already wrote.
        """
        if self.closed:
            return self.md_path if self.records else None
        self.closed = True
        self.ended = time.time()
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

        if not self.records:
            # Delete only the file this run created. It holds nothing but the
            # single meta line.
            try:
                if self.path.exists():
                    self.path.unlink()
            except OSError:
                pass
            return None

        try:
            self.md_path.write_text(self.markdown(final=True), encoding="utf-8")
        except OSError as exc:
            self._failed(exc)
            return None
        return self.md_path

    # --- Readable form ------------------------------------------------------

    def markdown(self, final: bool = False) -> str:
        """`final=False` is for reading in the middle of a meeting. **It must
        not say the meeting has ended.**"""
        # After it is closed, write the time it was closed. With
        # `time.time()`, the end time would grow every time the record is
        # read again later.
        end = self.ended if self.ended is not None else time.time()
        return render(
            {"started": _clock(self.started), **self.meta},
            self.records,
            ended=_clock(end),
            final=final,
        )

    # --- Internal -----------------------------------------------------------

    def _write(self, obj: dict) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
            # So that everything up to this point survives a crash.
            self._fh.flush()
        except OSError as exc:
            self._failed(exc)

    def _failed(self, exc: Exception) -> None:
        """Report once, then stay quiet. Do not keep printing the same line
        during a meeting."""
        if not self.error:
            self.error = f"{type(exc).__name__}: {exc}"
            print(f"  [record] cannot write: {self.error}")
            print("  From now on the record is kept in memory only. Captions go on.")
        self._fh = None


def render(meta: dict, records: list[dict], ended: str = "", final: bool = True) -> str:
    """Build the readable form from the contents of the `.jsonl`.

    `scripts/transcript_to_md.py` calls this too. **Do not keep the format in
    two places.**
    """
    lines: list[str] = []
    started = str(meta.get("started", ""))
    lines.append(f"# Live captions {started[:16]}".rstrip())
    lines.append("")
    lines.append(f"- Started: {started}")
    if ended:
        lines.append(f"- Ended: {ended}" if final
                     else f"- Up to: {ended} (the meeting is still running)")
    lines.append(f"- Sentences: {len(records)}")
    if meta.get("asr"):
        lines.append(
            f"- Speech recognition: {meta['asr']}"
            f" (delay={meta.get('delay', '?')}, languages={meta.get('languages', '?')})"
        )
    if meta.get("translate"):
        lines.append(f"- Translation: {meta['translate']}")
    # The direction is recorded per sentence, because it can be switched in
    # the middle of a meeting.
    used = [d for d in dict.fromkeys(str(r.get("dir", "")) for r in records) if d]
    if not used and meta.get("direction"):
        used = [str(meta["direction"])]
    if used:
        labels = {"ja2en": "Japanese → English", "en2ja": "English → Japanese"}
        lines.append("- Caption direction: " + ", ".join(labels.get(d, d) for d in used))
        if len(used) > 1:
            lines.append("  **The direction was changed during the meeting.**")
    if meta.get("glossary") is not None:
        lines.append(f"- Glossary: {meta['glossary']} terms")
    if meta.get("glossary_sets"):
        lines.append(f"- Glossary tables used: {meta['glossary_sets']}")
    if meta.get("dry_run"):
        lines.append("- **--dry-run. Nothing was sent to Zoom.**")
    lines.append("")
    lines.append("Each pair is the transcript (above) and the caption that was shown (below).")
    lines.append("**Add errors in the upper line to the third column of "
                 "the tables in `etc/glossary/`.**")
    lines.append("")
    lines.append("---")
    lines.append("")

    for record in records:
        ja = str(record.get("ja", "")).strip()
        en = [str(x).strip() for x in record.get("en") or [] if str(x).strip()]
        # The two trailing spaces are a Markdown line break. They make the
        # pair read as one block.
        lines.append(f"**{record.get('at', '')}**　{ja}  ")
        lines.append("\n".join(en) if en else "*(translation failed)*")
        lines.append("")
    return "\n".join(lines)


def load(path: Path) -> tuple[dict, list[dict]]:
    """Read the `.jsonl` and return (meta, records). Broken lines are
    skipped."""
    meta: dict = {}
    records: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # When the power is cut, the last line is truncated. Throw away
            # only that line.
            continue
        if obj.get("type") == "meta":
            meta = obj
        elif obj.get("type") == "line":
            records.append(obj)
    return meta, records


# --- List the records in the directory ---------------------------------------
#
# **This is needed to download them from the control page** (2026-09-20). The
# caption PC runs all the time, and it is operated over the tailnet. This
# saves having to start a remote desktop session just to read a record.


def _count_lines(path: Path) -> int:
    """The number of finalized sentences. **Subtract the one meta line.**

    This reads the whole file, but the file is a few hundred KB, so reading
    it every time the list is shown does not matter. `load()` is not used
    because the JSON does not have to be parsed.
    """
    total = 0
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                total += chunk.count(b"\n")
    except OSError:
        return 0
    return max(total - 1, 0)


def scan(directory: Path) -> list[dict]:
    """Return the records in the directory, **newest first**.

    Take the date, the time and the meeting name out of a name such as
    `live-caption_2026-09-08_143012_定例会議.jsonl`. **Look only at the
    `.jsonl`.** The `.md` pairs with it on the same stem, so listing both
    would show the same meeting on two lines.

    The prefix filter is there because the directory can be changed to
    another folder. An unrelated `.jsonl` must not be picked up and shown as
    a record.
    """
    out: list[dict] = []
    try:
        found = sorted(Path(directory).glob(f"{config.TRANSCRIPT_PREFIX}*.jsonl"))
    except OSError:
        return out
    for path in found:
        try:
            stat = path.stat()
        except OSError:
            continue
        stem = path.stem
        rest = stem[len(config.TRANSCRIPT_PREFIX):]
        # Up to "2026-09-08_143012" is the date and time; anything after that
        # is the meeting name.
        when, _, label = rest.partition("_")
        clock, _, label2 = label.partition("_")
        if len(clock) == 6 and clock.isdigit():
            when = f"{when} {clock[:2]}:{clock[2:4]}:{clock[4:]}"
            label = label2
        out.append({
            "stem": stem,
            "label": label,
            "when": when,
            "lines": _count_lines(path),
            "bytes": stat.st_size,
            "updated": _clock(stat.st_mtime),
            # Whether the readable form already exists. Even without it, the
            # `.md` can be assembled and returned.
            "md": path.with_suffix(".md").exists(),
        })
    out.sort(key=lambda r: r["stem"], reverse=True)
    return out


def markdown_of(path: Path, final: bool = True) -> str:
    """Build the readable form from the `.jsonl`.

    **Build it from here even when the `.md` exists.** The `.md` is written
    only at the end, so for a meeting in progress, or for a record left after
    the power was cut, it is missing or out of date. Keep one place that
    builds it (the same `render()` that `scripts/transcript_to_md.py` uses).

    The file's modification time stands in for the end time. The `.jsonl` has
    no end line. That time is when the last sentence was written, so it is a
    few seconds off from the real end.
    """
    path = Path(path)
    meta, records = load(path)
    try:
        ended = _clock(path.stat().st_mtime)
    except OSError:
        ended = ""
    return render(meta, records, ended=ended, final=final)
