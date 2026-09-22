"""翻訳。**向きは会議ごとに選ぶ。**

- `ja2en`: 日本語は英語に訳し、英語はそのまま出す
- `en2ja`: 英語は日本語に訳し、日本語はそのまま出す

どちらの向きでも、逆の言語が混ざったときはそのまま出す。会議の途中で主に話される
言語が変わることがあるので、片方の言語だけを前提にできない。

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

SYSTEM_JA2EN = """\
あなたは技術的な会議の同時通訳者である。音声認識の出力を、英語の字幕に変換する。

**この会議は日本語と英語が混ざる。** 前半と後半で主に話される言語が変わることもある。

- 入力が**日本語**なら、英語に訳す。
- 入力が**既に英語**なら、訳さずにそのまま出す。認識の誤りと言い直しだけを整え、
  語彙や言い回しは変えない。話者の多くは英語を母語としないので、
  文法の細かな誤りは、意味が通るなら直さずに残す。

入力は音声認識の生の出力である。次の特徴がある。

- 漢字の変換を誤っていることが多い。音は合っているので、読みから正しい専門用語を推測すること。
- アルファベットの略語が小文字になっている。正しい大文字に直すこと。
- 句読点が無い、または誤っている。

{meeting}
専門用語はこの分野のものとして解釈すること。

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

SYSTEM_EN2JA = """\
あなたは技術的な会議の同時通訳者である。音声認識の出力を、日本語の字幕に変換する。

**この会議は日本語と英語が混ざる。** 前半と後半で主に話される言語が変わることもある。

- 入力が**英語**なら、日本語に訳す。
- 入力が**既に日本語**なら、そのまま日本語で出す。**英語に訳してはいけない。**
  認識の誤りと言い直しだけを整え、語彙や言い回しは変えない。
  **入力が日本語のときも、下の置換規則は適用する。** 誤認識はそこで直す。

入力は音声認識の生の出力である。次の特徴がある。

- 専門用語を、音の近い普通の語に取り違えていることが多い。下の置換規則で直すこと。
- アルファベットの略語が小文字になっている。正しい大文字に直すこと。
- 句読点が無い、または誤っている。
- 話者の多くは英語を母語としない。文法が崩れていても、意味を取って訳すこと。

{meeting}
専門用語はこの分野のものとして解釈すること。

{glossary}

出力の規則:

- 日本語の字幕だけを出力する。説明や注釈は付けない。
- **出力は必ず日本語である。英語の文を出力してはいけない。**
  入力が日本語だったときは、その日本語を出す。訳し返さない。
- **1行は20〜{max_chars}文字にする。** これより長い場合だけ改行する。
  **極端に短い行を作らない。** 改行は、読点や助詞の切れ目など、意味の切れ目で入れること。
- **多くても3行に収めること。** 字幕の表示領域は狭く、送りすぎると先頭が流れて消える。
  入力が長い場合は、細部を削ってでも短くする。
- 会議で口頭で話される日本語にする。文語的な表現は使わない。「です・ます」で揃える。
- **専門用語を日本語に訳しすぎない。** 上の表にある語は表の日本語を使う。
  表に無い略語や装置名は、英字のまま残すこと。
  現場で英語のまま呼んでいる語（ロック、アライメントなど）も、そのまま使う。
- 意味が取れない部分は、無理に訳さず、そのまま音写する。
"""

# 向きから、使うシステムプロンプトを引く。
SYSTEMS = {"ja2en": SYSTEM_JA2EN, "en2ja": SYSTEM_EN2JA}

USER = """\
直前の文脈（訳さなくてよい。指示語を解釈するための参考）:
{context}

---

次の1文だけを英語の字幕にすること:
{target}
"""


def build_system(
    entries: list[glossary.Entry],
    max_chars: int | None = None,
    direction: str | None = None,
) -> str:
    """システムプロンプトを組み立てる。

    **これがこのシステムの中核である。** 本体も `scripts/` の実験もここを使う。
    プロンプトを別々に持つと必ずずれるので、複製しないこと。

    `direction` が None なら、いま選ばれている向き（`config.DIRECTION`）。
    `max_chars` が None なら、その向きの1行の文字数（英語80 / 日本語40）。

    **既定値引数で `config` の値を捕まえてはいけない。** 既定値引数は import の
    ときに1回だけ評価されるので、向きの切り替えも `.env` の差し替えも効かなくなる。
    ここで、呼ばれるたびに読む。
    """
    name = config.DIRECTION if direction is None else direction
    if max_chars is None:
        max_chars = config.DIRECTIONS[name].max_caption_chars
    template = SYSTEMS.get(name, SYSTEM_JA2EN)
    return template.format(
        meeting=config.meeting_context(),
        glossary=glossary.prompt_block(entries, direction=name),
        max_chars=max_chars,
    )


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

    def remember(self, target: str) -> None:
        """訳せた文を文脈に足す。先回りの翻訳が採用されたときに、呼ぶ側から呼ぶ。"""
        self.history.append(target)
        del self.history[:-config.CONTEXT_SENTENCES]

    async def translate(self, target: str, *, remember: bool = True) -> tuple[list[str], float]:
        """1文を訳して、(字幕の行, 所要秒) を返す。失敗したら空リスト。

        **所要秒は戻り値で返す。属性に置いてはいけない。** 翻訳は最大4本が
        同時に走るので、共有の属性に書くと、読むときには別の文の値になっている。

        `remember=False` は先回りの翻訳のためにある。**投げ捨てる可能性のある
        文を文脈に混ぜてはいけない。** 先回りは1分に数回走って大半が捨てられるので、
        混ぜると、直前3文の枠が言いかけの断片で埋まる。採用が決まってから
        `remember()` を呼ぶこと。
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
        if remember:
            self.remember(target)

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return [ln for line in lines for ln in _wrap(line, config.MAX_CAPTION_CHARS)], took


def _wrap(line: str, limit: int) -> list[str]:
    """モデルが長い行を返したときの保険。単語の切れ目で折る。

    **日本語には空白が無い。** 空白で折るだけだと、長い日本語の行がそのまま
    通ってしまい、保険にならない。空白が無い行は句読点で折り、それも無ければ
    文字数で切る。
    """
    if len(line) <= limit:
        return [line]
    if " " not in line:
        return _wrap_ja(line, limit)
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


def _wrap_ja(line: str, limit: int) -> list[str]:
    """空白の無い行を折る。読点・句点を優先し、無ければ文字数で切る。

    **行数を先に決めて、幅を均す。** 端から `limit` で切ると、42文字の行が
    「40文字」と「す。」に割れる。1〜2文字の行は字幕として読めない。
    """
    out = []
    while len(line) > limit:
        # **毎回、残りの長さから割り直す。** 最初に決めた幅のまま切り進めると、
        # 区切りの位置で余りがずれて、最後に「す。」だけの行が残る。
        pieces = -(-len(line) // limit)      # 何行に割るか（切り上げ）
        width = -(-len(line) // pieces)      # 均した幅
        window = line[:width]
        cut = max(window.rfind(mark) + len(mark) for mark in ("、", "。", "，", "・"))
        # あまり手前で切ると細切れになる。後半に区切りが無ければ文字数で切る。
        if cut <= width // 2:
            cut = width
            # **行の頭に句読点や閉じ括弧を置かない。** 1文字ぶん前の行へ送る。
            while line[cut: cut + 1] in ("、", "。", "，", "．", "・", "」", "）", "』"):
                cut += 1
        out.append(line[:cut].strip())
        line = line[cut:].strip()
    if line:
        out.append(line)
    return out
