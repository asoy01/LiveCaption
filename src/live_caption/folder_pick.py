"""記録の置き場を、OS のフォルダ選択の窓から選ぶ。

**別のプロセスで開く。** tkinter を字幕アプリの中で動かすと、窓の取り回しや
イベントループの衝突で本体ごと落とせる。**会議の最中にそれをやってはいけない。**
子プロセスなら、何が起きても親は生き残り、字幕は流れ続ける。

**窓は字幕PCの画面に出る。** 操作画面を tailnet 越しに別の機体から開いている人の
画面には出ない。**呼ぶ側で、127.0.0.1 から来た要求かどうかを確かめること**
（`web.py` の `/api/savedir` がやっている）。そうしないと、遠くの誰かが
字幕PCの画面に窓を開いたまま、会議が始まることになる。
"""

from __future__ import annotations

import os
import subprocess
import sys

# 子プロセスに渡す本体。**ASCII だけで書く。** 引数はコマンドラインから渡す。
# Windows のコマンドラインは UTF-16 なので日本語を運べるが、`-c` のソースは
# 端末のコードページで解釈されうる。日本語をここに書かない。
_SCRIPT = (
    "import sys, tkinter, tkinter.filedialog\n"
    "root = tkinter.Tk()\n"
    "root.withdraw()\n"
    # **最前面に出す。** 字幕アプリは窓を持たないので、出した窓が他の窓の
    # 後ろに隠れると、押しても反応しないように見える。
    "root.attributes('-topmost', True)\n"
    "p = tkinter.filedialog.askdirectory("
    "initialdir=sys.argv[1] or None, title=sys.argv[2], mustexist=False)\n"
    "root.destroy()\n"
    "sys.stdout.write(p or '')\n"
)

# 窓を開いたまま放っておかれたときに諦めるまで。会議が始まるまでに気づけばよい。
TIMEOUT_SEC = 300.0


def available() -> bool:
    """この機体でフォルダ選択の窓を出せるか。

    出せないなら、操作画面はパスの手入力だけを見せる。**押しても何も起きない
    ボタンを置かない。**
    """
    try:
        import tkinter  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    # 画面の無い環境（サービス、SSH越しの Linux）では窓を出せない。
    return os.name == "nt" or bool(os.environ.get("DISPLAY"))


def pick(initial: str = "", title: str = "Choose a folder") -> str:
    """フォルダ選択の窓を出す。選ばれたパスを返す。取り消しなら空文字。

    **失敗しても投げない。** 置き場を選び直せないことより、会議が止まるほうが
    困る。選べなければ空文字を返し、呼ぶ側は手入力に落とす。
    """
    if not available():
        return ""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        done = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(initial), str(title)],
            capture_output=True, timeout=TIMEOUT_SEC, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        print("  [置き場] フォルダ選択の窓が開いたままなので諦めた。")
        return ""
    except OSError as exc:
        print(f"  [置き場] フォルダ選択の窓を開けない: {exc}")
        return ""
    if done.returncode != 0:
        err = done.stderr.decode("utf-8", "replace").strip()
        print(f"  [置き場] フォルダ選択の窓が落ちた: {err[-200:]}")
        return ""
    return done.stdout.decode("utf-8", "replace").strip()
