#!/bin/sh
# The entry point of the caption container. It starts tailscaled, then the
# engine.
#
# **Tailscale is inside this box.** It is not a sidecar. So there is no
# `tailscaled.sock` to share and no CLI to copy around. The CLI that the
# engine's `tunnel.py` finds with `shutil.which("tailscale")` is the one in
# /usr/local/bin of the same box.
set -eu

STATE_DIR=/var/lib/tailscale
SOCK=/var/run/tailscale/tailscaled.sock
HOSTNAME_TS="${TS_HOSTNAME:-livecaption}"

mkdir -p "$STATE_DIR" /var/run/tailscale

# --- Audio ------------------------------------------------------------------
# **Pick up the sound of the meeting software without going through the host.**
# Not using the host's audio device is what makes this work on any host.
# Two outputs (sinks) are created.
#
#   meeting … where the meeting software plays. The engine records from
#             meeting.monitor
#   mic     … a "silent microphone" handed to the meeting software.
#             **Without it you cannot join the audio.**
#
# `sounddevice` sees them as `pulse` (see `/etc/asound.conf` and
# `ALSA_PLUGIN_DIR`). Pass `--device pulse` to the engine.
export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR"
# **Clear the leftovers before starting it.** It will not come up if the
# socket from the last run is still there.
rm -rf "$XDG_RUNTIME_DIR/pulse"
pulseaudio -D --exit-idle-time=-1 --disable-shm --log-target=stderr \
           2>/var/log/pulseaudio.log

i=0
while ! pactl info >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 100 ]; then
    echo "Audio:      PulseAudio did not start. See /var/log/pulseaudio.log."
    break
  fi
  sleep 0.1
done

if pactl info >/dev/null 2>&1; then
  # **Set the sink rate to 48000.** At the default 44100, the 48 kHz from the
  # meeting software is dropped to 44.1k and then raised again when the engine
  # opens the device at 48 kHz. Two resampling steps make recognition
  # noticeably worse (measured in stage 3).
  pactl load-module module-null-sink sink_name=meeting rate=48000 \
        channels=2 format=s16le \
        sink_properties=device.description=meeting >/dev/null
  pactl load-module module-null-sink sink_name=mic rate=48000 \
        channels=2 format=s16le \
        sink_properties=device.description=mic >/dev/null
  pactl set-default-sink meeting
  # **This is where the engine records.** The meeting software gets
  # PULSE_SOURCE=mic.monitor separately (the microphone it sees may be
  # silent).
  pactl set-default-source meeting.monitor
  echo "Audio:      Created meeting / mic. The engine records from meeting.monitor."
fi

# **Keep the state on a volume.** If it is lost, you start again from the
# Tailnet Lock signing, the host name changes, and the viewer URLs you handed
# out stop working.
tailscaled --state="$STATE_DIR/tailscaled.state" --socket="$SOCK" \
           --tun=tailscale0 >/var/log/tailscaled.log 2>&1 &

# Wait for the socket to open. Running `tailscale up` before it opens always
# fails.
i=0
while [ ! -S "$SOCK" ]; do
  i=$((i + 1))
  if [ "$i" -gt 100 ]; then
    echo "tailscaled: The socket did not open. See /var/log/tailscaled.log."
    tail -20 /var/log/tailscaled.log || true
    break
  fi
  sleep 0.1
done

if tailscale --socket="$SOCK" status >/dev/null 2>&1; then
  echo "Tailscale:  Already logged in. The state is kept in the volume."
elif [ -n "${TS_AUTHKEY:-}" ]; then
  # **Use a signed key** if your tailnet has Tailnet Lock enabled. A node that
  # joins with an unsigned key is registered, but it cannot talk to the other
  # nodes.
  echo "Tailscale:  Joining with an auth key (hostname=$HOSTNAME_TS)."
  tailscale --socket="$SOCK" up \
            --authkey="$TS_AUTHKEY" \
            --hostname="$HOSTNAME_TS" \
            --accept-dns=false || echo "Tailscale:  Could not join. Check the key and its signature."
else
  # The default for the distributed version. **No secret has to go in .env.**
  # A URL appears in the log.
  echo "Tailscale:  TS_AUTHKEY is not set. Open the URL below to connect by hand."
  tailscale --socket="$SOCK" up --hostname="$HOSTNAME_TS" --accept-dns=false &
fi

