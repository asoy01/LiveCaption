#!/bin/sh
# 字幕コンテナの入口。tailscaled を起こしてから本体を起動する。
#
# **Tailscale はこの箱の中にいる。** サイドカーではない。だから
# `tailscaled.sock` の共有も、CLI の配り直しも要らない。本体の `tunnel.py` が
# `shutil.which("tailscale")` で見つける CLI は、同じ箱の /usr/local/bin にある。
set -eu

STATE_DIR=/var/lib/tailscale
SOCK=/var/run/tailscale/tailscaled.sock
HOSTNAME_TS="${TS_HOSTNAME:-livecaption}"

mkdir -p "$STATE_DIR" /var/run/tailscale

# --- 音声 -------------------------------------------------------------------
# **会議ソフトの音を、ホストを通さずに拾う。** ホストの音声装置を使わないのが、
# ホストを選ばない作りの鍵である。口は2つ作る。
#
#   meeting … 会議ソフトの出力先。本体は meeting.monitor から録る
#   mic     … 会議ソフトに渡す「無音のマイク」。**これが無いと音声に入れない**
#
# `sounddevice` からは `pulse` として見える（`/etc/asound.conf` と
# `ALSA_PLUGIN_DIR`）。本体には `--device pulse` を渡すこと。
export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR"
# **残骸を消してから起こす。** 前回のソケットが残っていると立ち上がらない。
rm -rf "$XDG_RUNTIME_DIR/pulse"
pulseaudio -D --exit-idle-time=-1 --disable-shm --log-target=stderr \
           2>/var/log/pulseaudio.log

i=0
while ! pactl info >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 100 ]; then
    echo "音声:       PulseAudio が上がらない。/var/log/pulseaudio.log を見ること。"
    break
  fi
  sleep 0.1
done

if pactl info >/dev/null 2>&1; then
  # **口のレートを 48000 に揃える。** 既定の 44100 のままだと、会議ソフトの
  # 48 kHz が 44.1k に落とされ、本体が 48 kHz で開くときにまた上げられる。
  # リサンプルを2回通ると認識が目に見えて落ちる（段階3で実測した）。
  pactl load-module module-null-sink sink_name=meeting rate=48000 \
        channels=2 format=s16le \
        sink_properties=device.description=meeting >/dev/null
  pactl load-module module-null-sink sink_name=mic rate=48000 \
        channels=2 format=s16le \
        sink_properties=device.description=mic >/dev/null
  pactl set-default-sink meeting
  # **本体が録るのはここである。** 会議ソフトには PULSE_SOURCE=mic.monitor を
  # 別に渡す（会議ソフト側から見たマイクは無音でよい）。
  pactl set-default-source meeting.monitor
  echo "音声:       meeting / mic を作った。本体は meeting.monitor から録る"
fi

# **状態はボリュームに置く。** これが消えると、Tailnet Lock の署名からやり直しに
# なり、ホスト名も変わって、配ってあった閲覧URLが死ぬ。
tailscaled --state="$STATE_DIR/tailscaled.state" --socket="$SOCK" \
           --tun=tailscale0 >/var/log/tailscaled.log 2>&1 &

# ソケットが開くまで待つ。開かないまま `tailscale up` を撃つと必ず失敗する。
i=0
while [ ! -S "$SOCK" ]; do
  i=$((i + 1))
  if [ "$i" -gt 100 ]; then
    echo "tailscaled: ソケットが開かない。/var/log/tailscaled.log を見ること。"
    tail -20 /var/log/tailscaled.log || true
    break
  fi
  sleep 0.1
done

if tailscale --socket="$SOCK" status >/dev/null 2>&1; then
  echo "Tailscale:  ログイン済み（状態はボリュームに残っている）"
elif [ -n "${TS_AUTHKEY:-}" ]; then
  # **署名済みの鍵を使うこと。** 麻生の tailnet は Tailnet Lock が有効なので、
  # 署名の無い鍵で入ったノードは、登録はされても他のノードと話せない。
  echo "Tailscale:  auth key で参加する（hostname=$HOSTNAME_TS）"
  tailscale --socket="$SOCK" up \
            --authkey="$TS_AUTHKEY" \
            --hostname="$HOSTNAME_TS" \
            --accept-dns=false || echo "Tailscale:  参加に失敗した。鍵と署名を確認すること。"
