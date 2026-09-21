# LiveCaption Manual

日本語版は [manual.ja.md](manual.ja.md) にあります。

LiveCaption shows real-time captions for a meeting. At the moment it translates
between Japanese and English.

You choose the direction.

- **English captions for a meeting held in Japanese**, for people who do not
  speak Japanese
- **Japanese captions for a meeting held in English**, for people who find it
  hard to follow spoken English

LiveCaption can send captions to Zoom, but it is not limited to Zoom. It reads
the audio the meeting software plays, so it works with any meeting software.

**LiveCaption joins the meeting as a silent participant.** It never speaks. It
only makes captions from the audio it hears. The reason for this design is in
section 12, "How it works".

**There are three ways to run it.**

| Setup | What you need | When to use it |
|---|---|---|
| **Windows + Docker** (recommended) | One Windows PC and Docker Desktop | **One laptop is enough.** Start it for each meeting |
| **Linux + Docker** | One Linux machine and Docker | Leave it on an always-on machine and let it join scheduled meetings on its own |
| **Windows native** | One Windows PC and VB-CABLE | No Docker. **This way is no longer updated** |

In both Docker setups, **the meeting software and the audio devices are
inside the container.** Docker is the only thing you install on that machine,
and **you can still join the same meeting yourself.** The steps are in
section 2, "Install (Windows + Docker)". Section 3, "Install (Linux +
Docker)", covers what differs on Linux.

Windows native is in section 4, "Install (Windows native)". **That way is
frozen.** All existing features remain, but no new features go into it.

**Running a meeting is the same in all three.** The control page, the schedule,
the glossary and the meeting record do not differ. From section 5 on, this
manual names every place where the three ways differ.

**You can schedule meetings and let LiveCaption run with nobody watching.** At
the set time it joins Zoom, starts delivering, and stops when the meeting is
over. See section 6, "Scheduled meetings".

---

## 1. What you need

**All three ways:**

| Item | Notes |
|---|---|
| An OpenAI API key | Speech recognition and translation both call the OpenAI API |
| An account in your meeting software, such as Zoom | A free account is enough |
| Host or co-host rights | Only if you want to use the Zoom caption API. Sharing the viewer page, and giving people its URL, need no host rights |

**To run it in Docker:**

| Item | Notes |
|---|---|
| One machine | Docker Desktop (WSL2) on Windows, or Docker Engine on Linux. **The machine needs no sound card and no screen** |
| Docker and Docker Compose | Compose v2 or later |
| Git | Used to fetch the repository |
| A Tailscale account | Used to reach the control page, and to give the viewer URL to people outside your tailnet. The free tier is enough |

**You need an Intel or AMD machine.** The image is amd64 only, so it does not
run on an Apple Silicon Mac. Zoom publishes no arm64 build for Linux.

**To run it without Docker (Windows native):**