# Wait a little for the address to appear. **Do not hold up startup for it.**
# If the engine cannot get the address for `--control-bind`, it retries in the
# background.
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
[ -z "${ADDR:-}" ] && echo "Tailscale:  No address yet. LiveCaption keeps trying in the background."

# --- Glossary ----------------------------------------------------------------
# **Keep the tables on a volume.** They are user data that can be uploaded,
# downloaded and deleted from the control page, so baking them into the image
# means a table you added is lost when the container is rebuilt.
#
# Seed it with the tables in the image only when it is empty. **Never
# overwrite.** That would roll back, at every start, what was edited from the
# control page.
GLOSS_DIR="${LIVECAPTION_GLOSSARY_DIR:-/app/local/glossary}"
mkdir -p "$GLOSS_DIR"
if [ -z "$(ls -A "$GLOSS_DIR" 2>/dev/null)" ] && [ -d /app/etc/glossary ]; then
  cp -n /app/etc/glossary/*.tsv "$GLOSS_DIR"/ 2>/dev/null || true
  echo "Glossary:   Seeded $GLOSS_DIR ($(ls -1 "$GLOSS_DIR" | wc -l) files)"
else
  echo "Glossary:   $GLOSS_DIR ($(ls -1 "$GLOSS_DIR"/*.tsv 2>/dev/null | wc -l) files)"
fi

# --- Display ----------------------------------------------------------------
# **This display exists to hold the meeting software.** Nobody looks at it. To
# look inside, start it with LIVECAPTION_VNC=1 and open
# http://<host>:6080/vnc.html.
#
# A window manager (openbox) is needed. **Without one you cannot bring a
# window to the front, and `wmctrl` cannot see the windows either** (hit in
# stage 0). `xdotool search` works even with no WM.
export DISPLAY="${DISPLAY:-:99}"
rm -f /tmp/.X99-lock
Xvfb "$DISPLAY" -screen 0 "${LIVECAPTION_SCREEN:-1600x1200x24}" \
     >/var/log/xvfb.log 2>&1 &
i=0
while ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 100 ]; then
    echo "Display:    Xvfb did not start. See /var/log/xvfb.log."
    break
  fi
  sleep 0.1
done
openbox >/var/log/openbox.log 2>&1 &

# **The xterm defaults are too small to read.** This is the terminal you use
# when you work inside from VNC, so make it a readable size with a font that
# shows Japanese.
#
# **Write the `UXTerm` lines too.** A right-click in openbox starts
# `x-terminal-emulator`, and what that really is is `uxterm` (the UTF-8
# version). Its class name is `UXTerm`, not `XTerm`, so only one of the two
# does not match.
#
# **Overwrite it every time.** This setting belongs to us. Change it in the
# Dockerfile instead.
# **Do not name a CJK monospace font directly.** ASCII would then also be
# drawn at full width: the text is stretched, and the window runs off the
# screen (measured at 1874px, where the screen is 1600px).
# With `monospace`, fontconfig picks a half-width monospace font and falls
# back to Noto CJK for Japanese only.
cat > "$HOME/.Xresources" <<'XRES'
*faceName: monospace
*faceSize: 12
*background: #101010
*foreground: #e8e8e8
*saveLines: 5000
*selectToClipboard: true
XTerm*VT100.geometry: 110x32
UXTerm*VT100.geometry: 110x32
XRES
xrdb -merge "$HOME/.Xresources" 2>/dev/null || true

echo "Display:    $DISPLAY (${LIVECAPTION_SCREEN:-1600x1200x24})"

# **The Zoom settings.** If they already exist, leave them alone, so that the
# settings made after signing in are not lost.
#
# `speaker_volume` defaults to 0. The slider stays at the far left, and Zoom
# writes out silence even when PulseAudio is at 100%. **255 is the maximum.
# This is the key we found in stage 0.**
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
  echo "Zoom:       Wrote the settings (speaker_volume=255)."
fi

# **VNC is not started here. The engine owns it.**
# That is so you can open and close it from "the screen inside" on the control
# page, even during a meeting.
# If it were started here, the entrypoint shell becomes the engine with
# `exec`, so no parent reaps the child, and one zombie is left behind every
# time it is closed. `LIVECAPTION_VNC=1` is read by the engine and means
# "keep it open from the moment it starts".

exec pixi run --frozen python run.py "$@"
