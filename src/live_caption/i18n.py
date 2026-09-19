"""操作画面の言語。日本語と英語を切り替える。

**日本語の文をそのまま鍵にする。** 記号の鍵（`msg.start`）を使わない。理由は2つある。

1. 画面のマークアップとJSに手を入れずに済む。訳表を足すだけで英語になる
2. **訳が無い文字列は日本語のまま出る。** 鍵方式だと `{{msg.start}}` が画面に出る。
   会議中にそれが起きるより、日本語が残るほうがまだ読める

置き換えは**長い順**に行う。「開始」は「字幕の生成を開始した。」の中にも現れるので、
短いものから置くと、長い文の一部だけが英語になってしまう。

閲覧画面は元から英語なので、ここでは扱わない。
"""

from __future__ import annotations

LANGS = ("ja", "en")
DEFAULT = "ja"

# 日本語 -> 英語。**画面に出る文字列だけを入れる。** コードのコメントは対象外。
EN: dict[str, str] = {
    # --- ヘッダ ---
    "Live Captions ・ 操作": "Live Captions · Control",
    "生成: —": "Captions: —",
    "配信: —": "Tunnel: —",
    "文字を小さく": "Smaller text",
    "文字を大きく": "Larger text",
    "ドラッグで幅を変える": "Drag to change the width",
    "字幕を待っています…": "Waiting for captions…",

    # --- 字幕の生成 ---
    "字幕の生成": "Caption generation",
    "開始するまで、": "Until you press start, ",
    "音は取り込まれず、文字起こしも翻訳もしない。":
        "no audio is read, and nothing is transcribed or translated.",
    "音は取り込まれず、認識も翻訳もしない。":
        "no audio is read, and nothing is transcribed or translated.",
    "会議に入る前に立ち上げておいてよい。":
        "You can launch it before you join the meeting.",

    # --- 参加者への配信 ---
    "参加者への配信": "Delivery to participants",
    "配信を開始": "Start delivering",
    "経路": "Route",
    "Cloudflare（その場で配る）": "Cloudflare (hand out on the spot)",
    "Tailscale（前もって配る）": "Tailscale (hand out in advance)",
    "ホスト名が変わらないので、": "The host name never changes, so ",
    "会議のURLを前もって配れる。": "you can hand out a meeting URL in advance.",
    "tailnet 側の設定が1回だけ要る。": " It needs one setting on the tailnet side.",
    "準備は要らないが、": "No preparation is needed, but ",
    "URLは起動のたびに変わる。": "the URL changes every time you start it.",
    "前もって配ることはできない。": " You cannot hand it out in advance.",
    "閲覧URLのQRコード": "QR code for the viewer URL",
    "URLをコピー": "Copy the URL",
    "QRコードを保存": "Save the QR code",
    "このURLをQRで配る。参加者はブラウザで開くだけでよい。":
        "Hand out this URL as a QR code. People only open it in a browser.",

    # --- 会議 ---
    "会議": "Meetings",
    "会議ごとに別のURLを使う。": "Each meeting gets its own URL.",
    "配信するのは選んである1つだけで、": "Only the meeting you select is delivered, and ",
    "他の会議のURLは開けない。": "the other meetings' URLs do not open.",
    "会議の名前（例: KAGRA朝礼 9/25）": "Meeting name (for example: KAGRA morning 9/25)",
    "追加": "Add",
    "削除": "Delete",
    "この会議を配信する": "Deliver this meeting",
    "会議のURLはいつでも作れる。Zoomのリンクと一緒に案内に載せられる。":
        "You can make a meeting URL at any time, and put it in the invitation "
        "next to the meeting link.",
    "前もってURLを配るには、上の経路を ": "To hand out a URL in advance, set the route above to ",
    " にすること。": ".",

    # --- Zoom字幕 ---
    "Zoom字幕": "Zoom captions",
    "APIトークン": "API token",
    "登録": "Register",
    "会議中にホストが取る。「字幕」→「∧」→「手動字幕の設定」で":
        'The host gets it during the meeting: "Captions" then the arrow, then '
        '"Manual captions setup". ',
    "手動字幕を有効にしてから、": "Turn manual captions on first,",
    "「APIトークンをコピー」。": 'then choose "Copy the API token".',
    "有効にしないとこの項目は出ない。":
        "The item does not appear until manual captions are on.",
    "入力欄は伏せ字で、登録すると空になる。":
        "The field is masked, and it clears after you register.",

    # --- 画面共有 ---
    "画面共有で見せる": "Show by screen share",
    "閲覧画面を開く": "Open the viewer page",
    "この画面は共有しないこと。": "Never share this page.",
    "共有するのは閲覧画面のほう。": "Share the viewer page instead.",

    # --- 会議の記録 ---
    "会議の記録": "Meeting record",
    "途中まで読む": "Read it so far",
    "パスをコピー": "Copy the path",

    # --- 音声の入力 ---
    "音声の入力": "Audio input",
    "読み込み中…": "Loading…",
    "一覧を更新": "Refresh the list",
    "ファイル（--from-file）": "File (--from-file)",

    # --- 字幕の向き ---
    "字幕の向き": "Caption direction",
    "会議ごとに選ぶ。": "Choose it for each meeting. ",
    "選んだ時点で切り替わる。": "The change takes effect as soon as you pick it. ",
    "次の起動もこの向きで始まる。": "The next start uses the same direction. ",
    "逆の言語が混ざったときは、訳さずにそのまま出す。":
        "When the other language comes in, it is shown as it is, not translated.",
    "日本語 → 英語": "Japanese → English",
    "英語 → 日本語": "English → Japanese",

    # --- 用語集 ---
    "用語集": "Glossary",
    "名前で絞り込む": "Filter by name",
    "全部選ぶ": "Select all",
    "全部外す": "Clear all",

    # --- 遅延の調整 ---
    "遅延の調整": "Delay tuning",
    "よく変えるものではない": "Not something you change often",
    ".env に保存": "Save to .env",
    "既定に戻す": "Reset to defaults",

    # --- アプリの終了 ---
    "アプリの終了": "Quit LiveCaption",
    "音声の取り込みも文字起こしも止まる": "Audio capture and transcription both stop",

    # --- 状態の表示 ---
    "生成: 停止中": "Captions: stopped",
    "生成: 停止": "Captions: stopped",
    "生成: 中": "Captions: on",
    "音を取り込み、文字起こしと翻訳をしている":
        "Reading audio, transcribing and translating",
    "止まっている。音は取り込んでいない": "Stopped. No audio is being read",
    "停止中（音量は生成中に出る）":
        "Stopped (the level shows while captions are being made)",
    "音が来ていない": "No sound is arriving",
    "音が来ている（peak ": "Sound is arriving (peak ",
    "　取りこぼし ": "  dropped ",
    "Zoom: 送信中": "Zoom: sending",
    "Zoom: 停止中": "Zoom: stopped",
    "Zoom: 未登録": "Zoom: no token",
    "（失敗 ": " (failed ",
    "登録済み（会議 ": "Registered (meeting ",
    "トークン未登録": "No token yet",
    "送信 ": "sent ",
    " / 失敗 ": " / failed ",
    "**生成が止まっているので何も流れない**":
        "**Caption generation is stopped, so nothing is sent**",
    "配信: 停止中": "Tunnel: stopped",
    "配信: 起動中…": "Tunnel: starting…",
    "配信: 中": "Tunnel: on",
    "配信: 失敗": "Tunnel: failed",
    "配信: 停止": "Tunnel: stopped",
    "参加者が閲覧URLを開ける": "People can open the viewer URL",
    "cloudflared を起こしている": "Starting cloudflared",
    "cloudflared が無い": "cloudflared is not installed",
    "残さない（--no-save）": "Not saved (--no-save)",
    "残せていない: ": "Cannot save: ",
    " 文を記録した（終了時に読める形も書く）":
        " sentences recorded (a readable file is written at exit)",

    # --- 操作の結果 ---
    "字幕の生成を開始した。": "Caption generation started.",
    "字幕の生成を停止した。": "Caption generation stopped.",
    "選べない": "Cannot choose",
    "入力の一覧を取れない: ": "Cannot get the device list: ",
    "一覧を取り直した。": "The list was refreshed.",
    "入力を ": "Input set to ",
    " にした。": ".",
    "etc/glossary/ に .tsv が無い": "No .tsv file in etc/glossary/",
    "選ばれていない": "None selected",
    "　ほか ": "  and ",
    "文字起こしに渡す語 ": "Words sent to the recogniser ",
    " 語が上限で切り捨てられた。選ぶ表を減らすこと":
        " terms were cut by the limit. Use fewer tables",
    "当てはまる表が無い": "No table matches",
    "用語集の一覧を取れない: ": "Cannot get the glossary list: ",
    "用語集を ": "Glossary set to ",
    "(なし)": "(none)",
    "Zoomへは ": "Sent to Zoom as ",
    " として送る": "",
    "字幕の向きを取れない: ": "Cannot get the direction: ",
    "字幕の向きを変えた。": "Direction changed.",
    "既定 ": "default ",
    " 個を既定から変えている": " changed from the default",
    "保存先: ": "Saved to: ",
    "設定を取れない: ": "Cannot get the settings: ",
    " を ": " set to ",
    "既定値に戻した。": "Reset to the defaults.",
    ".env に保存した: ": "Saved to .env: ",
    "トークンを貼ること。": "Paste the token first.",
    "登録した。会議 ": "Registered. Meeting ",
    "。「開始」で送信を始める。": ". Press Start to begin sending.",
    "送信を開始した。": "Sending started.",
    "送信を停止した。": "Sending stopped.",
    "配信を始めている。URLが出るまで数秒かかる。":
        "Starting the delivery. The URL takes a few seconds.",
    "配信を止めた。閲覧URLは死んだ。":
        "The delivery is stopped. The viewer URL is dead.",
    "経路を選んだ。「配信を開始」で始める。":
        "Route selected. Press Start delivering to begin.",
    "tailscale に設定させている": "letting tailscale set it up",
    "tailscale が使えない": "tailscale is not usable",

    # --- 会議（画面の中で組み立てる文） ---
    # **ここに二重引用符を入れてはいけない。** 訳したあとの文字列は JavaScript の
    # 文字列リテラルの中に入る。`"` を入れるとリテラルがそこで閉じ、
    # **操作画面のスクリプト全体が構文エラーになって、どのボタンも効かなくなる。**
    # 同じ理由で、JavaScript の文字列に 「」 を書いてはいけない（下の記号の欄で
    # `"` に化ける）。名前は「: 」の後ろに置いて囲まない。
    "配信する会議: ": "Now delivering: ",
    "会議の名前を入れること。": "Type a meeting name first.",
    "会議を作った: ": "Meeting created: ",
    "。配信する会議は変えていない。":
        ". The meeting being delivered has not changed.",
    "会議を消した: ": "Meeting deleted: ",
    "会議を消した。閲覧URLが要るので、新しい会議を1つ作った。":
        "Meeting deleted. A viewer URL is always needed, so a new meeting "
        "was created.",
    "この会議を消す: ": "Delete this meeting: ",
    "。このURLは開けなくなる。よろしいですか。":
        ". Its URL stops working. Are you sure?",
    "URLがまだ決まらない。Tailscale に繋がっているか確かめること。":
        "No URL yet. Check that Tailscale is connected.",
    "Cloudflare ではURLが毎回変わる。配信を始めると出る。":
        "With Cloudflare the URL changes every time. It appears once you start "
        "delivering.",
    "クリップボードに書けない。選んであるので Ctrl+C を押すこと。":
        "Cannot write to the clipboard. The text is selected, so press Ctrl+C.",
    "コピーした": "Copied",
    "字幕アプリを終了する。よろしいですか。": "Quit LiveCaption. Are you sure?",
    "終了した。この画面を閉じる。": "Stopped. Closing this page.",
    "字幕アプリを終了した。": "LiveCaption has stopped.",
    "このタブは閉じてよい。": "You can close this tab.",
    "（ブラウザがタブを自動で閉じない設定になっている）":
        "(your browser does not close tabs by itself)",
    "終了した": "Stopped",
    "接続できません（": "Cannot connect (",
    "）。再試行 ": "). Retry ",
    "回目…": "…",

    # --- サーバから来る説明（config.py の調整つまみ）---
    "秒。発話が途切れてから、文末記号が無くても確定させるまで。"
    "実測の delta 間隔の p99 が 2.57 秒なので、下げると話の途中で切る":
        "Seconds to wait after speech stops before finalising a sentence without "
        "an end mark. The measured p99 gap is 2.57 s, so a lower value cuts "
        "sentences in the middle",
    "秒。無音がこれだけ続いたら、確定を待たずに翻訳を投げる。"
    "当たれば約0.9秒早く出る。0 で止める":
        "Seconds of silence after which the translation is sent without waiting "
        "for the sentence to be final. When it matches, the caption appears about "
        "0.9 s earlier. 0 turns it off",
    "文字。日本語がこれより長くなったら強制的に切る。"
    "字幕が速すぎて読めないときは下げる":
        "Characters. A sentence longer than this is cut. Lower it when the "
        "captions go by too fast to read",
    "秒。Zoomへ1行ずつ送る間隔。字幕の窓は最小4行しかない。"
    "閲覧画面には効かない":
        "Seconds between lines sent to Zoom. The caption window is only four "
        "lines. It does not apply to the viewer page",

    # --- 失敗したときに画面へ返す文 ---
    "用語集の受け口が用意できていない。": "The glossary control is not available.",
    "設定の受け口が用意できていない。": "The settings control is not available.",
    "音声の受け口が用意できていない。": "The audio control is not available.",
    "生成の受け口が用意できていない。": "The caption control is not available.",
    "字幕の向きの受け口が用意できていない。": "The direction control is not available.",
    "トンネルの受け口が用意できていない。": "The tunnel control is not available.",
    "用語集が無い: ": "No such glossary table: ",
    "先にトークンを入れること。": "Enter the token first.",
    "言語が違う: ": "Unknown language: ",
    "字幕の向きが違う: ": "Unknown caption direction: ",
    " のどちらか。": " is what you can use.",
    " のどれか。": " is what you can use.",
    "は小さすぎる（": " is too small (",
    " 以上にすること）。": " or more).",
    "」は数字として読めない。": '" is not a number.',
    "は変えられる設定ではない。": " is not a setting you can change.",
    "--dry-run で起動しているので、Zoomへは送らない。"
    "送るなら --dry-run を外して起動し直すこと。":
        "Started with --dry-run, so nothing is sent to Zoom. "
        "Restart without --dry-run to send.",
    "--from-file で起動しているので、入力は選べない。":
        "Started with --from-file, so the input device cannot be changed.",

    # --- 単独で出る短い語。**長い鍵を先に置き換えたあとに残ったものだけが当たる** ---
    "文字起こし": "Source",
    " 語": " terms",
    "開始": "Start",
    "停止": "Stop",
    "終了": "Quit",

    # --- 記号。**いちばん短いので最後に置き換わる** ---
    # 長い鍵を先に当てたあとに残るのは、区切りに使っている記号だけである。
    "（": "(",
    "）": ")",
    "「": '"',
    "」": '"',
    "。": ". ",
    "、": ", ",
    "　": " · ",   # 状態の表示をつなぐ全角空白
}

# **長い順に置き換える。** 短い鍵を先に置くと、長い文の一部だけが英語になる。
_ORDER = sorted(EN, key=len, reverse=True)


def apply(text: str, lang: str) -> str:
    """文字列を指定した言語にする。`ja` なら何もしない。"""
    if lang != "en" or not text:
        return text
    for ja in _ORDER:
        if ja in text:
            text = text.replace(ja, EN[ja])
    return text


def apply_json(obj, lang: str):
    """JSONで返す入れ子を、まとめて言語に合わせる。

    **字幕そのものには使わない。** 会議の中身を訳してはいけない。
    使うのは状態や説明を返す口だけである（`web.py` の `_send_json`）。
    """
    if lang != "en":
        return obj
    if isinstance(obj, str):
        return apply(obj, lang)
    if isinstance(obj, dict):
        return {k: apply_json(v, lang) for k, v in obj.items()}
    if isinstance(obj, list):
        return [apply_json(v, lang) for v in obj]
    return obj


def remaining_japanese(text: str) -> list[str]:
    """訳し残した日本語を返す。`scripts/check_ui_lang.py` が使う。"""
    import re

    return sorted(set(re.findall(r"[ぁ-んァ-ヶ一-龥々]+", text)))
