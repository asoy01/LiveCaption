"""設定。実測で決まった値は既定値として埋め込んである。

根拠は local/HANDOFF.md の「実測結果」にある。値を変えるときは、そこも読むこと。
"""

from __future__ import annotations

import ipaddress
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
# 「音が来ている」と見なす振幅。無音の見張りと `--check-audio` が同じ値を使う。
# **実測に基づく。** 無音は 0.003〜0.008、話し声は 0.698 まで振れた
# （local/HANDOFF.md「実測結果」）。
VOICE_PEAK = 0.01

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
# --- 操作画面の言語 ---------------------------------------------------------
# **閲覧画面は対象外。** あちらは元から英語で、参加者が見るものである。
# 訳表は `i18n.py` にある。選択は覚えて、次の起動も同じ言語で始める。
UI_LANG = "ja"
UI_LANG_STATE_PATH = PROJECT_ROOT / "local" / "ui_lang_state.json"

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

# 配信の経路は2つある。操作画面で選び、前回の選択を覚える。
#
#   cloudflare  一時トンネル。**URLは起動のたびに変わる。** 準備は要らない。
#               その場で決まった会議に向く。
#   tailscale   Tailscale Funnel。**ホスト名が変わらない。** 会議のURLを前もって
#               作って案内に載せられる。tailnet 側の設定が1回だけ要る。
TUNNEL_KINDS = ("cloudflare", "tailscale")
TUNNEL_KIND = "cloudflare"
TUNNEL_KIND_STATE_PATH = PROJECT_ROOT / "local" / "tunnel_kind.json"
TAILSCALE_CMD = "tailscale"
# Windows の既定の置き場。PATH に無ければここを見る。
TAILSCALE_LOCAL = Path(r"C:\Program Files\Tailscale\tailscale.exe")
# Funnel が公開側で受けるポート。**443・8443・10000 しか選べない**（Tailscaleの制限）。
FUNNEL_PUBLIC_PORT = 443
# tailnet 上のホスト名を覚えておく秒数。**0 にすると毎回プロセスを起こす。**
# 操作画面は2秒ごとに状態を取りに来るので、覚えないと1日に数万回になる。
TAILSCALE_HOST_CACHE_SEC = 30.0
# Tailscale が配るアドレスの範囲。**操作画面を出してよいのはここだけである。**
# 汎用のバインド指定にしてはいけない。0.0.0.0 と書けば、認証の無い操作画面が
# 学内LANの全員に見える。
# tailnet のアドレスが取れるまで、背景で試し直す間隔（秒）。
# 自動起動では Tailscale がまだ上がっていないことがある。
CONTROL_BIND_RETRY_SEC = 15.0

# --- 常駐（タスクトレイとログ） ---------------------------------------------
# **窓を消すなら、ログの行き先を先に決めること。** いまのところ、このアプリの
# 唯一の記録は端末に出る行である。窓を消したら、どこかに残さないと失敗が追えない。
# アプリのアイコン。スタートメニューのショートカットと、タスクトレイが使う。
APP_ICON = PROJECT_ROOT / "etc" / "LiveCaption.ico"
LOG_DIR = PROJECT_ROOT / "local" / "log"
LOG_KEEP = 20
TAILSCALE_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)

# --- 会議ごとの閲覧URL -------------------------------------------------------
# **会議ごとに別のURLを使う。** 参加者が会議ごとに違うので、前の会議のURLで
# 今日の字幕が見えてはいけない。URLに載る `/v/<経路>` の部分を会議ごとに作る。
#
# **前もって作れる。** Tailscale ではホスト名が分かっているので、会議の前日でも
# URLを確定できる。Zoomのリンクと一緒に案内に載せるための作りである。
#
# **配信するのは選んである1つだけ。** 他の会議のURLは、その日には404を返す。
MEETINGS_STATE_PATH = PROJECT_ROOT / "local" / "meetings.json"
# ホストがトークンを貼るURLの経路の長さ（バイト）。
# **閲覧用（VIEWER_SECRET_BYTES）より長くする。** 閲覧用は破られても字幕が漏れる
# だけで、しかも数十人に配る。こちらは Zoom へ何を送るかを決める。
HOST_SECRET_BYTES = 32

