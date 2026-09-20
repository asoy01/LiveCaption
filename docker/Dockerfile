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

COPY run.py ./
COPY src/ ./src/
COPY etc/ ./etc/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 記録の既定の置き場。compose はここにボリュームを重ねず、`/records` を使う。
RUN mkdir -p /app/local/transcripts

ENV TZ=Asia/Tokyo

# 引数はそのまま run.py に渡る。入口で tailscaled を起こしてから渡す。
#   docker compose run --rm engine --from-file /samples/excerpt75.wav --dry-run
ENTRYPOINT ["/entrypoint.sh"]
CMD ["--help"]
