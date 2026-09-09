"""翻訳。日本語は英語に訳し、英語はそのまま出す。

用語対訳表と置換規則をプロンプトに埋める。認識が漢字を外しても、ここで復元する。
ただし**全部は救えない**。誤認識が「別の妥当な専門用語」に着地すると、
流暢な誤訳が出る。認識側の品質も重要である（local/HANDOFF.md 参照）。

1文だけ切り出して訳すと指示語が壊れるので、直前の文を参考として渡す。
訳すのは最後の1文だけ。
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request

from . import config, glossary

SYSTEM = """\
あなたは重力波望遠鏡 KAGRA の会議の同時通訳者である。音声認識の出力を、英語の字幕に変換する。

**この会議は日本語と英語が混ざる。** KAGRAの朝礼は前半が英語、後半が日本語である。

- 入力が**日本語**なら、英語に訳す。
- 入力が**既に英語**なら、訳さずにそのまま出す。認識の誤りと言い直しだけを整え、
  語彙や言い回しは変えない。話者の多くは英語を母語としないので、
  文法の細かな誤りは、意味が通るなら直さずに残す。

入力は音声認識の生の出力である。次の特徴がある。

- 漢字の変換を誤っていることが多い。音は合っているので、読みから正しい専門用語を推測すること。
- アルファベットの略語が小文字になっている。正しい大文字に直すこと。
- 句読点が無い、または誤っている。

この会議は重力波検出器 KAGRA の定例である。話題は干渉計の光学と制御、防振系（サスペンション）、
真空、低温など。専門用語はこの分野のものとして解釈すること。

{glossary}

出力の規則:

- 英語の字幕だけを出力する。説明や注釈は付けない。
- **1行は40〜{max_chars}文字にする。** これより長い場合だけ改行する。
  **極端に短い行を作らない。** 1〜3語だけの行は読みにくい。
  改行は、句読点や接続詞など、意味の切れ目で入れること。
- **多くても3行に収めること。** 字幕の表示領域は狭く、送りすぎると先頭が流れて消える。
  入力が長い場合は、細部を削ってでも短くする。
- 会議で口頭で話される英語にする。文語的な表現は使わない。
- 意味が取れない部分は、無理に訳さず、そのまま音写する。
"""

USER = """\
直前の文脈（訳さなくてよい。指示語を解釈するための参考）:
{context}

---

次の1文だけを英語の字幕にすること:
{target}
"""


def build_system(
    entries: list[glossary.Entry], max_chars: int = config.MAX_CAPTION_CHARS
) -> str:
    """システムプロンプトを組み立てる。

    **これがこのシステムの中核である。** 本体も `scripts/` の実験もここを使う。
    プロンプトを別々に持つと必ずずれるので、複製しないこと。
    """
    return SYSTEM.format(glossary=glossary.prompt_block(entries), max_chars=max_chars)


def chat(model: str, system: str, user: str, timeout: float = 180.0) -> tuple[str, float]:
    """1回だけ問い合わせる。(本文, 所要秒) を返す。同期。"""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        config.TRANSLATE_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.openai_key()}",
            "Content-Type": "application/json",
        },
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as res:
        data = json.loads(res.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip(), time.perf_counter() - t0


class Translator:
    def __init__(self, entries: list[glossary.Entry], model: str = config.TRANSLATE_MODEL) -> None:
        self.model = model
        self.system = build_system(entries)
        self.history: list[str] = []

    def _request(self, target: str) -> tuple[str, float]:
        context = "\n".join(self.history[-config.CONTEXT_SENTENCES:]) or "(なし)"
        return chat(
            self.model, self.system, USER.format(context=context, target=target), timeout=60.0
        )

    async def translate(self, target: str) -> tuple[list[str], float]:
        """1文を訳して、(字幕の行, 所要秒) を返す。失敗したら空リスト。

        **所要秒は戻り値で返す。属性に置いてはいけない。** 翻訳は最大4本が
        同時に走るので、共有の属性に書くと、読むときには別の文の値になっている。
        """
        try:
            text, took = await asyncio.to_thread(self._request, target)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            print(f"  [翻訳の失敗] HTTP {e.code} {detail}")
            return [], 0.0
        except Exception as e:  # noqa: BLE001 - 会議中に落とさない
            print(f"  [翻訳の失敗] {type(e).__name__}: {e}")
            return [], 0.0

        # 訳した文だけを文脈に足す。失敗した文は足さない。
        self.history.append(target)
        del self.history[:-config.CONTEXT_SENTENCES]

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return [ln for line in lines for ln in _wrap(line, config.MAX_CAPTION_CHARS)], took


def _wrap(line: str, limit: int) -> list[str]:
    """モデルが長い行を返したときの保険。単語の切れ目で折る。"""
    if len(line) <= limit:
        return [line]
    out, current = [], ""
    for word in line.split(" "):
        if current and len(current) + 1 + len(word) > limit:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return out
