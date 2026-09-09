"""会議の記録を残す。

**日本語の認識文と、それを訳した英語の字幕を対にして書く。** 英語だけでは、
後から用語の誤りを追えない。誤認識は日本語の側にしか現れないためである
（`docs/test-procedure.md` の段階2で採取しているもの）。

## ファイルは2つできる

**置き場は既定でユーザーのダウンロードフォルダ**（`config.downloads_dir()`）。
会議のあとすぐ開けるところに出す。変えるなら `--save-dir`。

    ダウンロード/live-caption_2026-09-08_143012.jsonl   1行 = 確定した1文。逐次追記する
    ダウンロード/live-caption_2026-09-08_143012.md      読める形。終了時に書く

**名前に `live-caption_` を付ける。** ダウンロードフォルダは他のファイルと混ざる。
日付だけの名前では、何のファイルか分からない。

**`.jsonl` が原本である。1文が確定するたびに書いて流す。** 最後にまとめて書くと、
字幕アプリが落ちたときに会議1本ぶんが消える。1時間の会議でそこを賭けにしない。

`.md` は終了時に組み立てる。電源ごと落ちたときは残らないので、そのときは
`scripts/transcript_to_md.py` で `.jsonl` から作り直す。

## 会議中に落とさない

書き込みが失敗しても字幕は止めない。字幕を出すことが本来の仕事で、記録はその
副産物である。失敗したら画面に1度だけ出して、以後はメモリにだけ溜める。

## 1文も出なかったときはファイルを残さない

配線の確認などで短く起動して止めることがある。空の記録が溜まると、本物の会議の
記録が探しにくくなる。中身が無ければ、この実行で作ったファイルを消す。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import config


def _clock(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


class Transcript:
    """1回の起動ぶんの記録。

    `add()` を呼ぶのは本体のイベントループだけである（`app.App._post`）。
    書くのは数百バイトなので、そのまま同期で書いて流す。
    """

    def __init__(self, directory: Path, meta: dict | None = None) -> None:
        self.directory = Path(directory)
        self.meta = dict(meta or {})
        self.started = time.time()
        stem = config.TRANSCRIPT_PREFIX + time.strftime(
            "%Y-%m-%d_%H%M%S", time.localtime(self.started))
        self.path = self.directory / f"{stem}.jsonl"
        self.md_path = self.directory / f"{stem}.md"
        self.records: list[dict] = []
        self.error = ""
        self._fh = None

    # --- 本体から呼ぶ -------------------------------------------------------

    def open(self) -> None:
        """ファイルを作る。失敗しても例外は出さない（記録のために会議を止めない）。"""
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
    ) -> None:
        """確定した1文を記録する。

        `ja` は認識の出力そのもの。**日本語とは限らない。** 会議の前半は英語なので、
        そのときは英語がそのまま入る。`en` は字幕として出した行で、翻訳に失敗した
        ときは空になる。**空でも捨てない。** 後から誤認識を拾うのに要る。

        `when` は**認識が確定した時刻**である。呼ばれるのは翻訳が終わった後なので、
        ここで時計を読むと2〜3秒ずれる。記録に要るのは、訳せた時刻ではなく
        話された時刻なので、呼ぶ側から渡す。

        後ろの4つは遅延の調整用である。`cut` は確定の理由（punct / force / idle /
        flush）、`waited` は無音で確定したときに実際に待った秒数、`took` は翻訳、
        `total` は**確定から最初の字幕までの実測**。
        **1文ごとの実測が無いと、どこを削ればよいか決められない。**
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
        # 無音待ち以外は待っていないので、書いても意味が無い。
        if waited:
            record["waited"] = round(waited, 2)
        if took:
            record["took"] = round(took, 2)
        if total:
            record["total"] = round(total, 2)
        # 先回りの翻訳が当たった文。当たると total が took より小さくなる。
        if spec:
            record["spec"] = True
        self.records.append(record)
        self._write(record)

    def close(self) -> Path | None:
        """`.md` を書いて閉じる。書いたパスを返す。1文も無ければ何も残さない。"""
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

        if not self.records:
            # この実行で作ったファイルだけを消す。中身はメタ1行しかない。
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

    # --- 読める形 -----------------------------------------------------------

    def markdown(self, final: bool = False) -> str:
        """`final=False` は会議の途中で読むとき。**「終了」と書いてはいけない。**"""
        return render(
            {"started": _clock(self.started), **self.meta},
            self.records,
            ended=_clock(time.time()),
            final=final,
        )

    # --- 内部 ---------------------------------------------------------------

    def _write(self, obj: dict) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
            # 落ちたときに、そこまでが残るようにする。
            self._fh.flush()
        except OSError as exc:
            self._failed(exc)

    def _failed(self, exc: Exception) -> None:
        """1度だけ知らせて、以後は黙る。会議中に同じ行を出し続けない。"""
        if not self.error:
            self.error = f"{type(exc).__name__}: {exc}"
            print(f"  [記録の失敗] {self.error}")
            print("  以後はメモリにだけ溜める。字幕は続く。")
        self._fh = None


def render(meta: dict, records: list[dict], ended: str = "", final: bool = True) -> str:
    """`.jsonl` の中身から、読める形を組み立てる。

    `scripts/transcript_to_md.py` もこれを呼ぶ。**書式を2か所に持たない。**
    """
    lines: list[str] = []
    started = str(meta.get("started", ""))
    lines.append(f"# Live captions {started[:16]}".rstrip())
    lines.append("")
    lines.append(f"- 開始: {started}")
    if ended:
        lines.append(f"- 終了: {ended}" if final else f"- ここまで: {ended}（会議はまだ続いている）")
    lines.append(f"- 確定した文: {len(records)}")
    if meta.get("asr"):
        lines.append(
            f"- 音声認識: {meta['asr']}"
            f"（delay={meta.get('delay', '?')}、languages={meta.get('languages', '?')}）"
        )
    if meta.get("translate"):
        lines.append(f"- 翻訳: {meta['translate']}")
    if meta.get("glossary") is not None:
        lines.append(f"- 用語対訳表: {meta['glossary']} 語")
    if meta.get("glossary_sets"):
        lines.append(f"- 使った用語集: {meta['glossary_sets']}")
    if meta.get("dry_run"):
        lines.append("- **--dry-run。Zoomへは送っていない。**")
    lines.append("")
    lines.append("認識の出力（上）と、字幕として出した英語（下）を並べてある。")
    lines.append("**上の行の誤りは `etc/glossary/` の表の第3列に足すこと。**")
    lines.append("")
    lines.append("---")
    lines.append("")

    for record in records:
        ja = str(record.get("ja", "")).strip()
        en = [str(x).strip() for x in record.get("en") or [] if str(x).strip()]
        # 行末の空白2つは Markdown の改行。1つの塊として読ませる。
        lines.append(f"**{record.get('at', '')}**　{ja}  ")
        lines.append("\n".join(en) if en else "*（翻訳に失敗した）*")
        lines.append("")
    return "\n".join(lines)


def load(path: Path) -> tuple[dict, list[dict]]:
    """`.jsonl` を読んで (メタ, 記録) を返す。壊れた行は飛ばす。"""
    meta: dict = {}
    records: list[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # 電源が落ちると最後の1行が途中で切れる。そこだけ捨てる。
            continue
        if obj.get("type") == "meta":
            meta = obj
        elif obj.get("type") == "line":
            records.append(obj)
    return meta, records