else
  # 配布版の既定。**秘密を .env に置かずに済む。** ログにURLが出る。
  echo "Tailscale:  TS_AUTHKEY が無い。下のURLを開いて手で繋ぐこと。"
  tailscale --socket="$SOCK" up --hostname="$HOSTNAME_TS" --accept-dns=false &
fi

# アドレスが載るまで少し待つ。**待たなくても起動は止めない。**
# 本体は `--control-bind` のアドレスが取れなければ背景で取り直す。
i=0
while [ "$i" -lt 60 ]; do
  ADDR=$(tailscale --socket="$SOCK" ip -4 2>/dev/null || true)
  if [ -n "$ADDR" ]; then
    echo "Tailscale:  $ADDR  ($(tailscale --socket="$SOCK" status --json 2>/dev/null | sed -n 's/.*"DNSName": *"\([^"]*\)".*/\1/p' | head -1))"
    break
  fi
  i=$((i + 1))
  sleep 0.5
done
[ -z "${ADDR:-}" ] && echo "Tailscale:  アドレスがまだ無い。本体は背景で取り直す。"

# --- 用語対訳表 ---------------------------------------------------------------
# **表はボリュームに置く。** 操作画面からアップロード・ダウンロード・削除できる
# 利用者データであり、イメージに焼き込むと、足した表がコンテナの作り直しで消える。
#
# 空のときだけ、イメージに入っている表を種として置く。**上書きはしない。**
# 操作画面から直したものを、起動のたびに巻き戻すことになる。
GLOSS_DIR="${LIVECAPTION_GLOSSARY_DIR:-/app/local/glossary}"
mkdir -p "$GLOSS_DIR"
if [ -z "$(ls -A "$GLOSS_DIR" 2>/dev/null)" ] && [ -d /app/etc/glossary ]; then
  cp -n /app/etc/glossary/*.tsv "$GLOSS_DIR"/ 2>/dev/null || true
  echo "用語集:     $GLOSS_DIR に種を置いた（$(ls -1 "$GLOSS_DIR" | wc -l) 個）"
else
  echo "用語集:     $GLOSS_DIR（$(ls -1 "$GLOSS_DIR"/*.tsv 2>/dev/null | wc -l) 個）"
fi

# --- 画面 -------------------------------------------------------------------
# **会議ソフトを置くための画面である。** 人は見ない。中を見たいときは
# LIVECAPTION_VNC=1 で起こして、http://<ホスト>:6080/vnc.html を開く。
#
# 窓の管理役（openbox）が要る。**無いと窓を前面に出せず、`wmctrl` も
# 窓を見られない**（段階0で踏んだ）。`xdotool search` は WM 無しでも効く。
export DISPLAY="${DISPLAY:-:99}"
rm -f /tmp/.X99-lock
Xvfb "$DISPLAY" -screen 0 "${LIVECAPTION_SCREEN:-1600x1200x24}" \
     >/var/log/xvfb.log 2>&1 &
i=0
while ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 100 ]; then
    echo "画面:       Xvfb が上がらない。/var/log/xvfb.log を見ること。"
    break
  fi
  sleep 0.1
done
openbox >/var/log/openbox.log 2>&1 &
echo "画面:       $DISPLAY (${LIVECAPTION_SCREEN:-1600x1200x24})"

# **Zoom の設定。** 既にあれば触らない。サインイン後の設定を消さないためである。
#
# `speaker_volume` の既定は 0 である。つまみが左端のままで、PulseAudio 側が
# 100% でも Zoom は無音を書き出す。**255 が最大。これが段階0で見つけた鍵である。**
if [ ! -f "$HOME/.config/zoomus.conf" ]; then
  mkdir -p "$HOME/.config"
  cat > "$HOME/.config/zoomus.conf" <<'CONF'
[General]
enableShowPreviewWndToJoin=false
autoJoinAudio=true
enableAutoJoinAudio=true
muteVoipWhenJoin=true
enableStartVideoWhenJoin=false
speaker_volume=255
system.audio.type=default
CONF
  echo "Zoom:       設定を書いた（speaker_volume=255）"
fi

# **覗く口はここでは起こさない。本体が持つ。**
# 操作画面の「中の画面」から、会議中でも開け閉めできるようにするためである。
# ここで起こすと、入口のシェルが `exec` で本体になったあと親が回収しないので、
# 閉じるたびにゾンビが1つ残る。`LIVECAPTION_VNC=1` は本体が読み、
# 「起動した時点から開けておく」の意味になる。

exec pixi run --frozen python run.py "$@"
