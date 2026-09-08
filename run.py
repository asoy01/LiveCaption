#!/usr/bin/env python3
"""LiveCaption — 日本語の会議に、リアルタイムの英語字幕を出す。

会議で使う:

    pixi run python run.py --device "CABLE Output" --token "<ZoomのAPIトークンURL>"

トークンの取り方（ホストが会議中に行う）:

    1. ツールバーの「字幕」の横の「∧」をクリック
    2. 「手動字幕の設定」→「APIトークンをコピー」
    3. チャットで字幕PCに送る

ホストでない会議で使う（ブラウザに出して、その画面を共有する）:

    pixi run python run.py --web

**いちばん簡単な起動は「StartLiveCaption.bat」のダブルクリックである。**
これは `--web` で起動し、操作画面をブラウザで開く。Zoomのトークンも、
参加者に配るURLも、終了も、その画面から扱う。

参加者に閲覧URLを配る（一時トンネル。ホスト権限も画面共有も要らない）:

    操作画面の「トンネルを開始」を押す。QRコードと閲覧URLが出る

**トンネルは既定では張らない。** 字幕は Cloudflare を通るので、未公開の
観測結果を扱う会議では画面共有に留めること。起動時から張るなら --tunnel。

**--web を付けると、字幕の生成は停止した状態で始まる。** 操作画面の「開始」を
押すまで、音は取り込まれず、認識も翻訳もしない。会議に入る前に立ち上げてよい。
--web を付けない起動（--token だけ、--dry-run だけ）は、押す手段が無いのですぐ始まる。

**会議の記録は既定で残る。** 日本語の認識文と英語の字幕を対にして、
**ユーザーのダウンロードフォルダ**に `live-caption_<日時>.jsonl` と `.md` で置く。
要らないときは --no-save、置き場を変えるなら --save-dir。

会議を開かずに全体を試す（録音を実時間で流す）:

    pixi run python run.py --from-file local/SampleRecordings/_wav24k/mix.wav --dry-run

入力デバイスの一覧:

    pixi run python run.py --list-devices

**用語集は会議ごとに組み合わせを変える。** `etc/glossary/` に置いた .tsv を
必要なぶんだけ重ねる。操作画面の「用語集」で選べる。選択は覚えているので、
次の起動も同じ組み合わせで始まる。起動時に決めるなら:

    pixi run python run.py --glossary KAGRA_basic Interferometer
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from live_caption import app as app_mod  # noqa: E402
from live_caption import audio as audio_mod  # noqa: E402
from live_caption import config  # noqa: E402


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Zoom会議のリアルタイム英語字幕",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--token", help="ZoomのAPIトークンURL")
    p.add_argument("--device", help="入力デバイス名の一部（既定: CABLE Output）",
                   default="CABLE Output")
    p.add_argument("--from-file", help="デバイスの代わりにWAVを実時間で流す（24 kHz mono）")
    p.add_argument("--loop", action="store_true", help="--from-file を繰り返す")
    p.add_argument("--dry-run", action="store_true", help="Zoomには送らず画面に出すだけ")
    p.add_argument("--delay", default=config.ASR_DELAY,
                   choices=["minimal", "low", "medium", "high", "xhigh"],
                   help="認識の遅延と精度の調整（既定: low）")
    p.add_argument("--model", default=config.TRANSLATE_MODEL, help="翻訳のモデル")
    p.add_argument("--glossary", nargs="*", metavar="名前", default=None,
                   help="使う用語集（etc/glossary/ の .tsv の名前）。複数を重ねられる。"
                        "例: --glossary KAGRA_basic Interferometer。"
                        "指定しなければ前回の選択（操作画面からいつでも変えられる）")
    p.add_argument("--web", nargs="?", type=int, const=config.WEB_PORT, default=None,
                   metavar="ポート",
                   help=f"ブラウザに字幕を出す（閲覧は既定 {config.WEB_PORT}番）。--token と併用可")
    p.add_argument("--control-port", type=int, default=config.CONTROL_PORT,
                   metavar="ポート",
                   help=f"操作画面のポート（既定: {config.CONTROL_PORT}）。"
                        "**常に 127.0.0.1 でしか待ち受けない**")
    p.add_argument("--web-bind", default=config.WEB_BIND, metavar="アドレス",
                   help=f"**閲覧画面**を待ち受けるアドレス（既定: {config.WEB_BIND}）。"
                        "同じLANの端末から直接見せるなら 0.0.0.0。"
                        "操作画面はこの設定の影響を受けない")
    p.add_argument("--tunnel", action="store_true",
                   help="起動時から一時トンネルを張り、参加者に配るURLを作る。"
                        "既定では張らない（操作画面からいつでも開始できる）")
    p.add_argument("--cloudflared", metavar="パス",
                   help="cloudflared の場所。PATH と local/bin にあれば要らない")
    p.add_argument("--no-browser", action="store_true",
                   help="--web のときにブラウザを自動で開かない")
    p.add_argument("--no-save", action="store_true",
                   help="会議の記録を残さない（既定では残す）")
    p.add_argument("--save-dir", metavar="フォルダ", default=config.TRANSCRIPT_DIR,
                   help=f"記録の置き場（既定: {config.TRANSCRIPT_DIR}）")
    p.add_argument("--list-devices", action="store_true", help="入力デバイスの一覧を出す")
    p.add_argument("--check-audio", nargs="?", type=float, const=20.0, default=None,
                   metavar="秒",
                   help="音量だけを表示する（APIを呼ばない）。実機の試験はここから")
    return p.parse_args()


def main() -> int:
    # **デバイス名には、コンソールの文字コードで書けない文字が混じる。**
    # 例: 「マイク配列 (デジタルマイク向けインテル(R) スマート・サウンド」の (R)。
    # 既定のままだと、その名前を print した時点で UnicodeEncodeError で落ちる。
    # 入力を選び直しただけでアプリが止まるのは困るので、書けない文字は置き換える。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    args = parse_args()
    config.load_env()

    if args.list_devices:
        for i, name, ch, api in audio_mod.list_devices():
            print(f"  {i:3d}  {name}  ({ch} ch, {api})")
        return 0

    if args.check_audio is not None:
        capture = audio_mod.Capture(device=audio_mod.find_device(args.device))
        ok = asyncio.run(audio_mod.check_level(capture, args.check_audio))
        return 0 if ok else 1

    if not args.dry_run and not args.token and args.web is None:
        print("出口が無い。--token（Zoom字幕API）か --web（ブラウザ字幕）を指定すること。")
        print("会議を開かずに試すなら --dry-run を付ける。")
        return 1

    # トークンは会議が始まらないと取れない。無いまま起動してよい。
    # --web を付けてあれば、ブラウザの操作画面から後で入れられる。
    settings = config.Settings(
        caption_url=args.token or "",
        device=args.device,
        delay=args.delay,
        translate_model=args.model,
        dry_run=args.dry_run,
        save=not args.no_save,
        transcript_dir=Path(args.save_dir),
        glossary_names=None if args.glossary is None else tuple(args.glossary),
    )

    if args.from_file:
        capture = audio_mod.FileCapture(args.from_file, loop_forever=args.loop)
    else:
        capture = audio_mod.Capture(device=audio_mod.find_device(args.device))

    web = None
    if args.web is not None:
        from live_caption import tunnel as tunnel_mod
        from live_caption import web as web_mod

        web = web_mod.WebCaptions(
            port=args.web, control_port=args.control_port, bind=args.web_bind
        )
        # トンネルは口だけ用意しておく。張るかどうかは別（既定では張らない）。
        web.tunnel = tunnel_mod.Tunnel(args.web, command=args.cloudflared)

        def announce() -> None:
            """トンネルの状態が変わったら端末にも出す。

            **URLは起動表示より後に出てくる。** cloudflared が張り終えるまで
            数秒かかるためである。ここで出さないと、--no-browser のときに
            配信URLを知る手立てが無くなる。
            """
            st = web.tunnel.status()
            stamp = time.strftime("%H:%M:%S")
            if st["state"] == "on":
                print(f"[{stamp}] 配信  参加者に配るURL: {web.public_url()}")
                print(f"[{stamp}] 配信  QRコードは操作画面に出る: {web.control_url()}")
            elif st["state"] == "error":
                print(f"[{stamp}] 配信  トンネルが失敗した:")
                print(st["error"])
            elif st["state"] == "off":
                print(f"[{stamp}] 配信  トンネルを止めた。閲覧URLは死んだ。")

        web.tunnel.on_change = announce
        try:
            web.start()
        except OSError as exc:
            print(f"ブラウザ字幕を開始できない"
                  f"（閲覧 {args.web} / 操作 {args.control_port}）: {exc}")
            print("使われていないポートを指定すること。例: --web 8090 --control-port 8091")
            return 1
        if args.tunnel:
            st = web.tunnel.start()
            if st["state"] == "error":
                # トンネルが張れなくても本体は続ける。
                # 画面共有とZoom字幕APIは使えるためである。
                print("トンネルを開始できない:")
                print(st["error"])
            else:
                print("トンネルを起こしている。URLが出るまで数秒かかる。")
        # ダブルクリックで起動したときに、ブラウザを自分で開かせない。
        # **開くのは操作画面である。** 閲覧画面はそこから開ける。
        if not args.no_browser:
            webbrowser.open(web.control_url())

    application = app_mod.App(settings, web=web)
    # **操作画面があるときは、停止した状態から始める。** 会議に入る前にアプリを
    # 立ち上げておけるようにするためである。準備中の雑談を認識に流さない。
    # 操作画面が無ければ開始する手段が無いので、すぐ始める。
    start_now = web is None
    try:
        asyncio.run(application.run(capture, start_now=start_now))
    except KeyboardInterrupt:
        print()
        print("終了する。")
    finally:
        capture.stop()
        if web is not None:
            if web.tunnel is not None:
                web.tunnel.stop()
            web.stop()
        application.report(capture)
    return 0


if __name__ == "__main__":
    sys.exit(main())