| Item | Notes |
|---|---|
| A Windows PC | Used only for captions |
| VB-CABLE | A free virtual audio cable. [vb-audio.com/Cable/](https://vb-audio.com/Cable/) |
| pixi | Builds the Python environment. [pixi.sh](https://pixi.sh) |
| Git | Used to fetch the repository |

Speech recognition costs **$0.017 per minute**, so about one US dollar per hour.
Translation costs much less.

Silence is billed too, so press Stop for a long break. **Stop also stops
delivery and the Zoom captions** (see step 4 in section 5), and you have to
start those again afterwards. For a break of a few minutes, leave it running.

---

## 2. Install (Windows + Docker)

**This section is the main install.** To run it on Linux, read this section
first, then read section 3, "Install (Linux + Docker)", for the three steps that
differ.

You install it once. **In sections 2 and 3, the machine that runs Docker is
called the host.** From section 5 on, the host means the host of the Zoom
meeting, because the button on the control page is called "Show the host URL".

Docker is the only thing you install on the host. **The meeting software, the
audio devices, the screen and Tailscale are all inside the container.** The
host's own sound is never used, so playing music on the host, or changing the
host's volume, does not affect the captions.

**One laptop is enough.** You join the meeting as usual and speak, while the
Zoom inside the container joins the same meeting as a silent participant. The
screens and the audio devices are separate, so they do not interfere with each
other. **You do not need VB-CABLE.**

| | The host (you) | The container (captions) |
|---|---|---|
| Meeting software | A normal participant, with microphone and camera | A silent participant |
| Screen | Your usual screen | Inside the container (fixed at 1600x1200) |
| Audio | Your speakers and microphone | A virtual audio device inside the container |

To install without Docker, see section 4, "Install (Windows native)". **That
way is frozen.**

### 2.0 Install Docker Desktop

Get Docker Desktop from
[docker.com](https://www.docker.com/products/docker-desktop/). **Docker Desktop
needs WSL2**, which the installer turns on for you.

Start Docker Desktop, wait for the engine to come up, and check that it answers.

```powershell
docker version
```

**A machine with 16 GB of memory may not be enough.** The container itself uses
about 220 MB, but WSL2 reserves memory on top of that. Set a memory limit in
`%USERPROFILE%\.wslconfig` if the machine runs out.

### 2.1 Fetch the repository

```sh
git clone https://github.com/asoy01/LiveCaption.git
cd LiveCaption
```

Run every command below inside this folder.

### 2.2 Write the API key into `.env`

Copy the sample and put your OpenAI key in it.

```sh
cp .env.example .env
```

```
OPENAI_API_KEY=sk-...
```

Create the key at [platform.openai.com](https://platform.openai.com).

`.env` is not in Git (`.gitignore` lists it). **It is also kept out of the
Docker build context** (`.dockerignore`), so the key cannot be stored in an
image layer.

`.env.example` describes the other settings as well. **You do not need any of
them yet.**

### 2.3 Prepare Tailscale

**People in the meeting reach the viewer page through Tailscale.** The
container becomes a node on your tailnet, and Tailscale Funnel puts the viewer
page on the public internet while you deliver.

The control page stays inside the tailnet. **The control page has no
authentication.** Only the address the request comes from protects it, so the
control page never leaves the tailnet.

There are two ways to join. **Try (a) first.** With (a) you put no secret into
`.env`.

#### (a) Sign in by hand

Add nothing to `.env`. Leave the `TS_AUTHKEY` line commented out.

Start the container as it is (section 2.5 describes the normal start).

```sh
docker compose up -d
docker compose logs -f
```

The log prints a URL to open. The messages around it are in Japanese.

```
Tailscale:  TS_AUTHKEY が無い。下のURLを開いて…     no auth key: open the URL below

To authenticate, visit:

	https://login.tailscale.com/a/110e718b012a76

Tailscale:  アドレスがまだ無い。本体は背景で…      no address yet: retrying in the background
```

Open that URL in a browser and approve it with your Tailscale account.

**LiveCaption starts without waiting for you to approve.** That is what the last
line above means. Once you approve, it gets the address in the background and
prints the control page URL. **You do not have to restart it if you cannot
approve right away.**

**You only do this once.** The state is kept in the `tailscale` volume. You are
asked again only after `docker compose down -v`, which deletes the volume.

The node you approve gets a name. The default is `livecaption`. **If you run more
than one machine, change the name in `.env` before you start.**

```
TS_HOSTNAME=livecaption-laptop
```

Two machines cannot share a name. The second one is renamed to `livecaption-1`,
and **the viewer URL you gave people stops working.**

**With (a) the node gets no tag**, so two things need your attention in the admin
console.

- **Turn on Disable key expiry.** Otherwise the node leaves the tailnet after the
  default 180 days, and **the viewer URL stops working without warning**
- **Check that Funnel is allowed.** The default ACL gives `funnel` to
  `autogroup:member`, but a tailnet with a rewritten ACL may not give it

**On a tailnet with Tailnet Lock, (a) also needs a signature.** The container
holds no signing key, so it is registered but cannot talk to any other node. See
the end of this section.

#### (b) Use an auth key

Choose (b) if you want the container to start again with nobody watching. Create
the key in the Tailscale admin console (Settings → Keys → Generate auth key).

| Field | Value | Why |
|---|---|---|
| Reusable | **on** | You need it again whenever you recreate the volume |
| Ephemeral | **off** | An ephemeral node changes its name every time, and **the viewer URL you gave people in advance stops working** |
| Tags | `tag:livecaption` | Key expiry does not apply to a tagged device |

**Add the tag to your ACL first.** Funnel also needs a node attribute.

```json
"tagOwners": { "tag:livecaption": ["autogroup:admin"] },
"nodeAttrs": [ { "target": ["tag:livecaption"], "attr": ["funnel"] } ]
```

**Do not forget the `nodeAttrs` line.** A tagged device leaves
`autogroup:member`, and without that line it loses Funnel.

**If you do not tag the node, turn on Disable key expiry in the admin console.**
Otherwise the node leaves the tailnet after the default 180 days, and **the
viewer URL stops working without warning.**

Write the key into `.env`.

```
TS_AUTHKEY=tskey-auth-xxxxxxxx
```

#### If your tailnet uses Tailnet Lock

**Both (a) and (b) need a signature.** The container is a new node and holds no
signing key. An unsigned node is registered but **cannot talk to any other node.**

Check on a machine that holds a trusted signing key.

```sh
tailscale lock status
```

If it says `Tailnet Lock is NOT enabled.`, skip the rest of this section.

**With (b), sign the key.** Run this on a machine that holds a trusted signing
key, and put the signed key in `.env` as `TS_AUTHKEY`.

```sh
sudo tailscale lock sign tskey-auth-xxxxxxxx
```

**A node registered with a signed key is signed the moment it registers.** The
key is reusable, so **one key sets up as many signed nodes as you need.**

**With (a), sign the node key.** After you approve the login, get the node key
from inside the container.

```sh
docker compose exec engine tailscale lock status
```

Then sign it on the machine that holds the signing key.

```sh
sudo tailscale lock sign nodekey:xxxxxxxx
```

**You sign only once.** The state is kept in the `tailscale` volume.

**Keep a signed auth key secret.** The key itself is registered as a trusted
signing key on your tailnet, so **anyone who has it can add as many trusted nodes
as they like.** Tailnet Lock then gives no protection. **This is one
more reason to prefer (a).**

#### Limit which devices reach the control page

**Use your ACL to limit which devices reach the control page.** Without that
limit, anyone you invited to your tailnet can open the control page. Anyone who
opens it can quit the app, start delivering, and **read the meeting
record**.

### 2.4 Settings that differ per machine

**`TS_HOSTNAME` is the first one**, and 2.3 covers it: change it when you run
more than one machine.

**Turn off resident mode if you start LiveCaption only for a meeting.**
Otherwise LiveCaption runs every time Docker Desktop starts, and joins Zoom if
you have entered a schedule.

```
LIVECAPTION_RESTART=no
```

You then start it with `docker compose up -d` before a meeting and stop it with
`docker compose down` afterwards. **The button on the control page changes its
wording as well** (6.6). On an always-on machine you need neither line.

### 2.5 Start it

```sh
docker compose up -d --build
```

The first build takes about ten minutes. **After that it takes seconds.**

The log tells you whether it started.

```sh
docker compose logs -f
```

The start-up messages are in Japanese. The lines to look for are these.

```
音声:       meeting / mic を作った…            the audio devices are up
Tailscale:  100.x.x.x  (livecaption.<tailnet>.ts.net)
用語集:     /app/local/glossary（3 個）        3 glossary tables found
画面:       :99 (1600x1200x24)                 the screen is up
Zoom:       設定を書いた（speaker_volume=255） Zoom is configured
操作        https でも開ける: https://livecaption.<tailnet>.ts.net:8443
操作画面:   http://localhost:8081              the control page
            http://livecaption.<tailnet>.ts.net:8081
```

**That `https://...:8443` is the control page you use every day.** Open it from
any machine on your tailnet. Tailscale gets the certificate and renews it.

**Open it by the full name.** The certificate is issued for
`<machine>.<tailnet>.ts.net`, so a short name or an IP address does not match.

If you chose (a) in 2.3, the log prints the sign-in URL here. Open it and
approve, and the address appears. **The app keeps retrying in the background,
so you do not have to start it again.**

**The container starts again by itself after a host reboot**
(`restart: unless-stopped`). An always-on machine is then ready for
a scheduled meeting.

### 2.6 Sign in to Zoom

**The Zoom client inside the container starts signed out.** Sign in once by
hand so that it can join meetings on its own. **Use VNC.**

On the control page, open the **Setup** tab → "VNC" → **Start**. Open the URL it
shows and you see the screen inside the container.

```
https://livecaption.<tailnet>.ts.net:6443/vnc.html
```

Sign in to Zoom, then press **Stop**. **The sign-in is kept in a volume, so you
do not have to do it again.**

**VNC has no password.** Only the tailnet boundary protects it. **Start it when
you need it and stop it afterwards.** You can start and stop it during a
meeting.

**You can open a terminal inside the container too.** Right-click on the empty
desktop → `Terminal emulator`.

### 2.7 Add your glossary tables

**This repository contains no tables.** `etc/glossary/` is empty. Terms differ by
field, so make your own. The format and how to build them are in section 7,
"The glossary".

There is a sample in `docs/glossary-example.tsv`.

Under Docker you **upload tables from the control page** (Setup tab →
"Glossary"). **They are stored in a volume, so recreating the container does not
lose them.** You download and delete them in the same place.

### 2.8 Try it without a meeting

You can test the whole path with a recording.

```sh
docker compose run --rm -v "$PWD/recordings:/samples:ro" engine \
  --from-file /samples/test.wav --dry-run
```

With `--dry-run` nothing is sent to Zoom. You see the recognised text and the
translation only. Use a 24000 Hz mono WAV file.

---

## 3. Install (Linux + Docker)

**Use this on an always-on Linux machine.** It suits scheduled meetings that
run with nobody watching, because a machine that sleeps cannot act at the
scheduled time.

**The steps are the same as in section 2, apart from the three below.** Read
section 2, and apply these three as you go.

### 3.1 Install Docker (replaces 2.0)

Install Docker Engine and Compose instead of Docker Desktop. The steps are at
[docs.docker.com](https://docs.docker.com/engine/install/). **Compose must be v2
or later.**

Add yourself to the `docker` group so that you do not need `sudo`.

```sh
sudo usermod -aG docker $USER
```

**Log out and back in afterwards**, or the group change does not take effect.

### 3.2 If the machine runs ConnMan (not in section 2)

**Check this before you start the container.** ConnMan is used on some Debian
setups and on embedded machines. Skip this section if your machine does not run
it.

```sh
systemctl is-active connman
```

ConnMan manages every interface it finds, but **its default blacklist
does not include `docker`, `veth`, `br-` or `tailscale`.** So it also manages the
virtual interfaces Docker creates, fails DHCP on them, assigns a link-local
address, and **points the host's default route at them.** The host's outbound
traffic then goes to the container and is lost.

**The symptom is hard to recognise.** DHCP has to time out first, so the network
breaks **tens of seconds after** the container starts. In Zoom it looks like "it
joins, then loses audio and disconnects 30 to 40 seconds later".

Put this in `/etc/connman/main.conf`:

```
NetworkInterfaceBlacklist = vmnet,vboxnet,virbr,ifb,ve-,vb-,docker,veth,br-,tailscale
```

```sh
sudo systemctl restart connman
sudo systemctl restart tailscaled
```

**Include `tailscale` as well.** If you exclude only Docker, ConnMan manages
`tailscale0` next and shuts it down. **`tailscale status` still says "Online:
True" then**, because the connection to the control server runs over the wired
interface. Only the data path is broken, so the status does not show the
problem. Check the interface instead:

```sh
ip addr show tailscale0
```

### 3.3 Settings that differ per machine (replaces 2.4)

**Let it run resident.** Do not set `LIVECAPTION_RESTART`. With the default
`unless-stopped`, the container starts again by itself after a host reboot,
which is what a scheduled meeting needs.

`TS_HOSTNAME` works as in section 2: change it only when you run more than one
machine.

**Every other step (2.1 to 2.3, and 2.5 to 2.8) is the same.**

---

## 4. Install (Windows native)

**This way is frozen.** It still works and keeps all its features, but no new
features go into it. For a new installation, use section 2, "Install (Windows +
Docker)".

Do this once on the caption PC.

### 4.1 Install VB-CABLE

Download it from [vb-audio.com/Cable/](https://vb-audio.com/Cable/).
**Run the installer as administrator, then restart the PC.**

### 4.2 Set both CABLE devices to 48000 Hz

After the restart, open the Windows sound settings and check these two devices.

| Device | Format |
|---|---|
| `CABLE Input` (playback) | **48000 Hz**, 16 bit |
| `CABLE Output` (recording) | **48000 Hz**, 16 bit |

**Set both to 48000 Hz.** If the two rates differ, Windows resamples the audio
and the sound is distorted. LiveCaption opens the device at 48000 Hz and converts
the audio to 24000 Hz itself.

### 4.3 Install pixi

pixi builds the Python environment. **You do not have to install Python
separately.** pixi installs the version this project needs.

Open PowerShell and run one of these.

```powershell
winget install prefix-dev.pixi
```

```powershell
powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
```

**Open a new PowerShell window afterwards.** An older window does not see the
new PATH. Then check that pixi is installed.

```powershell
pixi --version
```

Git is installed the same way. Skip this if you already have it.

```powershell
winget install Git.Git
```

### 4.4 Fetch the repository

Put it anywhere you like. A path with no spaces and no non-ASCII characters is
the safe choice.

```powershell
cd C:\src
git clone https://github.com/asoy01/LiveCaption.git
cd LiveCaption
```

You can also download a ZIP from the GitHub page and unpack it, but then you
cannot update with `git pull`.

### 4.5 Build the Python environment

Run this inside the folder you cloned.

```powershell
pixi install
```

The packages go into a `.pixi` folder there. **pixi does not change your system
Python.** The first run takes a few minutes.

### 4.6 Write the API key into `.env`

**The file goes in the top folder of the repository**, in the same folder as
`pixi.toml` and `StartLiveCaption.bat`. Create a file called `.env` there.

```
LiveCaption\
  ├ .env.example          the sample, included in the repository
  ├ .env                  the file you create
  ├ pixi.toml
  ├ StartLiveCaption.bat
  ├ src\
  └ ...
```

The quickest way is to copy the sample. Run these two commands inside the folder
you cloned in 4.4.

```powershell
copy .env.example .env
notepad .env
```

Write your key in the file and save it.

```
OPENAI_API_KEY=sk-...
```

Create the key at [platform.openai.com](https://platform.openai.com).

**The name is `.env`, not `.env.txt`.** Notepad can add `.txt` when you use
"Save as" to make a new file. The `copy` command above avoids that.

`.env` is not committed to Git. It is listed in `.gitignore`, so you do not
publish your key by accident.

**The value in `.env` is used instead of the environment variable of the same
name.** If you set the key in both places, LiveCaption reads `.env`.

### 4.7 Check that the audio path works

This test needs no meeting and no API key. It plays a sine wave into
`CABLE Input` and reads it back from `CABLE Output`.

```powershell
pixi run python scripts/cable_loopback.py
```

If the test reads the signal back, VB-CABLE is working.

```
--- 本体が既定で選ぶもの: 2 'CABLE Output (VB-Audio Virtual ' [MME] ---
  区間 39、音あり 39（100%）  最大 peak 0.299  取りこぼし 0
  => 通っている
```

### 4.8 Put LiveCaption in the Start menu (optional)

Double-click **`InstallToStartMenu.bat`**. It adds a `LiveCaption` entry to the
current user's Start menu, so you do not have to find this folder before a
meeting.

**That entry opens no window.** It goes to the task tray. Right-click the icon
to open the control page, read the log, or quit (see 6.6). To watch it start,
run `StartLiveCaption.bat` by hand. That one keeps a console window.

```
Windows key  ->  type "livecaption"  ->  Enter
```

To keep the entry visible, right-click LiveCaption in the Start menu and choose
**Pin to Start** or **Pin to taskbar**.

No administrator rights are needed. It creates one shortcut, at
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\LiveCaption.lnk`.

**Run `InstallToStartMenu.bat` again if you move or rename this folder.** The
shortcut holds an absolute path, so it stops working when the folder moves.

The icon comes from `etc/LiveCaption.ico`. To change the icon, edit
`scripts/make_icon.py`, run it, and register the shortcut again. To remove the
entry, run `InstallToStartMenu.bat` from a terminal with `-Remove`.

```powershell
InstallToStartMenu.bat -Remove
```

### 4.9 If you operate the caption PC remotely

You can put the caption PC in another room. **Do not use RDP (Remote Desktop).**

RDP creates a separate session, locks the console session, and redirects the
audio to "remote audio". **RDP cuts the path to `CABLE Input` that your meeting
software was using, and the captions stop.**

Use a tool that drives the console session itself, such as a VNC-style remote
desktop. A VNC-style tool does not change the audio device setup.

### 4.10 Reaching the control page from another machine (optional)

Sending the whole screen over a remote desktop is often slow. The control page
is a web page, so **opening it directly from another machine is faster and more
reliable.**

If you use Tailscale, start it like this and the control page also listens on
your tailnet address.

```
pixi run caption --web --control-bind
```

With no value it finds this PC's Tailscale address by itself. At start-up you
will see:

```
操作画面:   http://localhost:8081  (yours only. Never share it)
            http://100.x.x.x:8081  (from inside the tailnet. Restrict it with an ACL)
```

**127.0.0.1 always stays.** You can still work at the machine itself when
Tailscale is down. Right after a reboot, if Tailscale is not up yet, it keeps
retrying in the background until the address appears.

**The control page has no authentication.** Only the address the request comes
from protects it. So:

- **Never expose it outside the tailnet.** LiveCaption refuses addresses
  outside the Tailscale ranges (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`)
- **Restrict it with a Tailscale ACL** so only your own devices can reach it.
  Without that limit, anyone you invited to your tailnet can open
  the control page. Anyone who opens it can quit the app, start delivering, and
  **read the meeting record**

The connection is plain HTTP, so **the copy buttons do not work** (a browser
restriction). The URL is selected for you; press Ctrl+C. The copy buttons still
work when you open the page at `localhost` on the machine itself.

---

## 5. Run a meeting

### Step 1. Start the app

**Under Docker, open the control page in a browser.** On an always-on machine
the container is already running, so there is nothing else to start.

**If you set `LIVECAPTION_RESTART=no`** (2.4), start the container first, on the
host.

```sh
docker compose up -d
```

```
https://livecaption.<tailnet>.ts.net:8443    control page   <- never share this one
```

Each meeting has its own viewer URL. It is shown on the **This meeting** tab.

**With Windows native**, double-click **`StartLiveCaption.bat`**. If you did
step 4.8, you can use **LiveCaption** in the Start menu instead. The control
page opens in your browser.

```
http://localhost:8081              control page   <- never share this one
http://localhost:8080/v/<random>   viewer page    <- share this one
```

**Never screen-share the control page.** It shows the Zoom caption token.

**LiveCaption starts with caption generation stopped.** No audio is read and
no API is called until you press start. You can launch LiveCaption before you join
the meeting, and the conversation during setup does not reach the recogniser.

#### Reading the control page

Captions are on the left, controls on the right. Drag the divider to change the
width; double-click it to go back to the default.

The right side has three tabs.

| Tab | What is in it | When you look at it |
|---|---|---|
| **This meeting** | Right now, the meeting being delivered, how people see it, meeting record | Every meeting |
| **Manage meetings** | Add, edit and delete schedules | Before a meeting |
| **Setup** | Audio input, glossary, delay tuning, quit | When you set the machine up |

**Everything you touch on the day is under This meeting.** You rarely open Setup
once the machine is set up.

**Manage meetings opens inside the right panel.** The captions stay on the left.

**The panel width does not change when you switch tabs.** If the Manage meetings
tab is too narrow, drag the divider. The tab's contents grow to the width you
set.

**A red dot on the This meeting tab means something there has failed.** The dot
appears so that you notice a failure while another tab is open.

The three entries under "How people see it" are folded. **Even folded, the
right side of each row says `delivering` or `sending`, so you can tell how many
are running.**

**The control page can be Japanese or English.** Pick the language in the
**日本語 / English** box in the header. The page reloads at once, and **the next
start uses the same language.** The rest of this manual names the buttons in
Japanese, because that is the default.

Only the control page changes. The captions, the recognised text, and the
meeting record are never translated. The viewer page is in English already.

### Step 2. Join the meeting

**Under Docker, LiveCaption joins by itself.** Put the Zoom invitation URL into
the meeting entry on the Manage meetings tab, and it joins when you press
"Start this meeting now" or when the scheduled time arrives. The audio output, the muted microphone and
the display name are already set inside the container. Section 6, "Scheduled
meetings", has the details.

**The level meter in step 4 tells you whether LiveCaption joined.** When it
fails to join, start VNC from the control page and look at the Zoom window. You
then see a waiting room, a passcode prompt, or an expired sign-in.

**With Windows native**, check three things in the meeting software on the
caption PC.

- **Audio output: `CABLE Input`.** If this is wrong, no audio reaches LiveCaption
- **Microphone: muted.** This PC never speaks
- **Display name: something like `Live Captions`.** The name appears in the
  participant list, so make it clear what this PC is doing

Then join the meeting.

### Step 3. Check the input device

Open the **Setup** tab on the control page and look at **音声の入力** (audio
input).

**Under Docker it is `pulse`.** That is the virtual audio device inside the
container: Zoom plays into it and LiveCaption reads from it. **Do not change it
once it is set up.**

**With Windows native it is `CABLE Output`.** If it is not, pick the right
one from the list. **The change takes effect as soon as you pick it.** There is
no apply button. Press **一覧を更新** (refresh the list) if you plugged in a
device just now.

The list shows the host API as well as the name, because Windows shows
`CABLE Output` three times: once for MME, once for DirectSound, once for WASAPI.

```
2: CABLE Output (VB-Audio Virtual (MME, 16 ch)
```

Any of the three works. MME is the default and is fine.

### Step 4. Check the direction, then start caption generation

On the **This meeting** tab, look at **字幕の向き** (caption direction).
**Choose it for each meeting.**

| Choice | What kind of meeting | Captions you get |
|---|---|---|
| 日本語 → 英語 | The meeting is held in Japanese | English |
| 英語 → 日本語 | The meeting is held in English | Japanese |

**The change takes effect as soon as you pick it.** There is no apply button.
You can change it in the middle of a meeting: the recogniser is not
reconnected, so captions keep running.

**A sentence spoken in the caption language is shown as it is, not translated.**
In a 日本語 → 英語 meeting, an English sentence is shown as it is. **One choice
covers a meeting that is English in the first half and Japanese in the second.**

**The next start uses the same direction.** LiveCaption remembers your last
choice.

Then press **開始** (start) at the top, under **いまの状態** (right now).

**Stop stops all three.** It closes caption generation, the delivery to
participants (the viewer URL), and the sending to the Zoom captions. **It closes
all three because the worst outcome is to believe you stopped while the captions
keep going.** Stopping delivery makes the viewer URL stop working, so Stop is
not a button to press at every break. What was stopped is shown at once.

**Watch the level meter below the button.** If nobody has spoken yet, the meter
does not move. Ask someone to speak. If the meter stays flat while a person is
speaking, the input device is wrong. Pick another one. You can change the
device while captions are being generated.

### Step 5. Show the captions

There are three outputs. **You can use all three at the same time.**

| Output | Host rights | How people see it | Leaves your network |
|---|---|---|---|
| Zoom caption API | **Required** | Each person turns manual captions on | Through Zoom |
| Screen share | Not required | You share the viewer page full screen | **No** |
| Give people a URL | Not required | People open a URL on their own device | Through Cloudflare or Tailscale |

#### A. Zoom caption API (needs host or co-host rights)

The token can only be made during a meeting, and only by a host or a co-host.

1. The host presses the arrow next to "Captions" in the toolbar, opens
   **"Manual captions setup"**, and turns manual captions on.
   **"Copy the API token" does not appear until manual captions are on**
2. From the same place, the host chooses **"Copy the API token"**
3. The host sends that token to you, for example in the meeting chat
4. Under **How people see it**, open **Zoomの字幕に流す** (into the Zoom captions),
   paste the token into **APIトークン**, and
   press **登録** (register). The field is masked, and it clears after you
   register
5. Press **開始** (start). Three warm-up captions are sent first

**Turn off the meeting software's automatic captions.** If they keep running,
LiveCaption's captions leave the screen before people can read them.

**Watch the status in the top right of the control page.**

| Display | Meaning |
|---|---|
| `Zoom: 未登録` | No token yet |
| `Zoom: 停止中` | Token registered. Press 開始 to send |
| `Zoom: 送信中` | Captions are being sent |
| `Zoom: 送信中（失敗 3）` | **Sending is failing.** The token expired, or the meeting changed |
| `生成: 停止中` | **No captions are being made.** Nothing flows, even if Zoom is set to sending |
| `生成: 中` | Captions are being made |

**Do not miss the failure count.** Zoom answers a wrong token with a non-200
status and no explanation. Get a new token and register it again.

#### B. Screen share

This needs no host rights, and the captions never leave your network. **You
share the screen of the PC you attend the meeting on**, not the screen of the
machine that makes the captions.

1. Under **How people see it**, open **画面共有で見せる** (show on a shared
   screen) and press **閲覧画面を開く** (open the viewer page)
2. Press **F11** to make the viewer page full screen
3. Share that browser window in your meeting software

**Under Docker the viewer page opens by its tailnet name.** That works as long
as the machine you opened the control page on is on the tailnet. To show it on
a machine that is not, use C, "Give people a URL".

**Screen sharing compresses the picture.** Small text becomes hard to read.
After you start sharing, check on a second device that the text is readable. If
it is not, show fewer lines (`config.WEB_LINES`).

#### C. Give people a URL

This needs no host rights either. LiveCaption opens an outgoing tunnel and
gives you a public URL and a QR code.

**Under Docker the route is Tailscale.** The container has no `cloudflared`, so
C-1 below does not apply. Read C-2. The preparation is already done in 2.3.

**There are two routes.** Open **ブラウザで見てもらう** (in a browser) under
**How people see it**, and pick one under **Route** (経路).

| | Cloudflare | Tailscale |
|---|---|---|
| Preparation | install `cloudflared` on the caption PC | one setting on the tailnet |
| URL | **changes every time you deliver** | **never changes** |
| Can you give it to people in advance? | no | **yes** |

Use Cloudflare for a meeting decided at short notice. Use Tailscale when you
want the URL in the invitation.

##### C-1. Give the URL during the meeting (Cloudflare)

**Install `cloudflared` on the caption PC first.** Without it, Start delivering
fails and the screen tells you how to install it. Use either of these.

```powershell
winget install --id Cloudflare.cloudflared
```

Or download
[cloudflared-windows-amd64.exe](https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe)
and put it in `local/bin/cloudflared.exe` inside the repository. Somewhere on
PATH works too. For any other location, pass `--cloudflared <path>`.

1. Set **Route** to **Cloudflare**
2. Press **Start delivering** (配信を開始). The status shows `配信: 起動中…` and then `配信: 中`
3. Show the QR code to the people who want captions, or send them the URL.
   **Press Copy the URL** and paste it into the meeting chat
4. To give people the QR code as a picture, press **Save the QR code**. Your
   browser saves a PNG (656 by 656 pixels) to its download folder. Put that
   file in an e-mail or on a slide
5. They open the URL on their own phone or laptop

##### C-2. Give the URL in advance (Tailscale)

The host name never changes, so **you know the URL the day before.** You can
put it in the invitation next to the meeting link.

Prepare once. **Under Docker this was done in 2.3.**

With Windows native, run this on the caption PC and follow the consent page
that opens in your browser. You need tailnet admin rights.

```
tailscale funnel 8080
```

It enables two things: HTTPS certificates, and the `funnel` attribute in the
policy file.

Then, for each meeting:

1. Set **Route** to **Tailscale**
2. Type the meeting name under **Meetings** and press **Add**. The URL for that
   meeting appears at once
3. Copy the URL, or save the QR code, and put it in the invitation
4. **On the day, select that meeting.** Press the circle on the left
5. Press **Start delivering**

**Only the selected meeting is delivered.** The other meetings' URLs do not
open that day. People from last week's meeting cannot watch today's captions.

**A URL you can give out in advance is convenient, and that is also its
weakness.** Only the random text at the end of the URL protects it, and it stays
the same for every meeting on that entry. If it leaks, delete that meeting and
make a new one.

**Neither route sends anything until you start delivering.** Once you do, the
captions go out through Cloudflare or Tailscale. **Do not give people a URL for
meetings whose content must not leave your organisation.** Use screen share
instead.

### Step 6. Tell the audience

Say this at the start of the meeting, to the people who will read the captions:

- **Each person turns manual captions on.** Nobody sees the captions until they do
- **Drag the caption area to make it taller.** The default is four lines. Four
  lines is not enough room to read a translation

### Step 7. End the meeting

**You do not have to quit the app after a meeting.** Once caption generation is
stopped, no API is called and nothing is billed. Leave it until the next
meeting.

To quit the app itself, open the **Setup** tab.

**Under Docker the button restarts it.** The container stops and starts again
within seconds. LiveCaption is used only from the control page, so the button
does not stop it for good (6.6).

**With Windows native the button quits.** The browser tab and the terminal
window both close. If you started it from the task tray, right-clicking the
icon and choosing quit does the same.

---

## 6. Scheduled meetings

So far a person sits at the control page for every meeting. **If you enter a
schedule, LiveCaption runs at the set time on its own.** This assumes the
machine stays powered on. **With Docker on an always-on machine, this is the
normal way to use it.**

It does four things by itself:

1. Selects the meeting and starts delivering
2. Joins Zoom
3. Starts making captions
4. Leaves and stops when the meeting is over

**The Zoom caption token is the one thing LiveCaption cannot get by itself**,
because only the host can create it, and only during the meeting. Section 6.5
covers how to receive it.

### 6.0 Starting without a schedule

**You can run the same four steps right now.** Use them for a meeting you never
put in the schedule, or for one that starts early.

Pick the meeting on the **This meeting** tab and press **Start this meeting
now**. The line under the button says what will happen for that meeting.

```
Delivery -> join Zoom -> Caption generation -> post to the chat
```

It joins Zoom only for a meeting that has a URL, and posts to the chat only for
a meeting with that box ticked (both are set on the **Manage meetings** tab).
**The chat gets the URL twice: when you press the button, and three minutes
later** (for a scheduled meeting, at the start time and three minutes later).

**It takes tens of seconds to start**, because Zoom has to launch and the
tunnel has to open. The progress is shown under **Right now**. To end it, press
**Stop now**.

This is not the **Start** button at the top. That one starts reading audio and
recognising speech, nothing else: **it never opens the tunnel and never joins
Zoom.**

### 6.1 Entering a schedule

Pick the **Manage meetings** tab on the control page. Each meeting row holds the
fields below.

Adding `/meetings` to the control page URL shows the same page
(`http://localhost:8081/meetings` with Windows native,
`https://livecaption.<tailnet>.ts.net:8443/meetings` under Docker).

| Field | Meaning |
|---|---|
| Start | When the meeting starts |
| Weekly | Repeat on the same weekday at the same time |
| Zoom | The invitation URL (`https://zoom.us/j/...`) or the meeting number. Empty: it does not join |
| Minutes before | Start delivering and join Zoom this many minutes early |
| Stop after silence | If no recognised text appears for this long, the meeting is treated as over |
| Hard cap | Stop after this long even if sound continues |
| Start this meeting automatically | **Only meetings with this ticked run on their own** |

**Automatic is off by default.** Running it automatically sends captions out
with nobody watching. Do not tick it for meetings whose content must not leave
your organisation.

Weekly is the only repeat. There is no support for more complex schedules.

### 6.2 Watching what it does

**Right now** is at the top of the **This meeting** tab.

```
Right now
[Start] [Stop]  Stopped
Waiting
2026-09-25 10:00  Morning meeting    in 23 min
2026-09-27 13:00  Collaborators      in 2 d 3 h
[Stop now] [Skip the next one]
```

While it runs, it shows how long until the silence stop and until the hard cap.

**A failure stays until you dismiss it.** With nobody watching, a failure that
scrolls away is never seen. Read it, then press **Got it**.

**Skip the next one** skips a single occurrence. The schedule itself stays.

### 6.3 Posting to the Zoom chat

**Once captions start, LiveCaption can post the caption URL and a QR code to the
meeting chat.** Tick the box on the Manage meetings tab. **It is off by default.**

The message is always in English.

```
Live captions for this meeting (Japanese to English):
https://livecaption.<tailnet>.ts.net/v/xxxxxxxx
Open the link in any browser. No app or sign-in needed.
```

**The URL goes first, the QR code second.** In a meeting where the host has
turned file sending off, the QR code is rejected, but **the URL has already
arrived**.

#### When it posts

**Twice: at the scheduled start time, and three minutes later.** The message is
the same both times. LiveCaption itself starts a few minutes earlier, but **it
does not post then.**

The reason is how Zoom works. **Zoom chat does not show what was said before you
joined.** Posting before the start time leaves nothing for the people who join
on time. The second post is for the people who join a little late.

**Anyone who joins more than three minutes late still misses it.** That is a
Zoom limit, and LiveCaption cannot avoid it. For a meeting where
people join late, put the caption URL in the Zoom invitation as well, or press
**Zoomのチャットに投げる** (post to the Zoom chat) by hand during the meeting.

**If Zoom is slow to join and the first post happens after the start time, the
second post is delayed by at least a minute**, so the same text never appears
twice within seconds.

To change the times, edit `SCHEDULE_CHAT_AT_MIN` in
`src/live_caption/config.py`. There is no `.env` setting for them.

**It also waits for Zoom to finish joining.** After the `zoommtg:` link is
opened, Zoom takes from tens of seconds to a few minutes to show the
meeting window. LiveCaption checks every 10 seconds and stops trying after 10
minutes, writing the result to the log.

To post by hand, press **Zoomのチャットに投げる** (post to the Zoom chat) inside
**ブラウザで見てもらう** (in a browser) under **How people see it**. It posts to
the meeting you are in right now.

#### How it works, and where it is weak

**Zoom has no API for posting into the chat of a live meeting.** LiveCaption
drives the Zoom windows instead. This method has these weaknesses.

- **It will stop working silently if Zoom changes.** It finds the windows by
  their class names
- **There is no way to confirm the message arrived.** LiveCaption knows only
  that it sent the message
- **It borrows the clipboard.** LiveCaption saves the text and writes it back,
  but **an image or a file you had on the clipboard is lost**
- While posting, the Zoom chat window comes to the front

**Under Docker that screen is inside the container.** Nobody can see it, so it
cannot appear in a screen share. The clipboard it borrows is the container's,
not the one on the machine you are working from.

**With Windows native it happens on the caption PC's screen. If you share that
screen, the Zoom chat window shows for a moment.** The clipboard it borrows is
the caption PC's.

A failure here does not stop the captions. The result is written to the log.

### 6.4 Zoom client settings

**In a meeting where the host uses AI Companion, a dialog appears when
LiveCaption joins** ("AI Companion is on"). **You cannot turn it off in the Zoom
settings.** Do not look for a way to.

**Leave it alone.** The captions appear even though nobody presses it. It causes
no trouble when LiveCaption runs with nobody watching.

The rest of this section is about Windows native. **Under Docker the
settings are already done.** They are written when the container starts, so you
can skip the rest. All you need is the Zoom sign-in (2.6).

With Windows native, if you use automatic joining, set these **once** in
the Zoom client. They are not per-meeting.

- Speaker `CABLE Input`, microphone muted (same as Step 2)
- Mute my microphone when joining
- Turn off my video when joining
- **Do not show the "Join with Computer Audio" prompt**

The last one matters most. **If that prompt is still shown, LiveCaption joins
but no audio arrives.** With nobody there to press it, the failure is hard to
diagnose.

If no sound arrives for five minutes after joining, the control page says:

> No sound is coming from Zoom. The passcode may be wrong, it may be stuck in
> the waiting room, or an update dialog may be open. Look at the screen.

**Personal links (`https://zoom.us/my/...`) are not supported**, because they do
not contain a meeting number. Use an invitation URL with a number (`/j/...`).

**The only way to leave Zoom is to quit the client**, because there is no way to
leave a meeting from outside the client. **LiveCaption quits only a meeting it
joined itself.** It never quits a meeting you joined.

### 6.5 The host URL

Even when LiveCaption is not the host, you can still put captions into Zoom
if the host helps.

Each meeting can have a **separate secret URL**, different from the
participants' one. Press **Show the host URL** in the schedule editor.

**Give this URL to the host only. Do not confuse it with the participants'
URL.** Anyone who holds it can send that meeting's captions to Zoom.

When the host opens it in a browser, a page appears for pasting the token. Once
the host sends the token, LiveCaption starts sending captions to Zoom.

**This URL exists only when the route is Tailscale.** It is never made for
Cloudflare, because TLS ends at Cloudflare and the Zoom credential would pass
through there in the clear.

It is accepted only when all of these are true:

- It is the meeting currently being delivered
- It is within 30 minutes of the scheduled start, or the meeting is running
- It has not been used yet (a second attempt is refused; **Accept one more** on
  the control page opens it again)

**If the URL leaks, delete that meeting and make a new one.**

### 6.6 Running resident, and starting at logon

**Under Docker it is already resident.** The container runs with
`restart: unless-stopped`, so it starts again by itself after a crash and after
a host reboot.

**This is why the control page offers a restart, not a quit.** To stop
it for real, run this on the host:

```sh
docker compose stop
```

A container stopped this way does not start again when the host reboots. Use
`docker compose start` to start it again. **Stopping it also stops the control
page, so you cannot undo it remotely.** Do not run that command when you are
working from somewhere else.

The rest of this section is about Windows native.

Double-click `StartLiveCaptionTray.vbs` and it goes to the task tray **with no
window at all**.

| Icon colour | State |
|---|---|
| Blue | Waiting |
| Green | Captions are running |
| Red | A failure is waiting to be read |

Hover to see the next meeting, or the one running now. Right-click to open the
control page, read the log, or quit.

There is no window, so the log goes to `local/log/` (the last 20 runs).

**Keep `StartLiveCaptionTray.vbs` pure ASCII if you edit it.** Windows does not
read that file as UTF-8. Non-ASCII comments make it **do nothing at all when you
double-click it, with no error message.** There is a note about this at the top
of the file too.

To start it at Windows logon, run this. No administrator rights are needed.

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

Add `-Remove` to undo it.

**Keep the caption PC logged in.** A locked screen is fine. It cannot run when
you are logged out, so after a reboot nothing starts until somebody logs in.

---

## 7. The glossary

The tables in `etc/glossary/` decide how well technical terms are translated.
**These tables are the part of LiveCaption you maintain.**

### Build tables for your own field

**Put one file per subject** in `etc/glossary/`, with the extension `.tsv`.

There is a sample at [glossary-example.tsv](glossary-example.tsv). Copy it into
`etc/glossary/` and replace the words with the ones your own meetings use.

```powershell
copy docs\glossary-example.tsv etc\glossary\MyProject.tsv
```

**`etc/glossary/` is not in Git, so a fresh clone has no tables at all.**
Personal names and organisation names make the tables work better, and you do
not want to share those. Your tables stay on your own machine.

**Under Docker you upload the tables from the control page**, not by putting
files in `etc/glossary/`. The steps are in 2.7.

There are two reasons to split them. First, you can exclude the words a
meeting does not need. Second, there is a limit on how many words can be passed
to the recogniser (`config.ASR_KEYWORD_LIMIT`, 200 by default). One large table
used for every meeting fills that limit with words the meeting does not need,
and the words that matter are cut.

### Format

One term per line, three columns separated by tabs.

```
Japanese (correct form)  <TAB>  English  <TAB>  common misrecognitions (comma separated)
```

The table is used in two places.

1. **Recognition.** The Japanese and English forms are passed to the recogniser
   as keywords, which makes it more likely to hear the term correctly
2. **Translation.** The whole table goes into the translation prompt, and the
   third column becomes replacement rules

**The third column is the important one.** The recogniser still makes mistakes.
When you write down the errors the recogniser really made, the translation step
can recover the correct term from them.

### Choose tables for each meeting

The **用語集** (glossary) row on the **Setup** tab is folded. **You can see what is
in use without opening it**: the table names and the total word count. Press the
row to open the list, and tick the tables you want. **The change takes effect as
soon as you tick.**

When there are eight or more tables, a filter box appears above the list.
**Tables you have ticked stay visible even when the filter hides the others.**
Otherwise you could untick a table without seeing which table you removed.
**全部選ぶ** (select all) and **全部外す** (clear all) also apply to
tables hidden by the filter.

Below the ticks you see the total word count and how many words go to the
recogniser. **Watch the limit.** Words past `config.ASR_KEYWORD_LIMIT` never
reach the recogniser. The word count turns red when words are cut. Use fewer
tables.

**The choice is remembered.** The next start uses the same combination. You can
also choose the tables at start-up.

```powershell
pixi run caption --web --glossary table1 table2
```

**When the same Japanese term appears in several tables, the entries are
merged.** The English comes from the first table that has it, and the
misrecognitions are collected from all of them. If two tables disagree on the
English, LiveCaption shows a warning on screen and uses the first table.

### Update the tables after a meeting

1. Open the `.md` of the meeting record (download it from the control page).
   The header says which tables that meeting used
2. Find the terms that came out wrong
3. Add the wrong text that the recogniser actually produced to the third column
   of the table that holds that term

Three kinds of entry belong in the third column.

- **Homophone errors.** A word written with the wrong characters
- **English misrecognitions.** An acronym turned into an ordinary English word;
  `PID` heard as "peed", for example
- **A word with the end of the previous word attached to its front.** The
  recogniser gets the word boundary wrong, so record that joined form as well

**Do not guess.** If a word makes no sense, ask the person who was speaking. You
cannot recover some errors from the sound alone.

**A word you meant to add may not be in the table.** When the columns are
separated by spaces instead of a tab, the whole line is read as one Japanese
term, and **neither the English nor the misrecognitions are registered.** You
cannot see this by looking at the file.

**Test a word you added before the meeting.** Speak a sentence that contains it
and check that the caption shows the right English. Writing it in the table is
not proof that it works.

---

## 8. The meeting record

**LiveCaption saves a record of every meeting by default.** You do not have to
turn it on.

The files go to **`local/transcripts/`**. Under Docker that is inside the
container, in the `state` volume. **The folder and the script
(`transcript_to_md.py`) say "transcript", and this manual says "meeting
record". They are the same files.**

```
local/transcripts/live-caption_2026-09-08_143012.jsonl   appended one sentence at a time
local/transcripts/live-caption_2026-09-08_143012.md      readable form, written at exit
```

### Downloading a record

**The records stay on the machine that makes the captions. Download them from
the control page.** You do not have to go to that machine, or copy the
files from it over a remote desktop.

There are two buttons under **Meeting record** on the **This meeting** tab.

| Button | What you get |
|---|---|
| **Readable (.md)** | The version people read. Recognised text and caption, paired |
| **Original (.jsonl)** | One sentence per line, with the measured delays. Use this one when you update the glossary |

The file is saved in the download folder of **the machine you opened the control
page on**.

**Only the latest record can be downloaded.** The line under the buttons says
which one it is. For an older meeting, take the file from
`local/transcripts/` directly. Under Docker, use `docker compose cp`.

```sh
docker compose cp engine:/app/local/transcripts ./transcripts
```

**You can download during the meeting.** The `.md` then says the meeting is
still going.

**Pressing Stop closes the record** (about three seconds later). The `.md` is
written and a new record starts, so a download after Stop gives you the
finished meeting. A short pause does not split the record, as long as you start
again within three seconds.

### Changing the folder

Changing the folder is optional. You can keep the records outside the
repository, for example when the repository is in a synced folder. Write the
folder in `.env`.

```
LIVECAPTION_SAVE_DIR=%LOCALAPPDATA%\LiveCaption\transcripts
```

**A folder that does not exist is created. A folder that cannot be written to
is refused, and the record goes to the default folder instead.** To change it
per launch, use `--save-dir`. That option takes priority over `.env`.

**Under Docker, leave it at the default.** The records go into the `state`
volume and survive recreating the container. **To put them in a folder on the
host, add a volume in `compose.yml`, not a path in `.env`.** A container path
in `LIVECAPTION_SAVE_DIR` is still a path inside the container.

Records accumulate. **Deleting them is a manual job.** From the host:

```sh
docker compose exec engine ls /app/local/transcripts
docker compose exec engine rm /app/local/transcripts/live-caption_2026-09-01_*
```

### What is in the record

The recognised text and the caption are paired. The timestamp is the time the
recognition became final.

The `.jsonl` also holds the measured delays. They are not in the `.md`, but you
need them when you tune the settings.

| Field | Meaning |
|---|---|
| `cut` | Why the sentence was finalised. `punct` (end mark) / `force` (length) / `idle` (silence) / `flush` (at exit) |
| `waited` | For a sentence finalised by silence, how long it actually waited |
| `took` | Seconds spent on translation |
| `total` | **Seconds from finalising to the first caption line.** `total − took` is the time the sentence waited in the queue |
| `spec` | Present when the speculative translation was used. `total` is then smaller than `took` |
| `dir` | The caption direction at that moment |

- **The `.md` file is written when you press Stop and when the app exits.** If
  the PC loses power, rebuild it from the `.jsonl` with
  `pixi run python scripts/transcript_to_md.py`. (A downloaded `.md` is built
  when you press the button, so it is always current.)
- To keep no record, start with `--no-save`
- If no sentence was produced, no file is written

---

## 9. Commands

### Docker

There is no command to type for a meeting. **These are for setting up and
maintenance.** Run them inside the repository folder.

```sh
docker compose up -d --build     # build and start: the first time, and after changing the code
docker compose logs -f           # start-up messages, and the running log
docker compose ps                # is it running
docker compose restart           # restart without rebuilding
docker compose stop              # stop. It stays down across a host reboot
docker compose start             # bring a stopped one back

docker compose exec engine bash  # get a shell inside
docker compose cp engine:/app/local/transcripts ./transcripts   # take the records out

# Try it with a recording. Nothing is sent to Zoom
docker compose run --rm -v "$PWD/recordings:/samples:ro" engine \
  --from-file /samples/test.wav --dry-run
```

**Do not use `docker compose down -v`.** It deletes the volumes, and with them
the list of meetings, the viewer URLs, the Tailscale signature and the Zoom
sign-in.

### Windows native

For a meeting you use the batch file. The commands below are for setting up,
testing, and recovery.

```powershell
StartLiveCaption.bat                             # this is what you use for a meeting
StartLiveCaptionTray.vbs                         # go to the task tray, no window
InstallToStartMenu.bat                           # add it to the Start menu (once)

pixi run web                                     # the same thing, from a terminal
pixi run caption --web                           # the same
pixi run caption --token "<URL>" --web           # Zoom captions and browser captions
pixi run caption --token "<URL>"                 # Zoom captions only
pixi run caption --web --no-save                 # keep no record
pixi run caption --check-audio 20                # watch the level only (no API)
pixi run devices                                 # list the input devices
pixi run caption --from-file recording.wav --dry-run   # play a WAV in real time
pixi run python scripts/transcript_to_md.py      # rebuild the .md after a crash
pixi run python scripts/cable_loopback.py        # test the VB-CABLE path
```

Options:

| Option | Meaning |
|---|---|
| `--token <URL>` | The Zoom caption API token. You can also paste it on the control page later |
| `--direction <way>` | Caption direction. `ja2en` (English captions for a Japanese meeting) or `en2ja` (Japanese captions for an English meeting). Default: the one you chose last |
| `--glossary <name> ...` | Which tables in `etc/glossary/` to use. Several can be given. Default: the combination you chose last |
| `--device <name>` | Part of the input device name. Default: `CABLE Output`. The Docker version is started with `pulse` |
| `--web [port]` | Show captions in a browser. The number is the viewer port, 8080 by default |
| `--control-port <port>` | The control page port, 8081 by default |
| `--control-bind [address]` | **Also serve the control page on your tailnet address.** With no value it finds the address by itself. 127.0.0.1 always stays. **Addresses outside the Tailscale ranges are refused** (see 4.10) |
| `--tray` | **Go to the task tray.** The log is also written to `local/log/` |
| `--web-bind <address>` | The address the **viewer page** listens on, 127.0.0.1 by default. Use 0.0.0.0 to show the viewer page directly to devices on the same LAN. The control page is not affected |
| `--tunnel` | Open the tunnel at start-up. It is off by default |
| `--no-browser` | Do not open the browser automatically |
| `--no-save` | Keep no record of the meeting |
| `--save-dir <folder>` | Where to write the record. **Takes priority over `LIVECAPTION_SAVE_DIR` in `.env`.** Default: `local/transcripts/` |
| `--dry-run` | Send nothing to Zoom; print to the screen only |
| `--from-file <wav>` | Play a WAV in real time instead of using a device. 24 kHz mono |
| `--loop` | Repeat the `--from-file` file |
| `--delay <level>` | Recognition delay and accuracy. `minimal`, `low`, `medium`, `high`, `xhigh`. Default `low` |
| `--model <name>` | The translation model. Default `gpt-4.1-mini` |
| `--check-audio [seconds]` | Show the level and exit. Calls no API |
| `--list-devices` | List the input devices |
| `--cloudflared <path>` | Where `cloudflared` is. Not needed if it is on PATH or in `local/bin` |

To register and remove the logon task:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove
```

**If a port is taken, change both numbers.** Port 8081 belongs to the control
page, so never give 8081 to the viewer page.

```powershell
pixi run caption --web 8090 --control-port 8091
```

---

## 10. Settings

The settings are in `src/live_caption/config.py`. The defaults come from
measurement, so change them only when you have a reason.

**The four below can be changed from the control page and from `.env`.** You do
not have to edit the code.

### From the control page

On the **Setup** tab, press the **遅延の調整** (delay tuning) row to open it.
**The row is folded because you do not change these values often.**

- Edit a number and leave the field. **The change takes effect at once. No
  restart**
- **`.env` に保存** (save to `.env`) makes the next start use the same values.
  The other lines in `.env`, such as `OPENAI_API_KEY`, are left alone
- **既定に戻す** (reset) goes back to the measured defaults
- When a value differs from the default, the folded header shows how many values
  differ

### In `.env`

`.env.example` shows the format.

| Name in `.env` | Setting |
|---|---|
| `LIVECAPTION_IDLE_FLUSH_SEC` | `IDLE_FLUSH_SEC` |
| `LIVECAPTION_SPECULATE_AFTER_SEC` | `SPECULATE_AFTER_SEC` |
| `LIVECAPTION_FORCE_CUT_CHARS` | `FORCE_CUT_CHARS` |
| `LIVECAPTION_LINE_INTERVAL_SEC` | `LINE_INTERVAL_SEC` |

When a value is replaced, LiveCaption prints a message at start-up.

**If you change only `IDLE_FLUSH_SEC`, `SPECULATE_AFTER_SEC` follows it**
(`IDLE_FLUSH_SEC − 1.1` seconds). Otherwise the speculative translation is sent
too early and is thrown away more often, with no gain.

**A typo does not stop the meeting.** A value that is not a number, or one that
is too small, produces a warning and the default is used. **LiveCaption never
ignores a bad value silently**, so read the start-up output.

### The main settings

| Setting | Default | Meaning |
|---|---|---|
| `ASR_MODEL` | `gpt-live-transcribe` | The speech recognition model |
| `ASR_DELAY` | `low` | How long the recogniser waits before it returns text. In a comparison of `minimal`, `low` and `high`, both `minimal` and `high` got technical terms wrong |
| `ASR_LANGUAGES` | `("ja", "en")` | **Do not fix this to one language.** A meeting can switch language part way through |
| `ASR_KEYWORD_LIMIT` | 200 | How many glossary words reach the recogniser. **Keep it above the size of your tables.** A warning appears at start-up when words are cut |
| `TRANSLATE_MODEL` | `gpt-4.1-mini` | The translation model |
| `CONTEXT_SENTENCES` | 3 | How many previous sentences go to the translation as context |
| `MAX_CAPTION_CHARS` | 80 / 40 | Longest caption line. The value depends on the direction: 80 for English, 40 for Japanese |
| `FORCE_CUT_CHARS` | 70 / 140 | A sentence longer than this is cut. The value depends on the direction: 70 when listening to Japanese, 140 for English. Lower it if the captions go by too fast |
| `IDLE_FLUSH_SEC` | 2.5 | How long to wait after speech stops before finalising a sentence without an end mark. **Measure the gaps between deltas with `pixi run python scripts/stream_test.py` before lowering it.** A value below those gaps cuts sentences in the middle |
| `IDLE_POLL_SEC` | 0.1 | How often that timer is checked. A smaller value wastes less time waiting |
| `SPECULATE_AFTER_SEC` | 1.4 | After this much silence, send the translation without waiting for the sentence to be final. When it matches, the caption appears about 0.9 s earlier. A translation that does not match is thrown away, which costs a little more. `0` turns it off |
| `LINE_INTERVAL_SEC` | 0.6 | The gap between lines sent to Zoom. The caption window is only four lines, so sending them at once pushes the first one out. **It does not apply to the viewer page**, which gets every line at once |
| `WEB_LINES` | 8 | How many lines the viewer page shows |
| `WEB_PORT` | 8080 | The viewer page |
| `CONTROL_PORT` | 8081 | The control page |

---

## 11. When something is wrong

| Symptom | Where to look |
|---|---|
| No log lines appear, and nothing happens | **Did you press 開始 under いまの状態?** Launching LiveCaption is not enough |
| `CABLE Output` is not in the list | Is VB-CABLE installed? Did you restart the PC? |
| The level meter does not move | The speaker setting in your meeting software. Both devices at 48000 Hz. **Is 音声の入力 on the Setup tab set to `CABLE Output`?** |
| "cannot open the input" | Another app may have the device. Does the device accept 48000 Hz? Pick a different input |
| No recognition lines | `OPENAI_API_KEY` in `.env`. **Is `.env` in the top folder of the repository, next to `pixi.toml`? Is it named `.env.txt` by mistake?** Also check the network |
| Recognition lines but no caption lines | A translation error should be on the screen |
| Log lines flow, but nobody sees the captions | **Are you looking at the host's screen?** The host never sees the captions. Did the other person turn manual captions on? Is the token from this meeting? |
| Different captions appear when someone speaks | **The meeting software's automatic captions are running.** Turn them off |
| A term is in the glossary but the recogniser still misses it | It may be cut by `ASR_KEYWORD_LIMIT`. Words at the end of the table do not reach the recogniser |
| Every term of one subject comes out wrong | That subject's table is probably not ticked |
| Captions stopped part way through | The `seq` number went backwards. If you restarted LiveCaption, check `local/seq_state.json` |
| Captions go by too fast to read | Ask the readers to make the caption area taller. Lower `config.FORCE_CUT_CHARS` |
| The recogniser reconnects again and again | The network. Turn off incoming video in the meeting software on the caption PC. Try another line |
| Captions stopped after you connected remotely | **Did you connect with RDP?** It cuts the audio path (see 4.9) |
| Nothing starts at the scheduled time | **Is "Start this meeting automatically" ticked?** It is off by default. Check that the meeting is listed under Right now |
| It joined Zoom but no sound arrives | **Is the "Join with Computer Audio" prompt still shown?** (see 6.4). It may also be stuck in the waiting room, or the passcode may be wrong |
| An AI Companion dialog appears when it joins | **Leave it alone** (see 6.4). The captions appear even though nobody presses it. You cannot turn it off in the Zoom settings |
| It does not leave when the meeting ends | Check the silence-stop setting. The hard cap always stops it |
| It keeps running after the meeting ended early | If somebody left a microphone open, the sound continues and LiveCaption never sees silence. Press Stop now |
| It does not start at logon | Are you logged in? **It cannot run while you are logged out.** Check the `LiveCaption` task in Task Scheduler |
| No tray icon appears | Read the log in `local/log/`. With no window, that is the only place to look |
| The control page is unreachable from the tailnet | Did you start it with `--control-bind`? Is Tailscale up? Is an ACL blocking it? |

**When you run it in Docker:**

| What you see | Where to look |
|---|---|
| The control page does not open | Is the container running (`docker compose ps`)? Does `docker compose logs` show a Tailscale address? **Open the `https://` URL by its full name** — a short name or an IP does not match the certificate |
| The log keeps showing a sign-in URL | That is what it does without `TS_AUTHKEY`. Open the URL in a browser and approve |
| It is on the tailnet but cannot reach other nodes | **Tailnet Lock.** The node is not signed. Get the node key with `tailscale lock status` and sign it on a machine that holds a signing key (2.3) |
| Delivery will not start | Does your ACL have `funnel` under `nodeAttrs`? A tagged device leaves `autogroup:member` and loses Funnel without that line (2.3) |
| The host loses its network when the container runs | **ConnMan** (3.2, Linux only). It breaks tens of seconds later, so checking right after start-up tells you nothing |
| It cannot get into Zoom | Has the sign-in expired? **Start VNC and look at the Zoom window** (2.6). It may be in a waiting room |
| The glossary stays empty | This repository contains no tables (2.7). Upload yours from the control page |
| You cannot find the meeting records | They are in the volume. Downloading from the control page is the quick way |
| It starts again after you pressed quit | That is `restart: unless-stopped`. To stop it for real, use `docker compose stop` (6.6) |

**Check the receiving side first.** A successful send is not proof that anything
is displayed. Every send can return 200 while nothing appears on the other
screen, and the cause is on the receiving side.

**Captions do not disappear on a timer.** A line stays until newer lines push it
out. If you miss one, look again instead of sending it a second time.

---

## 12. How it works

You do not need this section to use LiveCaption.

### Why it joins as a separate participant

**With Docker:**

```
[host PC]        runs the meeting as usual
     |
[container]
  Zoom ---------------- joins as a silent participant, microphone muted
   |
   +-- output --> meeting        (a PulseAudio null sink)
                     |
                  meeting.monitor --> LiveCaption
                                        |-- speech recognition
                                        |-- translation
                                        +-- output
```

**With Windows native:**

```
[host PC]        runs the meeting as usual
     |
[caption PC]
  meeting software ----- joins as a silent participant, microphone muted
   |
   +-- speaker output --> CABLE Input
                             |   (VB-CABLE, a virtual audio cable)
                          CABLE Output --> LiveCaption
                                             |-- speech recognition
                                             |-- translation
                                             +-- output
```

**Do not run LiveCaption on the host PC.** Meeting software does not send your
own microphone to your own speaker. If you record the speaker output on the host
PC, **the host's own voice is missing.** A separate participant receives the
mixed audio, and that mix contains every participant's voice.

**The audio goes through a virtual device**, so LiveCaption opens it as an
ordinary recording device. It never uses a loopback API, so volume and mute
settings do not affect what it reads. Under Docker that device is a PulseAudio
null sink; on Windows it is VB-CABLE. **The Docker version does not touch
the host's audio hardware at all.** Playing music on the host changes nothing,
and the host needs no sound card.

### Technical terms are corrected in two steps

Do not try to get technical terms right with speech recognition alone.

1. **Recognition.** The glossary words are passed as keywords, so the recogniser
   is more likely to catch the sound of a term
2. **Translation.** The glossary, the replacement rules built from real
   misrecognitions, and the last three sentences as context all go to the model.
   **This step does most of the work**

When the recogniser produces a word that only sounds similar, the translation
step can recover the term from the context and the table. **The translation step
cannot recover everything.** When a misrecognition becomes another technical
term that also makes sense, the translation reads well but is wrong.

### Only finished sentences are sent

If you show a partial sentence and rewrite it later, the reader cannot follow.
Captions accumulate and are never replaced, so a partial line stays on screen and
the corrected version appears below it. **The reader sees the same sentence
twice.**

For this reason, streaming cannot hide the translation time.

### Delay

The delay depends on how the sentence ends (measured).

| | Recognition | Wait to finalise | Translation | Send | Total |
|---|---|---|---|---|---|
| Ends with an end mark (about 74%) | 0.2–1 s | 0 s | 0.9 s | 0.3 s | **about 1.5–2 s** |
| Ends in silence (about 9%) | same | 2.5 s | 0.9 s | 0.3 s | **about 4 s** |
| Cut because it grew too long (the remaining 17%) | same | 0 s | 0.9 s | 0.3 s | **about 1.5–2 s** |

For a sentence that ends in silence, the translation is sent during the wait
(`SPECULATE_AFTER_SEC`). When it matches, the 0.9 s of translation disappears.

**The wait cannot be shortened.** Measuring the gaps between the recogniser's
outputs shows gaps of nearly 2.5 seconds while a person is still talking.
Lowering the threshold only cuts more sentences in the middle.

---

## Related documents

- [test-procedure.md](test-procedure.md) — the staged test for bringing up a new
  caption PC, and the checklist for the day of the meeting
- [glossary-example.tsv](glossary-example.tsv) — a sample glossary table.
  Copy it into `etc/glossary/`
- `.env.example` — every setting you can change, with a note on each
- `compose.yml` — the Docker set-up. The volumes and the environment variables
  are explained there
