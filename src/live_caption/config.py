"""設定。実測で決まった値は既定値として埋め込んである。

根拠は local/HANDOFF.md の「実測結果」にある。値を変えるときは、そこも読むこと。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# --- 音声 -------------------------------------------------------------------
# gpt-live-transcribe は 24 kHz・16 bit・モノラルの PCM を要求する。
ASR_RATE = 24_000
# 送る単位。100 ms が推奨。短くすると通信の回数が増え、長くすると遅延が増える。
CHUNK_MS = 100
# 取り込みは装置の実レートで行い、こちらで 24 kHz に落とす。
# VB-CABLE は Windows のサウンド設定で 48 kHz にしてある（2:1 で間引ける）。
CAPTURE_RATE = 48_000

# --- 音声認識 ---------------------------------------------------------------
ASR_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
ASR_MODEL = "gpt-live-transcribe"
# minimal と high はどちらも用語を外した。low が最良（HANDOFF 参照）。
ASR_DELAY = "low"
# KAGRAの朝礼は前半が英語、後半が日本語。言語を固定してはいけない。
ASR_LANGUAGES = ("ja", "en")
ASR_PROMPT = (
    "重力波望遠鏡 KAGRA の定例会議。干渉計の光学と制御、防振系、真空、低温の話題。"
    "日本語と英語が混ざる。"
)
# keywords に渡せる語数の上限。
#
# **用語表の全語が入る値にしておくこと。** `keywords()` は日本語と英語の両方を
# 渡すので、候補の数は用語表の語数の約2倍になる。100 にしていたとき、表の末尾
# 27語（PRCL・SRCL・ITM・s偏光など）が認識に届いていなかった（2026-09-07）。
# 翻訳の段には全語が入るので復元は効くが、認識の段では効かない。
#
# OpenAI の keywords に明示された上限は見当たらない。語数を増やしたときの
# 効きは未検証である（local/HANDOFF.md「次にやること」）。
# 切り捨てが起きたときは起動時の画面に警告が出る（app.py）。
ASR_KEYWORD_LIMIT = 200

# --- 翻訳 -------------------------------------------------------------------
# nano は速度が変わらず品質だけ落ちた。推論モデルは遅すぎて論外（HANDOFF 参照）。
TRANSLATE_MODEL = "gpt-4.1-mini"
TRANSLATE_URL = "https://api.openai.com/v1/chat/completions"
# 1文だけ切り出して訳すと指示語が壊れる。直前の文を参考として渡す。
CONTEXT_SENTENCES = 3

# --- 字幕の切り出しと送信 ---------------------------------------------------
# Zoomの字幕オーバーレイは既定フォントで1行およそ80文字。
# **これは英語を出すときの値である。** 日本語は全角で幅が倍あるので、
# 向きを `en2ja` にすると 40 に差し替わる（下の「字幕の向き」）。
MAX_CAPTION_CHARS = 80
# 句点が来ないまま伸び続けたときに、強制的に切る長さ。
#
# **短くすること。** 話者は「、」で繋いで長く話す。120文字にしていたら、
# 1文が字幕4〜5行になり、送った瞬間に先頭が窓から押し出された。
# 70文字なら、おおむね2行に収まる。
#
# **これは日本語を聞くときの値である。** 英語は同じ内容を話すのに字数が倍要るので、
# 向きを `en2ja` にすると 140 に差し替わる。ただし `.env` や操作画面で明示した値は
# 残る（下の「字幕の向き」と `apply_direction()`）。
FORCE_CUT_CHARS = 70
# 発話が途切れてから、句点が無くても確定させるまでの秒数。
IDLE_FLUSH_SEC = 2.5
# 上のタイマーが切れたかどうかを見にいく間隔。
#
# **細かくすること。** 0.5秒にしていたら、満了の検出が0.5秒刻みになり、
# 平均0.25秒・最悪0.5秒がそのまま字幕の遅れになっていた。0.1秒ごとに起きる
# コルーチンの費用は無視できる。
IDLE_POLL_SEC = 0.1
# 無音が続いたときに、確定を待たずに翻訳を先に投げるまでの秒数。0 で止める。
#
# **無音待ちを速くする手は、これしか残っていない。** `IDLE_FLUSH_SEC` は
# 下げられない（delta 間隔の p99 が 2.57秒 で、閾値 2.5秒 より上だった）。
# そこで、待っている 2.5秒 の間に訳しておき、確定した時点で出す。
# **切る危険は増えない。安全余裕はそのままである。**
#
# 値は「翻訳が確定に間に合う、いちばん遅い時刻」にする。早く投げるほど、
# 話が再開したときの投げ捨てが増えるだけで、得るものは無い。
#   IDLE_FLUSH_SEC 2.5 − 翻訳の中央値 0.9 − 余裕 0.2 = 1.4
SPECULATE_AFTER_SEC = 1.4
# 同じ翻訳から複数行が出たときに、1行ずつ空ける間隔。
# まとめて送ると読み手が追えない。窓は最小4行しかない。
LINE_INTERVAL_SEC = 0.6
# 会議開始時に流す捨て字幕。最初の数個は受信側に届かない（HANDOFF 参照）。
# **向きが `en2ja` のときは日本語の3行に差し替わる。**
WARMUP_CAPTIONS = (
    "Live captions are starting.",
    "Please widen the caption area to see more lines.",
    "---",
)

# --- Zoom字幕API ------------------------------------------------------------
# **これは英語を出すときの値である。** 向きが `en2ja` なら `ja-JP` に差し替わる。
CAPTION_LANG = "en-US"
CAPTION_TIMEOUT = 10.0
# seq はミーティングのセッション全体で単調増加していないといけない。
# 巻き戻すと Zoom はエラーを返さずに黙って捨てる。会議IDごとに保存する。
SEQ_STATE_PATH = PROJECT_ROOT / "local" / "seq_state.json"
# 状態を失ったときの保険。飛ばして送るのは可（実測済み）。
# 送信レートの実測は 3.5 回/秒なので、係数はそれより大きく取る。
SEQ_TIME_SCALE = 10
SEQ_EPOCH = 1_767_225_600  # 2026-01-01 UTC。32bit に収めるための基準。

# --- 字幕の向き -------------------------------------------------------------
# **会議ごとに1つ選ぶ。** 日本語の会議には英語字幕、英語の会議には日本語字幕。
# 逆の言語が混ざったときは訳さずにそのまま出す。プロンプトが両方を扱う。
#
# 向きが決めるのは、翻訳のプロンプトと、下の4つだけである。
# **認識には手を触れない。** `keywords` は用語表の日本語と英語の両方を常に渡すので
# （`glossary.keywords()`）、向きを変えても同じ語が同じ並びで行く。
# だから切り替えに WebSocket の張り直しが要らない。用語集の選び直しとはここが違う。


@dataclass(frozen=True)
class Direction:
    name: str
    label: str                # 操作画面に出す名前
    caption_lang: str         # Zoom字幕APIの lang
    max_caption_chars: int    # 1行の文字数
    force_cut_chars: int      # 文末記号が来ないまま伸びたときに切る長さ
    warmup: tuple[str, ...]   # 流し始めの捨て字幕


DIRECTIONS: dict[str, Direction] = {
    "ja2en": Direction(
        name="ja2en",
        label="日本語 → 英語",
        caption_lang=CAPTION_LANG,
        max_caption_chars=MAX_CAPTION_CHARS,
        force_cut_chars=FORCE_CUT_CHARS,
        warmup=WARMUP_CAPTIONS,
    ),
    "en2ja": Direction(
        name="en2ja",
        label="英語 → 日本語",
        caption_lang="ja-JP",
        # 日本語は全角で幅が倍あるので、同じ窓に入る文字数は半分になる。
        max_caption_chars=40,
        # 英語は同じ内容を話すのに字数が倍要る。70 のままだと文が細切れになる。
        force_cut_chars=140,
        warmup=(
            "字幕を開始します。",
            "字幕の表示領域を広げてください。",
            "---",
        ),
    ),
}
DIRECTION_DEFAULT = "ja2en"
# いま選ばれている向き。`apply_direction()` が書き換える。
DIRECTION = DIRECTION_DEFAULT
# 前回の選択。操作画面で選び直すたびに書く。次の起動もこれで始まる。
DIRECTION_STATE_PATH = PROJECT_ROOT / "local" / "direction_state.json"

# --- ブラウザ字幕 -----------------------------------------------------------
# Zoom字幕APIはホスト権限（トークンのコピー）が要る。自分がホストでない会議では
# 使えないので、ブラウザに出す道も用意してある（web.py）。見せ方は2つある。
# 画面共有するか、参加者に閲覧URLを配るか。
#
# **画面を2つに分けてある。ポートも分けてある。**
#   閲覧（WEB_PORT）   字幕を見るだけ。ここだけが外に出る
#   操作（CONTROL_PORT）トークン・送信の開始停止・終了・トンネル・記録。127.0.0.1 のみ
#
# パスで分けないのは、トンネルもリバースプロキシもオリジンごと通すためである。
# 経路の書き間違い1つで、URLを知った人が字幕アプリを止められるようになる。
WEB_PORT = 8080
CONTROL_PORT = 8081
# 閲覧を待ち受けるアドレス。既定は自分の機体からだけ。
# トンネルを使うときは cloudflared が 127.0.0.1 に繋ぐので、ここは既定のままでよい。
# 同じLANの端末から直接見せたいときだけ 0.0.0.0 にする（--web-bind）。
# **操作画面はこの設定の影響を受けない。常に 127.0.0.1 である。**
WEB_BIND = "127.0.0.1"
# 書きかけの文字起こしを閲覧画面へ渡す間隔。
#
# **文が確定するまでの数秒、画面には何も出ない。** そこを埋めるために、確定を待たずに
# 途中の文字を流す。delta は中央値 0.01秒 の間隔で来るので、そのまま毎回渡すと
# 長ポーリングが回りっぱなしになる。ここで間引く。
#
# **Zoom字幕と翻訳には流さない。** どちらも出した行を置き換えられない。
WEB_PARTIAL = True
PARTIAL_INTERVAL_SEC = 0.2
# 画面に見せる行数。Zoomの窓は最小4行だが、こちらは自分で決められる。
# 画面共有では圧縮で小さい字が潰れるので、行数を欲張らず大きく出す。
WEB_LINES = 8
# 閲覧URLに入れる、推測できない経路の長さ（バイト）。
# 一時トンネルのホスト名もランダムだが、経路にも入れておく。
VIEWER_SECRET_BYTES = 8
# 長ポーリングで、新しい行が来なかったときに空で返すまでの秒数。
#
# **SSEは使えない。** 一時トンネル（TryCloudflare）は text/event-stream を
# 端で溜め込み、接続が閉じるまでブラウザに届かない。実測でも15秒間1バイトも
# 来なかった。長ポーリングは応答が毎回完結するので通る（実測 0.4〜1.0秒）。
# 根拠は local/HANDOFF.md の「実測結果: 一時トンネル」。
LONGPOLL_WAIT_SEC = 25.0

# --- トンネル（参加者に閲覧URLを配るとき） ----------------------------------
# 一時トンネルは cloudflared が字幕PCから外向きに張る。着信は要らないので、
# 学内LAN・会議室のWiFi・テザリングのどれでも通る。
#
# **既定では張らない。** 未公開の観測結果を扱う会議で、事故で外に出さないため。
# 使うときは操作画面の「トンネル」から開始するか、--tunnel を付けて起動する。
TUNNEL_CMD = "cloudflared"
# 探しに行く場所。PATH に無ければここを見る（local/bin に置く運用）。
TUNNEL_LOCAL = PROJECT_ROOT / "local" / "bin" / "cloudflared.exe"
# URLが出てくるまで待つ秒数。これを過ぎたら失敗として扱う。
TUNNEL_TIMEOUT_SEC = 30.0

# --- 会議の記録 -------------------------------------------------------------
# 確定した1文ごとに、日本語の認識文と英語の字幕を対にして残す（transcript.py）。
# **既定で残す。** 誤認識は日本語の側にしか現れないので、用語表を育てるのに要る。
# 会議の内容が字幕PCのディスクに残るので、要らないときは --no-save で止められる。
#
# **置き場はユーザーのダウンロードフォルダ**（麻生の指示、2026-09-08）。
# 会議のあとすぐ開けるところに出す。置き場を変えるなら --save-dir。
#
# 名前に `live-caption_` を付ける。ダウンロードフォルダは他のファイルと
# 混ざるので、**日付だけの名前では何のファイルか分からない。**
# `scripts/transcript_to_md.py` もこの接頭辞で探す。


def downloads_dir() -> Path:
    """ユーザーのダウンロードフォルダを返す。

    **場所は動かせる。** Windowsは「既知のフォルダ」の設定を見る。
    見つからなければ `~/Downloads`。それも無ければ作る。
    """
    if os.name == "nt":
        try:
            import winreg

            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                # ダウンロードフォルダの既知のフォルダID
                path, _ = winreg.QueryValueEx(
                    k, "{374DE290-123F-4565-9164-39C4925E467B}"
                )
            if path:
                return Path(os.path.expandvars(path))
        except OSError:
            pass
    else:
        # Linux は XDG の設定を見る。無ければ ~/Downloads。
        xdg = os.environ.get("XDG_DOWNLOAD_DIR", "").strip()
        if xdg:
            return Path(os.path.expandvars(xdg))
    return Path.home() / "Downloads"


TRANSCRIPT_DIR = downloads_dir()
TRANSCRIPT_PREFIX = "live-caption_"

# --- その他 -----------------------------------------------------------------
# 用語対訳表は etc/glossary/ に置いた .tsv である。**会議ごとに組み合わせを変える。**
# docs/ ではない。**これは読み物ではなく、アプリが読むデータである。**
# サブシステムによって語彙が違うので、1つの大きな表を全部の会議で使うと、
# 関係の無い語が認識の keywords を食い、上限で本当に要る語が落ちる。
GLOSSARY_DIR = PROJECT_ROOT / "etc" / "glossary"
# 何も選ばれていないときに読むもの（拡張子は付けない）。
GLOSSARY_DEFAULT: tuple[str, ...] = ("KAGRA_basic",)
# 前回の選択。操作画面で選び直すたびに書く。次の起動もこれで始まる。
GLOSSARY_STATE_PATH = PROJECT_ROOT / "local" / "glossary_state.json"
ENV_PATH = PROJECT_ROOT / ".env"


@dataclass
class Settings:
    caption_url: str
    device: str | None = None
    # 使う用語集の名前。None なら、前回の選択（無ければ GLOSSARY_DEFAULT）。
    glossary_names: tuple[str, ...] | None = None
    # 字幕の向き。None なら、前回の選択（無ければ DIRECTION_DEFAULT）。
    direction: str | None = None
    delay: str = ASR_DELAY
    translate_model: str = TRANSLATE_MODEL
    dry_run: bool = False
    languages: tuple[str, ...] = field(default_factory=lambda: ASR_LANGUAGES)
    save: bool = True
    transcript_dir: Path = TRANSCRIPT_DIR


# --- .env から差し替えられる調整つまみ -------------------------------------
#
# **既定値は実測で決めたものである。** 上のコメントに根拠が書いてある。
# 会議室や話し方に合わせて現場で変えたいときだけ、`.env` に書く。
#
# (環境変数名, 定数名, 型, これ未満は受け付けない値)
_TUNABLE = (
    ("LIVECAPTION_IDLE_FLUSH_SEC", "IDLE_FLUSH_SEC", float, 0.3),
    ("LIVECAPTION_SPECULATE_AFTER_SEC", "SPECULATE_AFTER_SEC", float, 0.0),
    ("LIVECAPTION_FORCE_CUT_CHARS", "FORCE_CUT_CHARS", int, 20),
    ("LIVECAPTION_LINE_INTERVAL_SEC", "LINE_INTERVAL_SEC", float, 0.0),
)

# 先回りを投げてから確定までに空けておく秒数。翻訳の中央値 0.9 ＋ 余裕 0.2。
# `IDLE_FLUSH_SEC` だけを差し替えたときに、`SPECULATE_AFTER_SEC` をこれで導く。
SPECULATE_MARGIN_SEC = 1.1

# **差し替える前の値を控えておく。** 操作画面の「既定に戻す」で使う。
# `apply_env_overrides()` が globals() を書き換えるので、その前に取る。
_DEFAULTS = {attr: globals()[attr] for _e, attr, _c, _f in _TUNABLE}

# **人が明示して変えたつまみ。** 向きを切り替えると `FORCE_CUT_CHARS` の既定値も
# 変わる（日本語70 / 英語140）が、**明示した値まで勝手に戻してはいけない。**
# ここに入っているものは向きに追随しない。既定と同じ値に戻したら、ここから外れる。
_EXPLICIT: set[str] = set()
# 画面に出す説明。**単位と、変えるとどうなるかを書く。**
_TUNING_HELP = {
    "IDLE_FLUSH_SEC": "秒。発話が途切れてから、文末記号が無くても確定させるまで。"
                      "実測の delta 間隔の p99 が 2.57 秒なので、下げると話の途中で切る",
    "SPECULATE_AFTER_SEC": "秒。無音がこれだけ続いたら、確定を待たずに翻訳を投げる。"
                           "当たれば約0.9秒早く出る。0 で止める",
    "FORCE_CUT_CHARS": "文字。日本語がこれより長くなったら強制的に切る。"
                       "字幕が速すぎて読めないときは下げる",
    "LINE_INTERVAL_SEC": "秒。Zoomへ1行ずつ送る間隔。字幕の窓は最小4行しかない。"
                         "閲覧画面には効かない",
}


def coerce_tuning(attr: str, raw) -> float | int:
    """調整つまみの値を検算する。駄目なら `ValueError` を投げる。

    **`.env` からも操作画面からも、同じ規則で弾く。** 2か所に書くと食い違う。
    """
    for _env_name, name, cast, floor in _TUNABLE:
        if name != attr:
            continue
        try:
            value = cast(str(raw).strip())
        except (ValueError, TypeError):
            raise ValueError(f"{attr}: 「{raw}」は数字として読めない。") from None
        if value < floor:
            raise ValueError(f"{attr}: {value} は小さすぎる（{floor} 以上にすること）。")
        return value
    raise ValueError(f"{attr} は変えられる設定ではない。")


def default_of(attr: str) -> float | int:
    """そのつまみの既定値。**向きで変わるものは、いまの向きから取る。**

    `FORCE_CUT_CHARS` は聞く言語で変わる（日本語70 / 英語140）。操作画面の
    「既定に戻す」が、向きに合った値に戻るようにするため、ここで分岐する。
    """
    if attr == "FORCE_CUT_CHARS":
        return direction().force_cut_chars
    return _DEFAULTS[attr]


def _remember_explicit(attr: str, value) -> None:
    """人が明示して変えた値かどうかを覚える。

    **既定と同じ値なら「明示していない」に戻す。** 操作画面の「.env に保存」は
    既定のままの項目も書き出すので、`.env` に値があること自体は意思の証拠にならない。
    既定と違う値だけを、向きの切り替えから守る。
    """
    if value == default_of(attr):
        _EXPLICIT.discard(attr)
    else:
        _EXPLICIT.add(attr)


def set_tuning(attr: str, raw) -> float | int:
    """調整つまみを検算して入れる。操作画面から呼ぶ。"""
    value = coerce_tuning(attr, raw)
    globals()[attr] = value
    _remember_explicit(attr, value)
    return value


def tuning() -> list[dict]:
    """調整つまみの、いまの値と既定値。操作画面に返す。"""
    return [
        {
            "name": attr,
            "env": env_name,
            "value": globals()[attr],
            "default": default_of(attr),
            "min": floor,
            "step": 1 if cast is int else 0.1,
            "help": _TUNING_HELP.get(attr, ""),
        }
        for env_name, attr, cast, floor in _TUNABLE
    ]


def direction() -> Direction:
    """いま選ばれている向き。"""
    return DIRECTIONS[DIRECTION]


def apply_direction(name: str) -> Direction:
    """字幕の向きを切り替えて、出力側の定数を差し替える。選んだ向きを返す。

    **翻訳のプロンプトはここでは作り直さない。** 呼ぶ側（`app.apply_direction`）が
    用語表を持っているので、そちらでやる。ここが差し替えるのは定数だけである。
    """
    if name not in DIRECTIONS:
        known = " / ".join(DIRECTIONS)
        raise ValueError(f"字幕の向きが違う: 「{name}」。{known} のどちらか。")
    d = DIRECTIONS[name]
    globals()["DIRECTION"] = name
    globals()["CAPTION_LANG"] = d.caption_lang
    globals()["MAX_CAPTION_CHARS"] = d.max_caption_chars
    globals()["WARMUP_CAPTIONS"] = d.warmup
    # 明示して変えた値は残す。触っていないものだけ、向きの既定値に合わせる。
    if "FORCE_CUT_CHARS" not in _EXPLICIT:
        globals()["FORCE_CUT_CHARS"] = d.force_cut_chars
    return d


def direction_selection() -> str:
    """覚えている向き。無ければ既定。

    **覚えるのは、会議ごとに選び直す手間を無くすためである。** 用語集と同じ考え方。
    起動時の画面と操作画面の両方に出るので、前回のままなことには気づける。
    """
    try:
        saved = json.loads(DIRECTION_STATE_PATH.read_text(encoding="utf-8"))
        name = str(saved.get("name", ""))
    except (OSError, ValueError, AttributeError):
        name = ""
    return name if name in DIRECTIONS else DIRECTION_DEFAULT


def remember_direction(name: str) -> None:
    """次の起動のために選択を覚える。書けなくても落とさない。"""
    try:
        DIRECTION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        DIRECTION_STATE_PATH.write_text(
            json.dumps({"name": name}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"  [向き] 選択を覚えられない: {exc}")


def tuning_warning() -> str:
    """設定の組み合わせがおかしいときの一言。無ければ空文字。"""
    if SPECULATE_AFTER_SEC and SPECULATE_AFTER_SEC >= IDLE_FLUSH_SEC:
        return (f"先回り（{SPECULATE_AFTER_SEC}秒）が確定待ち（{IDLE_FLUSH_SEC}秒）"
                f"以上なので、先回りは一度も走らない。")
    return ""


def save_env(values: dict[str, str], path: Path | None = None) -> Path:
    """`.env` の該当行を書き換える。書いたパスを返す。

    **他の行を消してはいけない。** `.env` には `OPENAI_API_KEY` が入っている。
    既にある行はその場で置き換え、無ければ末尾に足す。
    `#LIVECAPTION_...=` のように畳んである行は、コメントを外して使う
    （`.env.example` をそのまま写した `.env` がこの形になっている）。

    **書き込みは一時ファイル経由で行う。** 途中で落ちて `.env` が壊れると、
    APIキーごと失われる。
    """
    # **既定値引数で ENV_PATH を捕まえない。** import のときに1回だけ評価されるので、
    # 後から差し替えられなくなる（Segmenter で同じ罠を踏んだ）。
    path = ENV_PATH if path is None else path
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)

    for i, line in enumerate(lines):
        stripped = line.strip()
        bare = stripped.lstrip("#").strip()
        key = bare.partition("=")[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        for key, value in remaining.items():
            lines.append(f"{key}={value}")

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def apply_env_overrides() -> None:
    """調整つまみを環境変数（`.env` を含む）で差し替える。

    **定数の行で `os.environ` を読んではいけない。** `config` は `load_env()` より
    先に import されるので、その時点では `.env` はまだ読まれていない。
    差し替えはここで、`.env` を読んだ後に行う。

    **会議の当日に、書き間違いでアプリが起動しないのは困る。** 数字として読めない
    値や小さすぎる値は、警告を出して既定値のままにする。**黙って無視はしない。**
    """
    changed: list[str] = []
    for env_name, attr, _cast, _floor in _TUNABLE:
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        before = globals()[attr]
        try:
            value = coerce_tuning(attr, raw)
        except ValueError as exc:
            print(f"  [設定の警告] {env_name}: {exc} 既定の {before} を使う。")
            continue
        globals()[attr] = value
        _remember_explicit(attr, value)
        # 既定と同じ値が書いてあることは多い（「.env に保存」が全項目を書くため）。
        # 変わっていないものを「差し替えた」と出すと、読む側が混乱する。
        if value != before:
            changed.append(f"{attr} {before} → {value}")

    # `IDLE_FLUSH_SEC` だけを変えたときは、先回りの時刻もそれに合わせる。
    # **合わせないと、先回りが早すぎて投げ捨てが増えるだけになる。**
    if (not os.environ.get("LIVECAPTION_SPECULATE_AFTER_SEC", "").strip()
            and os.environ.get("LIVECAPTION_IDLE_FLUSH_SEC", "").strip()
            and SPECULATE_AFTER_SEC):
        derived = round(max(0.3, IDLE_FLUSH_SEC - SPECULATE_MARGIN_SEC), 2)
        if derived != SPECULATE_AFTER_SEC:
            changed.append(f"SPECULATE_AFTER_SEC {SPECULATE_AFTER_SEC} → {derived}（自動）")
            globals()["SPECULATE_AFTER_SEC"] = derived

    if changed:
        print("  [設定] .env で差し替えた: " + "、".join(changed))

    # 先回りは確定より前に投げないと意味が無い。
    warning = tuning_warning()
    if warning:
        print(f"  [設定の警告] {warning}")


def load_env(path: Path = ENV_PATH) -> None:
    """.env を読んで、調整つまみを反映する。

    **.env の値を優先し、環境変数を上書きする。**
    """
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            # .env の値を優先する。環境変数に同じ名前があっても上書きする。
            if value:
                os.environ[key] = value
    # .env が無くても、環境変数だけで差し替えられるようにする。
    apply_env_overrides()


def openai_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY が無い。.env に書くか、環境変数に設定すること。")
    return key
