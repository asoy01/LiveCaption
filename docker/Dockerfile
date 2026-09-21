# LiveCaption のエンジン。段階1。
#
# ここには会議ソフトが入っていない。音は WAV で代用する（`--from-file`）。
# Zoom・Xvfb・PulseAudio が足されるのは段階3である。
#
# 中身は Windows で動いているものと同じ pixi 環境である（`pixi.lock` の
# linux-64 の側）。**別の作り方で依存を入れ直さない。** 実会議で測って決めた
# 組み合わせなので、環境が違えば測り直しになる。

FROM debian:13-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*

# 音声。**コンテナの中だけで音を作って拾う。ホストの音声装置は使わない。**
# これがホスト非依存の鍵である。`meeting` という null sink に会議ソフトが
# 音を出し、本体はその monitor から録る。
RUN apt-get update && apt-get install -y --no-install-recommends \
      pulseaudio pulseaudio-utils libasound2-plugins procps \
 && rm -rf /var/lib/apt/lists/*

# **PortAudio に PulseAudio を見せる。** 段階0では、これで `sounddevice` から
# `pulse` として見えることを確かめた。本体の `audio.py` は書き直さずに済む。
RUN printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' \
      > /etc/asound.conf

# **pixi の alsa-lib は、自分の prefix の下しかプラグインを探さない。**
# pulse プラグインは Debian 側にあるので、場所を教える。段階0は Debian の
# python3 で測ったので、ここは新しく要るものである。
ENV ALSA_PLUGIN_DIR=/usr/lib/x86_64-linux-gnu/alsa-lib

# pixi は版を固定する。osmium に入っているものと同じ。
ENV PIXI_VERSION=v0.72.0 \
    PIXI_HOME=/opt/pixi \
    PATH=/opt/pixi/bin:$PATH
RUN curl -fsSL https://pixi.sh/install.sh | bash \
 && pixi --version

WORKDIR /app

# **環境を先に作る。** ソースを直しても、この層は焼き直さずに済む。
# pixi の取得物は残さない。イメージが倍近くなる。
# `LICENSE` も要る。`pixi.toml` の `license-file` が指しており、無いと解析で落ちる。
COPY pixi.toml pixi.lock LICENSE ./
RUN pixi install --locked \
 && rm -rf /root/.cache/rattler

# Tailscale。**サイドカーにはせず、この箱の中に入れる。**
# `tunnel.py` は `shutil.which("tailscale")` で CLI を探すので、PATH に置けば
# 本体のコードは変えずに済む。版は osmium のホスト側と揃えてある。
#
# **userspace-networking は使えない。** 操作画面を出すには `--control-bind` が
# 100.x のアドレスを実際のインターフェースに見つける必要があり、userspace では
# tailscale0 が作られない。だから `/dev/net/tun` と `NET_ADMIN` が要る。
ENV TAILSCALE_VERSION=1.102.3
RUN curl -fsSL -o /tmp/ts.tgz \
      "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz" \
 && tar -xzf /tmp/ts.tgz -C /tmp \
 && mv "/tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscale"  /usr/local/bin/ \
 && mv "/tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscaled" /usr/local/sbin/ \
 && rm -rf /tmp/ts.tgz "/tmp/tailscale_${TAILSCALE_VERSION}_amd64" \
 && tailscale --version

# 会議ソフト。**A案（Zoom の Linux デスクトップ版）である。**
# Web版は「Automated bots aren't allowed to join this meeting.」で弾かれる
# （段階0で8回測って同じ結果）。HANDOFF_ARCHIVE ではなく HANDOFF の
# 「段階0の実測」を読むこと。
#
# 画面を持たない土台なので、GUI の依存は自分で入れる。
# xdotool / wmctrl / imagemagick は、窓を操作して様子を撮るために要る。
RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 libxcb-xtest0 libxcb-xinerama0 libxcb-image0 \
      libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
      libxcb-xfixes0 libxcb-xkb1 libxkbcommon-x11-0 libxcb-cursor0 \
      libgl1 libegl1 libxslt1.1 libdrm2 libgbm1 libnss3 libasound2t64 \
      libxcomposite1 libxdamage1 libxrandr2 libatk1.0-0 libatk-bridge2.0-0 \
      libatspi2.0-0 libcups2 libpango-1.0-0 libcairo2 libgtk-3-0 \
      fonts-noto-cjk xvfb openbox xdotool x11-utils wmctrl imagemagick \
      x11vnc novnc websockify \
 && rm -rf /var/lib/apt/lists/*

# **`latest` のままにする。** Zoom は古いクライアントを会議から締め出すので、
# 版を固定すると、ある日とつぜん会議に入れなくなる。
RUN curl -fsSL -o /tmp/zoom.deb https://zoom.us/client/latest/zoom_amd64.deb \
 && (dpkg -i /tmp/zoom.deb || (apt-get update && apt-get -y -f install)) \
 && rm -f /tmp/zoom.deb && rm -rf /var/lib/apt/lists/*

# **Zoom の設定とサインインはボリュームに残す。** ここが消えると、
# サインインのやり直しになる。
ENV HOME=/home/livecaption
RUN mkdir -p /home/livecaption/.config

COPY run.py ./
COPY src/ ./src/
COPY etc/ ./etc/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 記録の既定の置き場。compose はここにボリュームを重ねず、`/records` を使う。
RUN mkdir -p /app/local/transcripts

ENV TZ=Asia/Tokyo

# **表示言語を固定する。** Zoom の窓の名前で会議中かどうかを見ている
# （`zoom_join_linux.py`）。locale が変わると名前が変わり、判定が外れる。
# Qt も「UTF-8 でない」と文句を言う。
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# **ログを溜めない。** 窓の無い運用では `docker compose logs` だけが手がかりで、
# Python は端末でないと出力を溜め込む。起動の途中で固まったときに何も見えない。
ENV PYTHONUNBUFFERED=1

# `docker compose exec` で入ったときにも音声と画面を触れるようにする。
# **入口が export しても、あとから exec で入った殻には渡らない。**
# これが無いと paplay は「Connection refused」、zoom は画面に繋げず黙って落ちる。
ENV XDG_RUNTIME_DIR=/tmp/xdg
ENV DISPLAY=:99

# 引数はそのまま run.py に渡る。入口で tailscaled を起こしてから渡す。
#   docker compose run --rm engine --from-file /samples/excerpt75.wav --dry-run
ENTRYPOINT ["/entrypoint.sh"]
CMD ["--help"]
