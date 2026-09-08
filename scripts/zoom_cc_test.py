#!/usr/bin/env python3
"""Zoom 字幕API (third-party closed caption) の疎通確認ツール。

一括測定:
    python scripts/zoom_cc_test.py --auto "<APIトークンURL>"

表示の挙動を目で確かめる:
    python scripts/zoom_cc_test.py --display "<APIトークンURL>"

seq を明示して始める（既定は local/seq_state.json の続き）:
    python scripts/zoom_cc_test.py --display --seq 5000 "<APIトークンURL>"

重要: seq はミーティングのセッション全体で単調増加していないといけない。
巻き戻すと Zoom はエラーを返さずに黙って捨てる。本番で字幕アプリが落ちて
再起動したとき、seq を 1 に戻すと字幕が無言で止まる。

対話:
    python scripts/zoom_cc_test.py "<APIトークンURL>"

トークンの取り方:
    1. ホストとしてミーティングを開始する
    2. ツールバーの「字幕」の横の「∧」をクリック
    3. 「手動字幕の設定」→「APIトークンをコピー」
    トークンはそのセッション限り。会議を開き直したら取り直す。

準備:
    2台目の端末（スマホでよい）で同じ会議に入り、「字幕を表示」をオンにしておく。
    測定中に字幕がどう見えるかを目で確認するため。

対話コマンド:
    <任意の文字列>       その文字列を字幕として送る
    /rate N [interval]  短い字幕を N 回、interval 秒間隔で連投する
    /long N             N 文字の字幕を 1 回送る
    /lang xx-XX         言語コードを変更する（既定 en-US）
    /seq                現在の seq を表示する
    /quit               終了
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT = 10.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# seq はミーティングのセッション全体で単調増加していないといけない。
# スクリプトを再実行しても続きから始められるよう、会議ごとに保存する。
STATE_PATH = PROJECT_ROOT / "local" / "seq_state.json"


class CaptionSender:
    def __init__(self, base_url: str, lang: str = "en-US", seq: int | None = None) -> None:
        self.base_url = base_url.rstrip("&")
        self.lang = lang
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.base_url).query)
        self.meeting_key = query.get("id", ["unknown"])[0]
        self.seq = seq if seq is not None else self._load_seq()

    def _state(self) -> dict:
        if STATE_PATH.exists():
            try:
                return json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _load_seq(self) -> int:
        """同じ会議で前に送った続きから始める。seq を巻き戻すと Zoom は黙って捨てる。"""
        return self._state().get(self.meeting_key, 0) + 1

    def _save_seq(self) -> None:
        state = self._state()
        state[self.meeting_key] = self.seq - 1
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def post(self, text: str):
        """字幕を1回送る。(status, body, 所要秒) を返す。status は接続失敗時 None。"""
        url = f"{self.base_url}&seq={self.seq}&lang={self.lang}"
        req = urllib.request.Request(
            url,
            data=text.encode("utf-8"),
            method="POST",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                status = res.status
                body = res.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            return None, str(e.reason), time.perf_counter() - t0
        dt = time.perf_counter() - t0
        if status == 200:
            # seq は成功したときだけ進める。リトライでは増やさない。
            self.seq += 1
            self._save_seq()
        return status, body, dt


def show(status, body, dt) -> None:
    label = "OK " if status == 200 else "NG "
    code = status if status is not None else "----"
    line = f"  {label} {code}  {dt * 1000:6.0f} ms"
    if body.strip():
        line += f"  {body.strip()[:120]}"
    print(line)


def burst(sender: CaptionSender, n: int, interval: float, prefix: str):
    """n 回連投して (成功数, 失敗数, 遅延のリスト, 実測の秒数) を返す。"""
    ok, ng, lat = 0, 0, []
    t0 = time.perf_counter()
    for i in range(n):
        status, body, dt = sender.post(f"{prefix} {i + 1}/{n}")
        lat.append(dt)
        if status == 200:
            ok += 1
        else:
            ng += 1
            if ng <= 2:
                show(status, body, dt)
        if interval:
            time.sleep(interval)
    return ok, ng, lat, time.perf_counter() - t0


def cmd_rate(sender: CaptionSender, args: list[str]) -> None:
    n = int(args[0]) if args else 20
    interval = float(args[1]) if len(args) > 1 else 0.0
    print(f"  {n} 回送信 (間隔 {interval} 秒)")
    ok, ng, lat, total = burst(sender, n, interval, "rate test")
    print(f"  成功 {ok} / 失敗 {ng} / 合計 {total:.2f} 秒 ({n / total:.1f} 回/秒)")
    print(f"  1回あたり  最小 {min(lat)*1000:.0f} ms"
          f"  平均 {sum(lat)/len(lat)*1000:.0f} ms  最大 {max(lat)*1000:.0f} ms")


def make_text(n: int) -> str:
    """n 文字の文字列を作る。10文字ごとに、その位置の文字数が数字で入る。

    画面に見えた最後の数字が、そのまま「何文字まで表示されたか」になる。
    末尾の5文字は [END] なので、最後まで出ていればそれが見える。
    """
    if n < 10:
        return "x" * n
    parts = []
    pos = 0
    while pos < n:
        pos += 10
        parts.append(f"{pos:04d}......")
    text = "".join(parts)[:n]
    return text[:-5] + "[END]" if n >= 15 else text


def cmd_long(sender: CaptionSender, args: list[str]) -> None:
    n = int(args[0]) if args else 1000
    print(f"  {n} 文字を送信")
    show(*sender.post(make_text(n)))


def auto(sender: CaptionSender) -> int:
    print("=" * 62)
    print("Zoom 字幕API 一括測定")
    print("=" * 62)
    print("2台目の端末で「字幕を表示」をオンにして、画面を見ながら実行すること。")
    print()

    # 1. 疎通
    print("1. 疎通確認")
    status, body, dt = sender.post("Hello. This is a caption API test.")
    show(status, body, dt)
    if status != 200:
        print("\n   疎通しない。以降の測定は行わない。")
        print("   Webポータルで「手動字幕」と「字幕APIトークンの使用を許可する」を確認すること。")
        return 1
    print("   => Zoomの画面に英文が出ているか目で確認すること。")
    print()

    # 2. 表示時間
    print("2. 表示時間の確認")
    print("   目印を1つ送る。Zoomの画面から消えるまでの秒数を数えること。")
    sender.post("=== WATCH THIS LINE. Count seconds until it disappears. ===")
    for i in range(10, 0, -1):
        print(f"\r   {i} 秒経過待ち...", end="", flush=True)
        time.sleep(1)
    print("\r   (待機終了)                ")
    print()

    # 3. レート
    print("3. 送信レートの上限")
    print("   間隔を変えて連投する。実測が下回るのは往復時間のため。")
    print(f"   {'間隔':>10}  {'成功':>4} {'失敗':>4}  {'実測':>10}  {'平均遅延':>9}")
    for label, n, interval in [
        ("1.0 秒", 10, 1.0),
        ("0.5 秒", 10, 0.5),
        ("0.2 秒", 15, 0.2),
        ("0.1 秒", 20, 0.1),
        ("なし", 20, 0.0),
    ]:
        ok, ng, lat, total = burst(sender, n, interval, f"rate {label}")
        print(f"   {label:>10}  {ok:>4} {ng:>4}  {n/total:>7.1f}回/秒  {sum(lat)/len(lat)*1000:>6.0f} ms")
        time.sleep(1.0)
    print("   => 失敗が出た行から上が、使える上限。")
    print()

    # 4. 文字数
    print("4. 1回に送れる文字数")
    for n in (100, 500, 1000, 2000, 4000, 8000):
        status, body, dt = sender.post(make_text(n))
        mark = "OK" if status == 200 else f"NG ({status})"
        note = f"  {body.strip()[:80]}" if status != 200 and body.strip() else ""
        print(f"   {n:>5} 文字  {mark}{note}")
        time.sleep(0.5)
    print("   => Zoomの画面で、末尾まで表示されているかも目で確認すること。")
    print("      POSTが通っても、表示が途中で切れている場合がある。")
    print()

    print("=" * 62)
    print("測定終了。結果と、画面で見えた様子をあわせて記録すること。")
    return 0


def interactive(sender: CaptionSender) -> int:
    print(f"送信先: {sender.base_url[:60]}...")
    print(f"言語:   {sender.lang}")
    print("文字列を入力すると字幕として送信する。/quit で終了。")
    print()
    while True:
        try:
            line = input("caption> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.startswith("/"):
            parts = line.split()
            cmd, args = parts[0], parts[1:]
            if cmd == "/quit":
                return 0
            elif cmd == "/seq":
                print(f"  seq = {sender.seq}")
            elif cmd == "/lang":
                if args:
                    sender.lang = args[0]
                print(f"  lang = {sender.lang}")
            elif cmd == "/rate":
                cmd_rate(sender, args)
            elif cmd == "/long":
                cmd_long(sender, args)
            else:
                print(f"  未知のコマンド: {cmd}")
            continue
        show(*sender.post(line))


def display_test(sender: CaptionSender) -> int:
    """字幕の表示の挙動を目で確かめるためのモード。時間をかけてゆっくり送る。"""
    print("=" * 62)
    print("Zoom 字幕 表示挙動の確認")
    print("=" * 62)
    print("2台目の端末の画面だけを見ること。この画面は見なくてよい。")
    print("所要 約2分。")
    print()

    input("準備ができたら Enter を押す > ")
    print()

    print("--- 試験0: seq を飛ばしても届くか ---")
    print("  2行送る。2行目は seq を 500 飛ばす。")
    sender.post("TEST 0a ... normal seq. You should see this line.")
    time.sleep(4)
    sender.seq += 500
    sender.post("TEST 0b ... seq jumped by 500. Do you see this line too?")
    time.sleep(4)
    print("  => 0b が見えたか。見えれば、seq は飛ばしてもよい（再起動からの復帰が楽になる）。")
    print("     見えなければ、seq は 1 ずつ増やすしかない。")
    print()

    print("--- 試験A: 字幕が消えるまでの時間 ---")
    print("  1行だけ送る。画面から消えるまでの秒数を数えること。")
    sender.post("TEST A ... count the seconds until this line disappears")
    for i in range(1, 31):
        print(f"  {i} 秒", end="  ", flush=True)
        if i % 10 == 0:
            print()
        time.sleep(1)
    print()
    print("  => 消えた秒数を記録すること。30秒たっても残っていればそう記録する。")
    print()

    print("--- 試験B: 新しい字幕は前の字幕をどうするか ---")
    print("  3行を3秒おきに送る。前の行が上に流れるか、消えて置き換わるかを見ること。")
    for i, line in enumerate(
        ["TEST B ... line ONE of three",
         "TEST B ... line TWO of three",
         "TEST B ... line THREE of three"], 1):
        sender.post(line)
        print(f"  {i} 行目を送った")
        time.sleep(3)
    time.sleep(2)
    print("  => 画面に何行見えているか。ONE はまだ見えるか。")
    print()

    print("--- 試験C: 長い文はどこまで表示されるか ---")
    print("  末尾に番号が入っている。どこまで見えたかを記録すること。")
    for n in (200, 500, 1000, 2000):
        sender.post(make_text(n))
        print(f"  {n} 文字を送った")
        time.sleep(8)
    print("  => 各回、画面に見えた最後の数字を記録すること。")
    print()

    print("=" * 62)
    print("記録すること:")
    print("  A. 字幕が消えるまでの秒数")
    print("  B. 前の行は流れるか、置き換わるか。同時に何行見えるか")
    print("  C. 何文字まで表示されたか")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    auto_mode = "--auto" in args
    disp_mode = "--display" in args
    for flag in ("--auto", "--display"):
        if flag in args:
            args.remove(flag)
    seq = None
    if "--seq" in args:
        i = args.index("--seq")
        seq = int(args[i + 1])
        del args[i:i + 2]
    if not args:
        print(__doc__)
        return 1
    sender = CaptionSender(args[0], seq=seq)
    print(f"seq は {sender.seq} から始める（{STATE_PATH.name} の続き）")
    if auto_mode:
        return auto(sender)
    if disp_mode:
        return display_test(sender)
    return interactive(sender)


if __name__ == "__main__":
    sys.exit(main())