# --- 予定された会議に自動で参加する ------------------------------------------
# **既定では何もしない。** 会議ごとに `auto` の印を付けたものだけを回す。
# 配信は「未公開の内容を扱う会議では使わない」という決まりで既定を切りにしてある。
# 無人で配信を始めるのはその既定を裏返す操作なので、人が1件ずつ選ぶ。
SCHEDULE_TICK_SEC = 5.0
# 開始からこれだけ過ぎた回は、もう拾わない。止めていた間に流れた回を
# まとめて始めないため。
SCHEDULE_GRACE_MIN = 10
# 生成が本当に始まったかを確かめるまでの猶予。
# **`generating` だけを見てはいけない。** 入力を開けなかったときも False に戻る。
SCHEDULE_ARM_SEC = 90.0
# Zoomに入れと言ってから、音が一度も来ないまま「入れていない」と見なすまでの秒数。
# **急がない。** 会議が始まるのは遅れるものである。待機室・パスコード違い・更新の
# ダイアログは、どれも「音が来ない」としてしか観測できない。
SCHEDULE_JOIN_AUDIO_SEC = 300.0

# Zoomのチャットに投げる時刻。**予定の開始時刻を過ぎてから投げる。**
#
# **Zoomのチャットは、入る前の発言が見えない。** 早く投げると、後から入って
# きた人には何も残らない（麻生の指摘、2026-09-20）。字幕アプリは開始の
# `lead_min` 分前に動き出すので、そこで投げると全員が取りこぼす。
#
# **2回投げる。開始時刻と、その3分後**（麻生の指示、2026-09-20）。同じ内容である。
# 1回目は定刻に入った人に、2回目は少し遅れて入った人に届く。
SCHEDULE_CHAT_AT_MIN = (0.0, 3.0)
# 2回が続けて出ないように空ける下限。Zoomの参加が遅れて1回目が押し出されると、
# 2回目の時刻を既に過ぎていることがある。**同じ文が数秒差で2つ並ぶのは、
# 壊れているように見える。**
SCHEDULE_CHAT_GAP_SEC = 60.0
# そこから、投げられないまま諦めるまでの上限と、見に行く間隔。
# **`zoommtg:` を投げてから会議の窓が出るまで、Zoomは数十秒から数分かかる。**
# 実測では、起動の5秒後にはまだ無かった（2026-09-20 の実会議）。
SCHEDULE_CHAT_WAIT_SEC = 600.0
SCHEDULE_CHAT_RETRY_SEC = 10.0
# Zoomに出す表示名。**発言しない参加者だと分かる名前にする。**
ZOOM_DISPLAY_NAME = "Live Captions"

# --- ホストがトークンを貼る受け口 --------------------------------------------
# **これはトンネル越しに出る、唯一の書き込み口である。** 閲覧サーバは他に
# 状態を変える手段を持たない。狭く作り、開いている時間を短くする。
#
# 予定の開始前後、これだけの分は受け付ける。**恒久的な口にしない。**
# 会議1本あたり1時間ほどに絞られる。
HOST_TOKEN_WINDOW_MIN = 30
# 失敗をこれだけ数えたら、その会議の受け口を閉じる。
# 32バイトの経路を総当たりされる心配は無いが、走査や不具合で叩かれ続けるのを止める。
HOST_MAX_ATTEMPTS = 5
# 受け口を叩ける最短の間隔（秒）。長ポーリングと同じ機体で実時間の文字起こしを
# しているので、洪水を浴びせられないようにする。
HOST_MIN_INTERVAL_SEC = 1.0
# 受け取る本文の上限。トークンのURL1本しか来ないので、小さくてよい。
HOST_MAX_BODY = 4096
# **Cloudflare では開かない。** TLS が Cloudflare の入口で終わるので、
# Zoom の資格情報がそこを平文で通る。Tailscale なら TLS はこの機体で終わる。
HOST_TOKEN_KINDS = ("tailscale",)


# --- 会議の記録 -------------------------------------------------------------
# 確定した1文ごとに、日本語の認識文と英語の字幕を対にして残す（transcript.py）。
# **既定で残す。** 誤認識は日本語の側にしか現れないので、用語表を育てるのに要る。
# 会議の内容が字幕PCのディスクに残るので、要らないときは --no-save で止められる。
#
# **置き場は `local/transcripts/`**（麻生の指示、2026-09-20）。**字幕PCの中に
# 溜めておいて、操作画面から落とす。** 字幕PCは常時起動で、操作は tailnet 越し
# である。ダウンロードフォルダに出しても、取りに行くには RustDesk が要る。
# 記録を読むためだけに遠隔操作を起こすのは重い。
#
# （2026-09-08 から 2026-09-20 まではダウンロードフォルダだった。そのときの
# 理由は「会議のあとすぐ開ける」ことで、字幕PCの前に座る前提だった。）
#
# 置き場を変えるなら `.env` の `LIVECAPTION_SAVE_DIR` か `--save-dir`。
#
# 名前に `live-caption_` を付ける。`scripts/transcript_to_md.py` もこの接頭辞で
# 探す（`*.jsonl` で探すと、無関係なファイルを拾う）。
TRANSCRIPT_DIR = PROJECT_ROOT / "local" / "transcripts"
TRANSCRIPT_PREFIX = "live-caption_"

