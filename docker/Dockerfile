# The LiveCaption box. **It is self-contained.**
#
# What is inside: the caption engine (a pixi environment), Zoom for Linux, a
# virtual audio device made with PulseAudio, an X server with no screen
# (Xvfb plus openbox), Tailscale and VNC.
# **The host needs only Docker.** It uses no audio device and no display.
#
# The contents are the same pixi environment that runs on Windows (the
# linux-64 side of `pixi.lock`). **Do not install the dependencies some other
# way.** The combination was chosen by measuring in real meetings, so a
# different environment means measuring again.

FROM debian:13-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*

# Audio. **The sound is made and picked up inside the container only. It does
# not use the host's audio device.** That is the key to not depending on the
# host. The meeting software plays into a null sink named `meeting`, and the
# engine records from its monitor.
RUN apt-get update && apt-get install -y --no-install-recommends \
      pulseaudio pulseaudio-utils libasound2-plugins procps \
 && rm -rf /var/lib/apt/lists/*

# **Show PulseAudio to PortAudio.** In stage 0 this is how we confirmed that
# `sounddevice` sees it as `pulse`. The engine's `audio.py` needs no changes.
RUN printf 'pcm.!default { type pulse }\nctl.!default { type pulse }\n' \
      > /etc/asound.conf

# **The alsa-lib from pixi looks for plugins only under its own prefix.**
# The pulse plugin lives on the Debian side, so tell it where that is. Stage 0
# was measured with Debian's python3, so this is a new requirement here.
ENV ALSA_PLUGIN_DIR=/usr/lib/x86_64-linux-gnu/alsa-lib

# The pixi version is pinned. It matches the one on the host.
ENV PIXI_VERSION=v0.72.0 \
    PIXI_HOME=/opt/pixi \
    PATH=/opt/pixi/bin:$PATH
RUN curl -fsSL https://pixi.sh/install.sh | bash \
 && pixi --version

WORKDIR /app

# **Build the environment first.** Editing the source then does not rebuild
# this layer.
# Do not keep what pixi downloads. It almost doubles the image size.
# `LICENSE` is needed too. `pixi.toml` points at it with `license-file`, and
# parsing fails without it.
COPY pixi.toml pixi.lock LICENSE ./
RUN pixi install --locked \
 && rm -rf /root/.cache/rattler

# Tailscale. **It goes inside this box, not in a sidecar.**
# `tunnel.py` looks for the CLI with `shutil.which("tailscale")`, so putting it
# on PATH means the engine's code needs no changes. The version matches the
# one on the host.
#
# **userspace-networking cannot be used.** To serve the control page,
# `--control-bind` has to find a 100.x address on a real interface, and in
# userspace mode no tailscale0 is created. That is why `/dev/net/tun` and
# `NET_ADMIN` are needed.
ENV TAILSCALE_VERSION=1.102.3
RUN curl -fsSL -o /tmp/ts.tgz \
      "https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz" \
 && tar -xzf /tmp/ts.tgz -C /tmp \
 && mv "/tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscale"  /usr/local/bin/ \
 && mv "/tmp/tailscale_${TAILSCALE_VERSION}_amd64/tailscaled" /usr/local/sbin/ \
 && rm -rf /tmp/ts.tgz "/tmp/tailscale_${TAILSCALE_VERSION}_amd64" \
 && tailscale --version

# cloudflared, for the other delivery route (a TryCloudflare temporary
# tunnel). It needs no account and no tailnet settings, so it is the route
# for people who cannot use Funnel. `tunnel.py` finds it with
# `shutil.which("cloudflared")`. One static binary; the version is pinned.
ENV CLOUDFLARED_VERSION=2026.9.3
RUN curl -fsSL -o /usr/local/bin/cloudflared \
      "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-amd64" \
 && chmod 755 /usr/local/bin/cloudflared \
 && cloudflared --version

# The meeting software. **This is option A, the Zoom desktop client for
# Linux.** The web client is refused with "Automated bots aren't allowed to
# join this meeting." (measured eight times in stage 0, with the same result
# every time).
#
# The base image has no display, so the GUI dependencies are installed here.
# xdotool, wmctrl and imagemagick are needed to drive the windows and take
# pictures of what they show.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libglib2.0-0 libxcb-xtest0 libxcb-xinerama0 libxcb-image0 \
      libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
      libxcb-xfixes0 libxcb-xkb1 libxkbcommon-x11-0 libxcb-cursor0 \
      libgl1 libegl1 libxslt1.1 libdrm2 libgbm1 libnss3 libasound2t64 \
      libxcomposite1 libxdamage1 libxrandr2 libatk1.0-0 libatk-bridge2.0-0 \
      libatspi2.0-0 libcups2 libpango-1.0-0 libcairo2 libgtk-3-0 \
      fonts-noto-cjk xvfb openbox xdotool x11-utils wmctrl imagemagick \
      `# VNC. **Use x0vncserver from TigerVNC, not x11vnc.**` \
      `# The old cut-text that x11vnc uses can carry Latin-1 only, so Japanese` \
      `# on the clipboard turns into ??? (measured 2026-09-21). TigerVNC` \
      `# supports the extended clipboard, and UTF-8 goes through both ways.` \
      `# Both attach to an existing display, so they are used the same way.` \
      tigervnc-scraping-server novnc websockify \
      `# A right-click in openbox calls x-terminal-emulator, so xterm is` \
      `# needed. What actually runs is uxterm. xrdb is in x11-xserver-utils.` \
      xterm xclip x11-xserver-utils \
 && rm -rf /var/lib/apt/lists/*

# **Leave this as `latest`.** Zoom locks old clients out of meetings, so
# pinning a version means that one day you suddenly cannot join.
RUN curl -fsSL -o /tmp/zoom.deb https://zoom.us/client/latest/zoom_amd64.deb \
 && (dpkg -i /tmp/zoom.deb || (apt-get update && apt-get -y -f install)) \
 && rm -f /tmp/zoom.deb && rm -rf /var/lib/apt/lists/*

# **Keep the Zoom settings and sign-in on a volume.** If they are lost, you
# have to sign in again.
ENV HOME=/home/livecaption
RUN mkdir -p /home/livecaption/.config

COPY run.py ./
COPY src/ ./src/
COPY etc/ ./etc/
# **The recovery tools go in too.** `transcript_to_md.py` turns a record back
# into a readable form when the engine died before writing the `.md`.
# **The times you need it are mostly the times you cannot reach into the
# container.** If it is not in the image, you cannot use it.
COPY scripts/ ./scripts/
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# The default place for records. compose does not put a volume over this one;
# it uses `/records`.
RUN mkdir -p /app/local/transcripts

ENV TZ=Asia/Tokyo

# **Pin the display language.** The Zoom window title is how we tell whether a
# meeting is running (`zoom_join_linux.py`). A different locale means a
# different title, and the check fails. Qt also complains that the locale is
# not UTF-8.
ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# **Do not buffer the log.** With no window to look at, `docker compose logs`
# is the only clue, and Python buffers its output when it is not on a
# terminal. If startup hangs halfway, you would see nothing.
ENV PYTHONUNBUFFERED=1

# So that audio and display also work when you come in with `docker compose
# exec`. **What the entrypoint exports does not reach a shell that attaches
# later with exec.** Without this, paplay says "Connection refused" and zoom
# cannot reach the display and dies silently.
ENV XDG_RUNTIME_DIR=/tmp/xdg
ENV DISPLAY=:99

# The arguments are passed straight to run.py. The entrypoint starts
# tailscaled first, then passes them on.
#   docker compose run --rm engine --from-file /samples/excerpt75.wav --dry-run
ENTRYPOINT ["/entrypoint.sh"]
CMD ["--help"]
