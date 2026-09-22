"""用語対訳表の読み込み。

**表は複数ある。** `etc/glossary/` に置いた `.tsv` を、会議に合わせて選んで重ねる。
例: `Optics` + `Control`。選択は操作画面から変えられる。

分けるのは、サブシステムによって語彙が違うためである。1つの大きな表を
全部の会議で使うと、関係の無い語が認識の `keywords` を食い、上限
（`config.ASR_KEYWORD_LIMIT`）で本当に要る語が落ちる。

書式は `etc/glossary/*.tsv`:

    日本語(正しい表記) <TAB> English <TAB> よくある誤認識(カンマ区切り)

第3列が本体である。実際に出た誤認識を貯めると効く。英語の誤認識も入れる
（例:「people」は「p-pol」の誤認識）。

この表は2箇所で使う。

1. 音声認識の `keywords`（認識の段で音を拾わせる）
2. 翻訳のプロンプト（認識が漢字を外しても英語に復元する）

本命は2である。参考情報として並べるだけでは効かないので、置換規則として渡す。
根拠は local/HANDOFF.md の「用語対訳表を置換規則にした効果」。

**字幕の向きを変えても、表は作り直さない。** `日本語 / English` の対なので、
`prompt_block()` が左右を入れ替えるだけで `en2ja` に使える。認識に渡す
`keywords()` は、そもそも両方の言語を常に渡しているので向きに依存しない。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Entry:
    ja: str
    en: str
    wrong: tuple[str, ...]


#: 表の名前に使ってよい文字。**操作画面には認証が無い。**
#: 名前はそのままファイル名になるので、ここを緩めるとディレクトリを遡られる。
_NAME_OK = re.compile(r"\A[A-Za-z0-9_\-. ぁ-んァ-ヶ一-龠々ー]{1,64}\Z")

#: アップロードを受ける上限。用語表は数百行のテキストなので、1 MB あれば余る。
MAX_UPLOAD_BYTES = 1 << 20


def check_name(name: str) -> str:
    """表の名前として使える形に直す。使えなければ `ValueError`。

    **`.tsv` を外した「名前」だけを扱う。** パスの区切りも `..` も通さない。
    """
    stem = name[:-4] if name.lower().endswith(".tsv") else name
    stem = stem.strip()
    if not stem or not _NAME_OK.match(stem) or stem in {".", ".."}:
        raise ValueError(
            f"用語集の名前として使えない: 「{name}」\n"
            "  英数字・かな・漢字・` _ - . `だけ、64文字までにすること。"
        )
    return stem


def path_of(name: str) -> Path:
    """名前から表のファイルの場所を作る。拡張子は付けても付けなくてもよい。"""
    return config.glossary_dir() / f"{check_name(name)}.tsv"


def available() -> list[dict]:
    """`etc/glossary/` にある表の一覧。名前順。

    語数まで返す。**どれを選ぶと何語になるかが見えないと、選べない。**
    """
    out = []
    root = config.glossary_dir()
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.tsv"), key=lambda q: q.name.lower()):
        out.append({"name": p.stem, "terms": len(load_file(p))})
    return out


def read_text(name: str) -> str:
    """表の中身をそのまま返す。ダウンロードに使う。"""
    path = path_of(name)
    if not path.is_file():
        raise ValueError(f"そういう名前の用語集は無い: 「{name}」")
    return path.read_text(encoding="utf-8")


def save_text(name: str, text: str) -> str:
    """表を書き込む。同じ名前があれば置き換える。保存した名前を返す。

    **中身を確かめてから書く。** 操作画面から入る唯一の書き込み口なので、
    壊れた表を置いて会議の当日に気づく、ということが無いようにする。
    """
    stem = check_name(name)
    raw = text.encode("utf-8")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"用語集が大きすぎる（{len(raw)} バイト）。"
            f"{MAX_UPLOAD_BYTES} バイトまでにすること。"
        )
    if "\x00" in text:
        raise ValueError("テキストではない。.tsv を渡すこと。")

    # **書式を間違えた表を受け取らない。** カンマ区切りで出した `.csv` は、
    # 1行まるごとが日本語の語1つとして読めてしまう。黙って壊れた表になり、
    # 会議の当日に「用語が効かない」という形で気づくことになる。
    lines = [ln for ln in text.splitlines()
             if ln.strip() and not ln.startswith("#")]
    entries = parse_text(text)
    if not entries or not any("\t" in ln for ln in lines):
        raise ValueError(
            "用語集として読めない。書式を確かめること:\n"
            "  日本語(正しい表記) <TAB> English <TAB> よくある誤認識(カンマ区切り)\n"
            "  **区切りはタブである。カンマではない。**\n"
            "  Excel から出すときは「テキスト (タブ区切り)」を選ぶこと。"
        )

    root = config.glossary_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{stem}.tsv"
    # 改行は LF に揃える。Windows で編集した表がそのまま来る。
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if not body.endswith("\n"):
        body += "\n"
    # **書きかけを残さない。** 会議中に置き換えることがある。
    tmp = path.with_suffix(".tsv.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    return stem


def delete_file(name: str) -> str:
    """表を消す。消した名前を返す。"""
    path = path_of(name)
    if not path.is_file():
        raise ValueError(f"そういう名前の用語集は無い: 「{name}」")
    path.unlink()
    return path.stem


def parse_text(text: str) -> list[Entry]:
    """表の中身を解く。**受け取る前の検査にも使うので、ファイルとは別にしてある。**"""
    entries: list[Entry] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        ja = cols[0].strip()
        en = cols[1].strip() if len(cols) > 1 else ""
        wrong = tuple(
            w.strip() for w in (cols[2] if len(cols) > 2 else "").split(",") if w.strip()
        )
        if ja:
            entries.append(Entry(ja, en, wrong))
    return entries


def load_file(path: Path) -> list[Entry]:
    """1つの表を読む。"""
    return parse_text(path.read_text(encoding="utf-8"))


def load(names: list[str] | tuple[str, ...] | None = None) -> list[Entry]:
    """選んだ表を重ねて読む。`names` が None なら既定の選択を使う。

    **同じ日本語が複数の表に出たら、1つにまとめる。** 英語は最初に出たものを採り、
    誤認識の列は全部の表から集める。誤認識は多いほうがよいので捨てない。

    英語が食い違ったら画面に出す。**黙って片方を捨てると、会議中に
    「表に書いたはずの英語が出ない」と悩むことになる。**
    """
    if names is None:
        names = selection()

    merged: dict[str, Entry] = {}
    for name in names:
        try:
            path = path_of(name)
        except ValueError:
            # **読み込みで落とさない。** 覚えていた選択が壊れていても、
            # 会議は始められないといけない。
            print(f"  [用語集] 「{name}」は名前として使えない。飛ばす")
            continue
        if not path.exists():
            print(f"  [用語集] {path.name} が無い。飛ばす")
            continue
        for e in load_file(path):
            old = merged.get(e.ja)
            if old is None:
                merged[e.ja] = e
                continue
            if old.en and e.en and old.en != e.en:
                print(f"  [用語集] 「{e.ja}」の英語が食い違う: "
                      f"{old.en!r} を使い、{e.en!r}（{name}）は使わない")
            wrong = tuple(dict.fromkeys(old.wrong + e.wrong))
            merged[e.ja] = Entry(old.ja, old.en or e.en, wrong)
    return list(merged.values())


def selection() -> tuple[str, ...]:
    """いま選ばれている表の名前。前回の選択を覚えている。

    **覚えるのは、会議ごとに選び直す手間を無くすためである。** 同じ種類の
    会議が続くことが多い。起動時の画面と操作画面の両方に出るので、
    前回のままなことに気づけないという心配はない。
    """
    try:
        saved = json.loads(config.GLOSSARY_STATE_PATH.read_text(encoding="utf-8"))
        names = tuple(str(n) for n in saved.get("names", []))
    except (OSError, ValueError, AttributeError):
        names = ()
    # 消えた表を覚えたままにしない。壊れた名前も落とす。
    names = tuple(n for n in names if _exists(n))
    if names:
        return names
    return tuple(n for n in config.GLOSSARY_DEFAULT if _exists(n))


def _exists(name: str) -> bool:
    try:
        return path_of(name).exists()
    except ValueError:
        return False


def remember(names: list[str] | tuple[str, ...]) -> None:
    """次の起動のために選択を覚える。書けなくても落とさない。"""
    try:
        config.GLOSSARY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.GLOSSARY_STATE_PATH.write_text(
            json.dumps({"names": list(names)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"  [用語集] 選択を覚えられない: {exc}")


def keywords(
    entries: list[Entry], limit: int | None = config.ASR_KEYWORD_LIMIT
) -> list[str]:
    """音声認識に渡す語。日本語の正しい表記と英語の両方を渡す。

    `limit=None` なら切り捨てない。上限で何語が落ちるかを数えるときに使う。
    """
    out: list[str] = []
    for e in entries:
        out.append(e.ja)
        if e.en and e.en != e.ja:
            out.append(e.en)
    return out if limit is None else out[:limit]


def prompt_block(entries: list[Entry], direction: str | None = None) -> str:
    """翻訳のプロンプトに埋める2節を作る。

    **向きで左右を入れ替える。** 表は `日本語 / English` の対なので、どちらを
    字幕の言語にするかで、対訳表の並びと、置換規則の行き先が変わる。表そのものは
    作り直さない。`en2ja` でも同じ `.tsv` をそのまま使う。

    `direction` が None なら、いま選ばれている向き（`config.DIRECTION`）。
    """
    name = config.DIRECTION if direction is None else direction

    if name == "en2ja":
        # 出すのは日本語。英語 = 日本語 の順に並べ、置換規則も日本語に着地させる。
        header = "## 用語対訳表（この日本語を必ず使う）"
        terms = [f"- {e.en} = {e.ja}" for e in entries if e.en]
        rules = [f"- 「{w}」 → 「{e.en}」 = {e.ja}"
                 for e in entries if e.en for w in e.wrong]
        examples = [
            "- **左の語がこの分野で意味を成さないなら、必ず右の語として訳すこと。**",
            "  例:「people」は「p-pol」（p偏光）の誤認識であることが多い。",
            "- **日本語の誤認識も含まれる。** 日本語で話している部分にも同じ規則を適用すること。",
            "  例:「間食系」「感傷系」は日本語として意味を成さない。必ず「干渉計」の話である。",
            "  **「防振系」や「懸架系」と取り違えてはいけない。音が近いだけの別の語である。**",
        ]
    else:
        header = "## 用語対訳表（この英語を必ず使う）"
        terms = [f"- {e.ja} = {e.en}" for e in entries if e.en]
        rules = [f"- 「{w}」 → 「{e.ja}」 = {e.en}" for e in entries for w in e.wrong]
        examples = [
            "- **左の語がこの分野で意味を成さないなら、必ず右の語として訳すこと。**",
            "  例:「間食系」「感傷系」は日本語として意味を成さない。必ず「干渉計」= interferometer とする。",
            "  **「防振系」や「懸架系」と取り違えてはいけない。音が近いだけの別の語である。**",
            "- **英語の誤認識も含まれる。** 英語で話している部分にも同じ規則を適用すること。",
            "  例:「people」は「p-pol」（p偏光）の誤認識であることが多い。",
        ]

    return "\n".join([
        header,
        "",
        *terms,
        "",
        "## 誤認識の置換規則（重要）",
        "",
        "下の「誤 → 正」は、実際に音声認識が出した誤りである。",
        "左の語が入力に現れたら、右の語の誤認識だと考えること。",
        "",
        *examples,
        "- 左の語が普通の語としても成立する場合（例:「変更」「反射」「people」「サークル」）は、文脈で判断する。",
        "  装置や測定の話をしている最中なら、右の語を優先する。",
        "  人や組織の話をしているなら、そのままの意味で訳す。",
        "",
        *rules,
    ])
