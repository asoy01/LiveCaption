# CLAUDE.md

## Overview

**LiveCaption** — 日本語で行われる会議に、リアルタイムの英語字幕を出すシステム。
少人数の外国人参加者（KAGRAの共同研究者）が、日本語の会議についていけるようにするのが目的。

**Zoomに限らない。** いまの出口はZoom字幕API・画面共有・参加者へのURL配布の3つで、
音声を `CABLE Output` から取る作りなので、音が出るものなら会議ソフトを選ばない。
名前を `zoom-live-caption` から `LiveCaption` に変えたのはこのため（2026-09-08）。

### 構成（確定済み）

字幕専用のWindows PCを1台用意し、会議に「無言の参加者」として参加させる。

```
[ホストPC]  通常どおりZoomで会議
     ↓
[字幕専用PC]
  Zoom ── 無言の参加者として参加、マイクはミュート
   └─ スピーカー出力 → CABLE Input
                          ↓ (VB-CABLE = 仮想オーディオケーブル)
                      CABLE Output → 字幕アプリ
                                       ├ 音声認識（用語リスト付き）
                                       ├ 翻訳（用語対訳表 + 文脈）
                                       └ HTTP POST → Zoom字幕API
                                                       ↓
                                            全参加者のZoom画面に英語字幕
```

専用PCは自分では発言しないので、Zoomから届くミックス音声に全参加者の声が入る。
ホストPCでループバック録音する方式は、Zoomが自分のマイク音声をスピーカーに流さないため、
ホストの声が取れない。この理由で専用PC方式を採用した。

VB-CABLE を使うと、字幕アプリは `CABLE Output` を普通の録音デバイスとして開けばよい。
WASAPI ループバックAPIを使わないので、音量やミュートの影響を受けない。

### 経緯と、採用しなかった選択肢

- **Zoom標準の翻訳字幕(Translated Captions)** — 東大アカウントにライセンスが無い。
  ライセンスのある別アカウントで試したが、専門用語の精度が不十分だった。
  用語を教える手段が無いため、KAGRAの用語が多い会議には向かない。
- **Wordly** — 精度は良いが高額（年間契約、10時間から）。
- **VoiceMeeterでホストPCのマイクとスピーカーを混ぜる方式** — 1台で完結するが、
  当日トラブると切り分けに時間がかかる。専用PC方式を優先した。

### 設計方針

**用語対応は2段階に分ける。** 音声認識だけで解こうとしない。

1. **認識側** — keyterm prompting / phrase list で固有名詞の音を拾わせる。
2. **翻訳側** — LLMに用語対訳表と直前2〜3文の文脈を渡す。ここが本命。
   認識側が音的に近い誤りをしても、文脈と用語表があれば復元できる。

**字幕は確定した部分だけを流す。** 途中経過を表示して後から書き換えると、読み手は追えない。

### 音声認識の候補（未決定）

録音した実会議で比較してから決める。

| 部品 | 用語指定の方法 | 料金（ストリーミング） |
|---|---|---|
| Deepgram Nova-3 | keyterm prompting（専用機能、日本語対応） | $0.0077/分 |
| OpenAI gpt-transcribe streaming | プロンプトで語彙を渡す | $0.017/分 |
| faster-whisper 自前 | initial_prompt のみ（弱い） | GPU代のみ |

### Zoom字幕API

会議中に ホスト/共同ホストが 字幕アイコン →「APIトークンをコピー」でURLを取得する。
そのURLに `&seq=N&lang=en-US` を付けて、UTF-8のプレーンテキストをPOSTする。
`seq` は新しい字幕ごとに1ずつ増やす（リトライでは増やさない）。

前提として、管理者設定で以下が有効になっている必要がある（確認済み・使用可）:
「Allow use of caption URL to integrate with 3rd-party Closed Captioning services」

将来的には Server-to-Server OAuth アプリを作り、
`GET /v2/meetings/{meetingId}/token?type=closed_caption`（スコープ `meeting:read`）で
トークンを自動取得できる。ただし東大側の管理者承認が要る可能性があるため、当面は手動。

## Folder Structure

```
StartLiveCaption.bat  会議で使う入口。ダブルクリックで起動する
run.py                起動と引数の解釈
src/live_caption/     字幕システム本体
scripts/              単発の検証スクリプト
compose.yml           Dockerで回すときの入口（移行中）
docker/Dockerfile     字幕コンテナの作り方
data/recordings/      比較用の会議録音（gitignore対象）
docs/                 用語対訳表、調査メモ
local/                作業物と HANDOFF.md（gitignore対象）
```

**引き継ぎは `local/HANDOFF.md` に書く。** いまの仕様と、これからの作業に要るもの
だけを置く。済んだ判断の根拠（実測結果）と作業の記録（セッション記録）は
`local/HANDOFF_ARCHIVE.md` に移してある。**決まったことを覆したくなったら、
測り直す前に書庫を読むこと。** 新しいことを書き足す先は `HANDOFF.md` である。

**Windows版は凍結し、Linux/Docker に一本化する**（2026-09-20 に決定）。
常時起動の Linux 機 osmium で回す。段階0（会議ソフトの選定）と段階1
（エンジンのコンテナ化）は済んだ。**進め方と実測は `local/HANDOFF.md` の
「字幕PCをやめて、Dockerに移す」にある。Docker に触る前に読むこと。**

**会議の記録は `local/transcripts/` に溜まる**（`live-caption_<日時>.jsonl` と `.md`）。
**操作画面から落とす。** 字幕PCは遠隔で操作するので、記録を取りに行かなくて済むように
してある。置き場を変えるなら `.env` の `LIVECAPTION_SAVE_DIR`。

## Python

pixi環境が作られています。
必要に応じて、`pixi add` でパッケージをインストールしてください。

Pythonの実行:

```
pixi run python
```

Python 3.12 に固定してあります。音声系ライブラリ（ctranslate2, soundcard 等）の
ホイールが揃っているためです。上げる場合は各パッケージの対応を確認してください。
