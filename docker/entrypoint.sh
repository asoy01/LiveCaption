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

exec pixi run --frozen python run.py "$@"
