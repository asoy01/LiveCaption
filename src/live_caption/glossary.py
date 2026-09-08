"""用語対訳表の読み込み。

書式は `docs/glossary.tsv`:

    日本語(正しい表記) <TAB> English <TAB> よくある誤認識(カンマ区切り)

第3列が本体である。実際に出た誤認識を貯めると効く。英語の誤認識も入れる
（例:「people」は「p-pol」の誤認識）。

この表は2箇所で使う。

1. 音声認識の `keywords`（認識の段で音を拾わせる）
2. 翻訳のプロンプト（認識が漢字を外しても英語に復元する）

本命は2である。参考情報として並べるだけでは効かないので、置換規則として渡す。
根拠は local/HANDOFF.md の「用語対訳表を置換規則にした効果」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class Entry:
    ja: str
    en: str
    wrong: tuple[str, ...]


def load(path: Path | None = None) -> list[Entry]:
    entries: list[Entry] = []
    for line in (path or config.GLOSSARY_PATH).read_text(encoding="utf-8").splitlines():
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


def prompt_block(entries: list[Entry]) -> str:
    """翻訳のプロンプトに埋める2節を作る。"""
    terms = [f"- {e.ja} = {e.en}" for e in entries if e.en]
    rules = [f"- 「{w}」 → 「{e.ja}」 = {e.en}" for e in entries for w in e.wrong]

    return "\n".join([
        "## 用語対訳表（この英語を必ず使う）",
        "",
        *terms,
        "",
        "## 誤認識の置換規則（重要）",
        "",
        "下の「誤 → 正」は、実際に音声認識が出した誤りである。",
        "左の語が入力に現れたら、右の語の誤認識だと考えること。",
        "",
        "- **左の語がこの分野で意味を成さないなら、必ず右の語として訳すこと。**",
        "  例:「間食系」「感傷系」は日本語として意味を成さない。必ず「干渉計」= interferometer とする。",
        "  **「防振系」や「懸架系」と取り違えてはいけない。音が近いだけの別の語である。**",
        "- **英語の誤認識も含まれる。** 英語で話している部分にも同じ規則を適用すること。",
        "  例:「people」は「p-pol」（p偏光）の誤認識であることが多い。",
        "- 左の語が普通の語としても成立する場合（例:「変更」「反射」「people」「サークル」）は、文脈で判断する。",
        "  装置や測定の話をしている最中なら、右の語を優先する。",
        "  人や組織の話をしているなら、そのままの意味で訳す。",
        "",
        *rules,
    ])