# 置き場を変えるときに書く環境変数。`.env` に書けば次の起動から効く。
SAVE_DIR_ENV = "LIVECAPTION_SAVE_DIR"

# 「停止」を押してから、記録を区切るまでの秒数。
#
# **停止は「この会議は終わり」の意味である**（配信もZoom字幕も閉じる）。記録も
# そこで閉じて `.md` を書く。閉じないと、落とした `.md` に「会議はまだ続いている」
# と書かれたままになる（麻生の指摘、2026-09-20）。
#
# **すぐには閉じない。** 止めた時点で、最後の1文がまだ翻訳の途中のことがある
# （翻訳の中央値 0.9秒、行の間隔 0.6秒）。即座に区切ると、その1文だけが次の
# 記録に落ちる。3秒待てば、書き終わってから区切れる。
# **この間に再開したら区切らない。** 休憩で止めただけなら、記録は1本のままにする。
STOP_ROLL_WAIT_SEC = 3.0


def check_save_dir(path: str | Path) -> Path:
    """記録の置き場として使えるか確かめる。使える絶対パスを返す。

    **無ければ作る。** 会議の前に「フォルダが無い」で止まるより、作ってしまう
    ほうがよい。作れない場所（権限、存在しないドライブ）はここで弾く。

    **書けるかどうかは、実際に書いて確かめる。** Windows では、読めるのに
    書けないフォルダ（ドライブの直下、OneDrive の同期中）がある。属性を見るだけ
    では通ってしまい、会議の最中に記録だけが静かに落ちる。
    """
    text = str(path).strip().strip('"')
    if not text:
        raise ValueError("フォルダを指定すること。")
    target = Path(os.path.expandvars(text)).expanduser()
    if not target.is_absolute():
        raise ValueError("絶対パスで指定すること。")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"そのフォルダは作れない: {exc}") from exc
    if not target.is_dir():
        raise ValueError("フォルダではない。")
    probe = target / ".livecaption_write_test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ValueError(f"そのフォルダには書けない: {exc}") from exc
    return target

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


def tunnel_kind_selection() -> str:
    """覚えている配信の経路。無ければ Cloudflare。"""
    try:
        saved = json.loads(TUNNEL_KIND_STATE_PATH.read_text(encoding="utf-8"))
        kind = str(saved.get("kind", ""))
    except (OSError, ValueError, AttributeError):
        kind = ""
    return kind if kind in TUNNEL_KINDS else TUNNEL_KIND


def remember_tunnel_kind(kind: str) -> None:
    """次の起動のために覚える。書けなくても落とさない。"""
    try:
        TUNNEL_KIND_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        TUNNEL_KIND_STATE_PATH.write_text(
            json.dumps({"kind": kind}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"  [配信] 経路を覚えられない: {exc}")


def ui_lang_selection() -> str:
    """覚えている操作画面の言語。無ければ日本語。"""
    from . import i18n

    try:
        saved = json.loads(UI_LANG_STATE_PATH.read_text(encoding="utf-8"))
        name = str(saved.get("lang", ""))
    except (OSError, ValueError, AttributeError):
        name = ""
    return name if name in i18n.LANGS else i18n.DEFAULT


def apply_ui_lang(name: str) -> str:
    """操作画面の言語を切り替える。選んだ言語を返す。"""
    from . import i18n

    if name not in i18n.LANGS:
        raise ValueError(f"言語が違う: 「{name}」。{' / '.join(i18n.LANGS)} のどちらか。")
    globals()["UI_LANG"] = name
    return name


def remember_ui_lang(name: str) -> None:
    """次の起動のために覚える。書けなくても落とさない。"""
    try:
        UI_LANG_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        UI_LANG_STATE_PATH.write_text(
            json.dumps({"lang": name}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"  [言語] 選択を覚えられない: {exc}")


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
