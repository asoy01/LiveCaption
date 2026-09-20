# LiveCaption Manual

日本語版は [manual.ja.md](manual.ja.md) にあります。

LiveCaption shows real-time subtitles for a meeting. At the moment it translates
between Japanese and English.

You choose the direction.

- **English captions for a meeting held in Japanese**, for people who do not
  speak Japanese
- **Japanese captions for a meeting held in English**, for people who find it
  hard to follow spoken English

LiveCaption can send captions to Zoom, but it is not limited to Zoom. It reads
the audio from a virtual audio cable, so it works with any meeting software that
plays sound.

**Use one Windows PC only for captions.** That PC joins the meeting as a silent
participant. The reason is in section 10, "How it works".

**You can schedule meetings and let it run with nobody watching.** At the set
time it joins Zoom, starts delivering, and shuts the meeting down when it ends.
See section 4, "Scheduled meetings".

---

## 1. What you need

| Item | Notes |
|---|---|
| A Windows PC | Used only for captions |
| VB-CABLE | A free virtual audio cable. [vb-audio.com/Cable/](https://vb-audio.com/Cable/) |
| An OpenAI API key | Speech recognition and translation both call the OpenAI API |
| pixi | Builds the Python environment. [pixi.sh](https://pixi.sh) |
| Git | Used to fetch the repository |
| An account for the caption PC in your meeting software, such as Zoom | A free account is enough |
| Host or co-host rights | Only if you want to use the Zoom caption API. The other two ways do not need host rights |

Speech recognition costs **$0.017 per minute**, so about one US dollar per hour.
The translation cost is much smaller than that.

Silence is billed too, so press stop during a break. **Stop also stops delivery
and the Zoom captions** (see step 4 in section 3), so for a short break it is
simpler to leave it running.

---

## 2. Install

Do this once on the caption PC.

### 2.1 Install VB-CABLE

Download it from [vb-audio.com/Cable/](https://vb-audio.com/Cable/).
**Run the installer as administrator, then restart the PC.**

### 2.2 Set both CABLE devices to 48000 Hz

After the restart, open the Windows sound settings and check these two devices.

| Device | Format |
|---|---|
| `CABLE Input` (playback) | **48000 Hz**, 16 bit |
| `CABLE Output` (recording) | **48000 Hz**, 16 bit |

**Set both to 48000 Hz.** If the two rates differ, Windows resamples the audio
and the sound is distorted. LiveCaption opens the device at 48000 Hz and converts
the audio to 24000 Hz itself.

### 2.3 Install pixi

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

### 2.4 Fetch the repository

Put it anywhere you like. A path with no spaces and no non-ASCII characters is
the safe choice.

```powershell
cd C:\src
git clone https://github.com/asoy01/LiveCaption.git
cd LiveCaption
```

You can also download a ZIP from the GitHub page and unpack it, but then you
cannot update with `git pull`.

### 2.5 Build the Python environment

Run this inside the folder you cloned.

```powershell
pixi install
```

The packages go into a `.pixi` folder there. **pixi does not change your system
Python.** The first run takes a few minutes.

### 2.6 Write the API key into `.env`

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
you cloned in 2.4.

```powershell
copy .env.example .env
notepad .env
```

Write your key in the file and save it.

```
OPENAI_API_KEY=sk-...
```

You create the key at [platform.openai.com](https://platform.openai.com).

**The name is `.env`, not `.env.txt`.** Notepad can add `.txt` when you use
"Save as" to make a new file. The `copy` command above avoids that.

`.env` is not committed to Git. It is listed in `.gitignore`, so you do not
publish your key by accident.

**The value in `.env` is used instead of the environment variable of the same
name.** If you set the key in both places, LiveCaption reads `.env`.

### 2.7 Check that the audio path works

This test needs no meeting and no API key. It plays a sine wave into
`CABLE Input` and reads it back from `CABLE Output`.

```powershell
pixi run python scripts/cable_loopback.py
```

If the amplitude comes back, VB-CABLE is working.

```
--- 本体が既定で選ぶもの: 2 'CABLE Output (VB-Audio Virtual ' [MME] ---
  区間 39、音あり 39（100%）  最大 peak 0.299  取りこぼし 0
  => 通っている
```

### 2.8 Put LiveCaption in the Start menu (optional)

Double-click **`InstallToStartMenu.bat`**. It adds a `LiveCaption` entry to the
current user's Start menu, so you do not have to find this folder before a
meeting.

**That entry opens no window.** It goes to the task tray; right-click the icon
for the control page, the log, and quit (see 4.6). To watch it start up, run
`StartLiveCaption.bat` by hand — that one keeps a console.

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

### 2.9 If you operate the caption PC remotely

You can put the caption PC in another room. **Do not use RDP (Remote Desktop).**

RDP creates a separate session, locks the console session, and redirects the
audio to "remote audio". **RDP cuts the path to `CABLE Input` that your meeting
software was using, and the captions stop.**

Use a tool that drives the console session itself, such as a VNC-style remote
desktop. A VNC-style tool does not change the audio device setup.

### 2.10 Reaching the control page from another machine (optional)

Sending the whole screen over a remote desktop is often slow. The control page
is a web page, so **opening it directly from another machine is faster and more
reliable.**

If you use Tailscale, start it like this and the control page also listens on
your tailnet address.

```
pixi run caption --web --control-bind
```

With no value it finds this PC's Tailscale address by itself. At startup you
will see:

```
操作画面:   http://localhost:8081  (yours only. Never share it)
            http://100.x.x.x:8081  (from inside the tailnet. Restrict it with an ACL)
```

**127.0.0.1 always stays.** You can still work at the machine itself when
Tailscale is down. Right after a reboot, if Tailscale is not up yet, it keeps
retrying in the background until the address appears.

**The control page has no authentication.** The only thing protecting it is
where a request can come from. So:

- **Never expose it outside the tailnet.** Addresses outside the Tailscale
  ranges (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`) are refused by design
- **Restrict it with a Tailscale ACL** so only your own devices can reach it.
  If you have invited other people into your tailnet, they can otherwise open
  the control page — and that means quitting the app, starting delivery, and
  **reading the meeting transcript**

The connection is plain HTTP, so **the copy buttons do not work** (a browser
restriction). The URL is selected for you; press Ctrl+C. From `localhost` at the
machine itself they work as before.

---

## 3. Run a meeting

### Step 1. Start the app

Double-click **`StartLiveCaption.bat`**. If you did step 2.8, you can use
**LiveCaption** in the Start menu instead. The control page opens in your
browser.

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
once it is right.

**Manage meetings opens inside the right panel.** The captions stay on the left.

**The panel width does not change when you switch tabs.** If the management page
is too narrow, drag the divider. Its contents grow to the width you set.

**A red dot on the This meeting tab means something there has failed.** It is
there so you notice while another tab is open.

The three entries under "How people see it" are folded. **Even folded, the right
of each row says `delivering` or `sending`, so you can tell how many are running.**

### Step 2. Join the meeting from the caption PC

Check three things in the meeting software on the caption PC.

- **Audio output: `CABLE Input`.** If this is wrong, no audio reaches LiveCaption
- **Microphone: muted.** This PC never speaks
- **Display name: something like `Live Captions`.** The name appears in the
  participant list, so make it clear what this PC is doing

Then join the meeting.

**The control page can be Japanese or English.** Pick the language in the
**日本語 / English** box in the header. The page reloads at once, and **the next
start uses the same language.** The rest of this manual names the buttons in
Japanese, because that is the default.

Only the control page changes. The captions, the transcription, and the meeting
record are never translated. The viewer page is in English already.

### Step 3. Check the input device

Open the **Setup** tab on the control page and look at **音声の入力** (audio
input). It should be `CABLE Output`. If it is not, pick the right one from the list. **The change
takes effect as soon as you pick it.** There is no apply button. Press
**一覧を更新** (refresh the list) if you plugged in a device just now.

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
participants (the viewer URL), and the sending to the Zoom captions. **The worst
case is thinking you stopped and having captions keep flowing.** Stopping
delivery kills the viewer URL, so this is not a button to press at every break.
What was stopped is written on the spot.

**Watch the level meter just below it.** If nobody has spoken yet, the meter does not move.
Ask someone to speak. If the meter stays flat while a person is speaking, the
input device is wrong. Pick another one. You can change the device while
captions are being generated.

### Step 5. Show the captions

There are three ways. **You can use all three at the same time.**

| Output | Host rights | How people see it | Leaves your network |
|---|---|---|---|
| Zoom caption API | **Required** | Each person turns manual captions on | Through Zoom |
| Screen share | Not required | You share the viewer page full screen | **No** |
| Hand out a URL | Not required | People open a URL on their own device | Through Cloudflare |

#### A. Zoom caption API (needs host or co-host rights)

The token can only be made during a meeting, and only by a host or a co-host.

1. The host presses the arrow next to "Captions" in the toolbar, opens
   **"Manual captions setup"**, and turns manual captions on.
   **"Copy the API token" does not appear until manual captions are on**
2. From the same place, the host chooses **"Copy the API token"**
3. The host sends that token to the caption PC in the meeting chat
4. Under **How people see it**, open **Zoomの字幕に流す** (into the Zoom captions),
   paste the token into **APIトークン**, and
   press **登録** (register). The field is masked, and it clears after you
   register
5. Press **開始** (start). Three warm-up captions are sent first

**Turn off the meeting software's automatic captions.** If they keep running,
LiveCaption's captions are pushed out and cannot be read.

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

This needs no host rights, and the captions never leave your network.

1. Under **How people see it**, open **画面共有で見せる** (show on a shared
   screen) and press **閲覧画面を開く** (open the viewer page)
2. Press **F11** to make the viewer page full screen
3. Share that browser window in your meeting software

**Screen sharing compresses the picture.** Small text breaks up. After you start
sharing, check on a second device that the text is readable. If it is not, show
fewer lines (`config.WEB_LINES`).

#### C. Hand out a URL

This needs no host rights either. The caption PC opens an outgoing tunnel and
gives you a public URL and a QR code.

**There are two routes.** Open **ブラウザで見てもらう** (in a browser) under
**How people see it**, and pick one under **Route** (経路).

| | Cloudflare | Tailscale |
|---|---|---|
| Preparation | none | one setting on the tailnet |
| URL | **changes every time you deliver** | **never changes** |
| Can you hand it out in advance? | no | **yes** |

Use Cloudflare for a meeting decided on the spot. Use Tailscale when you want
the URL in the invitation.

##### C-1. Hand out on the spot (Cloudflare)

1. Set **Route** to **Cloudflare**
2. Press **Start delivering** (配信を開始). The status shows `配信: 起動中…` and then `配信: 中`
3. Show the QR code to the people who want captions, or send them the URL.
   **Press Copy the URL** and paste it into the meeting chat
4. To hand out the QR code as a picture, press **Save the QR code**. Your
   browser saves a PNG (656 by 656 pixels) to its download folder. Put that
   file in an e-mail or on a slide
5. They open the URL on their own phone or laptop

##### C-2. Hand out in advance (Tailscale)

The caption PC's host name never changes, so **the URL is fixed the day
before.** You can put it in the invitation next to the meeting link.

Prepare once. Run this on the caption PC and follow the consent page that opens
in your browser. You need tailnet admin rights.

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

**A fixed URL is also a weakness.** The only thing protecting it is the random
text at the end of the URL. If it leaks, delete that meeting and make a new one.

**Neither route delivers anything by default.** Captions travel through
Cloudflare or Tailscale. **Do not hand out a URL for meetings whose content
must not leave your organisation.** Use screen share instead.

### Step 6. Tell the audience

Say this at the start of the meeting, to the people who will read the captions:

- **Each person turns manual captions on.** Nobody sees the captions until they do
- **Drag the caption area to make it taller.** The default is four lines. Four
  lines is not enough room to read a translation

### Step 7. End the meeting

Open the **Setup** tab and press **終了** (quit) under **アプリの終了**. The
browser tab and the terminal window both close. If you started it from the task
tray, right-clicking the icon and choosing quit does the same.

---

## 4. Scheduled meetings

So far a person sits at the control page for every meeting. **If you enter a
schedule, it runs at the set time on its own.** This assumes the caption PC
stays powered on.

It does four things by itself:

1. Selects the meeting and starts delivering
2. Joins Zoom
3. Starts making captions
4. Leaves and stops when the meeting is over

**Only the Zoom caption token cannot be obtained automatically**, because only
the host can create it, during the meeting. Section 4.4 covers how to receive it.

### 4.0 Starting without a schedule

**You can run the same four steps right now.** Use this for a meeting you never
put in the schedule, or one that starts early.

Pick the meeting on the **This meeting** tab and press **Start this meeting
now**. The line under the button says what will happen for that meeting.

```
Delivery -> join Zoom -> Caption generation -> post to the chat
```

It joins Zoom only for a meeting that has a URL, and posts to the chat only for
a meeting with that box ticked (both are set on the **Manage meetings** tab).
**The chat gets the URL twice: when you press the button, and three minutes
later** (for a scheduled meeting, at the start time and three minutes later).

**It takes tens of seconds to come up**, because Zoom has to launch and the
tunnel has to open. The progress is shown under **Right now**. To end it, press
**Stop now**.

This is not the **Start** button at the top. That one starts reading audio and
transcribing, nothing else: **it never opens the tunnel and never touches
Zoom.**

### 4.1 Entering a schedule

Pick the **Manage meetings** tab on the control page. Each meeting row has the
fields laid out in it.

Opening `http://localhost:8081/meetings` directly shows the same thing.

| Field | Meaning |
|---|---|
| Start | When the meeting starts |
| Weekly | Repeat on the same weekday at the same time |
| Zoom | The invitation URL (`https://zoom.us/j/...`) or the meeting number. Empty: it does not join |
| Minutes before | Start delivering and join Zoom this many minutes early |
| Stop after silence | If no transcript appears for this long, the meeting is treated as over |
| Hard cap | Stop after this long even if sound continues |
| Start this meeting automatically | **Only meetings with this ticked run on their own** |

**Automatic is off by default.** Running it automatically sends captions out
with nobody watching. Do not tick it for meetings whose content must not leave.

Weekly is the only repeat. There is no support for more complex schedules.

### 4.2 Watching what it does

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
scrolls away is a failure nobody sees. Read it, then press **Got it**.

**Skip the next one** sits out a single occurrence. The schedule itself stays.

### 4.3 Posting to the Zoom chat

**Once captions start, LiveCaption can post the caption URL and a QR code to the
meeting chat.** Tick the box on the Manage meetings tab. **It is off by default.**

The message is always in English.

```
Live captions for this meeting (Japanese to English):
https://ms-s1-max.tail4b88d2.ts.net/v/xxxxxxxx
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
on time. The second post is for the people who come in a little late.

**Anyone who joins more than three minutes late still misses it.** That is a
Zoom limit and there is nothing this side can do about it. For a meeting where
people join late, put the caption URL in the Zoom invitation as well, or press
**Zoomのチャットに投げる** (post to the Zoom chat) by hand during the meeting.

**If Zoom is slow to join and the first post slips, the second is held back by
at least a minute**, so the same text never appears twice within seconds.

To change the times, edit `SCHEDULE_CHAT_AT_MIN` in
`src/live_caption/config.py`. There is no `.env` setting for them.

**It also waits for Zoom to finish joining.** After the `zoommtg:` link is
opened, Zoom takes anywhere from tens of seconds to a few minutes to show the
meeting window. LiveCaption looks every 10 seconds and gives up after 10
minutes, writing that to the log.

To post by hand, press **Zoomのチャットに投げる** (post to the Zoom chat) inside
**ブラウザで見てもらう** (in a browser) under **How people see it**. It posts to
the meeting you are in right now.

#### How it works, and where it is weak

**Zoom has no API for posting into the chat of a live meeting.** LiveCaption
drives the Zoom windows on the caption PC instead. That brings these weaknesses.

- **It will stop working silently if Zoom changes.** It finds the windows by
  their class names
- **There is no way to confirm the message arrived.** We only know it was sent
- **It borrows the clipboard.** Text is saved and put back, but **an image or a
  file you had on the clipboard is lost**
- While posting, the Zoom chat window comes to the front on the caption PC.
  **If you are sharing the caption PC's screen, it shows for a moment**

A failure here does not stop the captions. The result is written to the log.

### 4.4 Zoom client settings

If you use automatic joining, set these **once** in the Zoom client. They are
not per-meeting.

- Speaker `CABLE Input`, microphone muted (same as Step 2)
- Mute my microphone when joining
- Turn off my video when joining
- **Do not show the "Join with Computer Audio" prompt**

The last one matters most. **If that prompt is still shown, it joins but no
audio arrives.** With nobody there to press it, the failure is hard to diagnose.

If no sound arrives for five minutes after joining, the control page says:

> No sound is coming from Zoom. The passcode may be wrong, it may be stuck in
> the waiting room, or an update dialog may be open. Look at the screen.

**Personal links (`https://zoom.us/my/...`) are not supported**, because they do
not contain a meeting number. Use an invitation URL with a number (`/j/...`).

**The only way to leave Zoom is to quit the client**, because nothing lets you
leave a meeting from outside. **It never quits a meeting you joined yourself**
— it only quits when it was the one that joined.

### 4.5 The host URL

Even when the caption PC is not the host, you can still put captions into Zoom
if the host helps.

Each meeting can have a **separate secret URL**, different from the
participants' one. Press **Show the host URL** in the schedule editor.

**Give this URL to the host only. Do not confuse it with the participants'
URL.** Whoever holds it can push that meeting's captions into Zoom.

When the host opens it in a browser, a page appears for pasting the token. Once
sent, the caption PC starts sending captions to Zoom.

**This URL exists only when the route is Tailscale.** It is never made for
Cloudflare, because TLS ends at Cloudflare and the Zoom credential would pass
through there in the clear.

It is accepted only when all of these hold:

- It is the meeting currently being delivered
- It is within 30 minutes of the scheduled start, or the meeting is running
- It has not been used yet (a second attempt is refused; **Accept one more** on
  the control page opens it again)

**If the URL leaks, delete that meeting and make a new one.**

### 4.6 Running resident, and starting at logon

Double-click `StartLiveCaptionTray.vbs` and it goes to the task tray **with no
window at all**.

| Icon colour | State |
|---|---|
| Blue | Waiting |
| Green | Captions are running |
| Red | A failure is waiting to be read |

Hover to see the next meeting, or the one running now. Right-click for the
control page, the log, and quit.

There is no window, so the log goes to `local/log/` (the last 20 runs).

**Keep `StartLiveCaptionTray.vbs` pure ASCII if you edit it.** Windows does not
read that file as UTF-8. Non-ASCII comments make it **do nothing at all when you
double-click it, with no error message.** The file says so at the top too.

To start it at Windows logon, run this. No administrator rights are needed.

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

Add `-Remove` to undo it.

**Keep the caption PC logged in.** A locked screen is fine. It cannot run when
you are logged out, so after a reboot nothing starts until somebody logs in.

---

## 5. The glossary

The tables in `etc/glossary/` decide how well technical terms are translated.
**These tables are the part of LiveCaption you maintain.**

### Build tables for your own field

**Put one file per subject** in `etc/glossary/`, with the extension `.tsv`.

There is a sample at [glossary-example.tsv](glossary-example.tsv). Copy it into
`etc/glossary/` and replace the words with the ones your own meetings use.

```powershell
copy docs\glossary-example.tsv etc\glossary\MyProject.tsv
```

**`etc/glossary/` is not in Git.** Personal names and organisation names make the
tables work better, and you do not want to share those. Your tables stay on your
own machine.

There are two reasons to split them. First, you can leave out the words a
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

**When the same Japanese term appears in several tables, they are merged.** The
English comes from the first table that has it, and the misrecognitions are
collected from all of them. If two tables disagree on the English, LiveCaption shows
a warning on screen and uses the first table.

### Grow the tables after a meeting

1. Open the `.md` of the meeting record (download it from the control page).
   The header says which tables that meeting used
2. Find the terms that came out wrong
3. Add the wrong text that the recogniser actually produced to the third column
   of the table that holds that term

Three kinds of entry belong in the third column.

- **Homophone errors.** A word written with the wrong characters
- **English misrecognitions.** An acronym turned into an ordinary English word;
  `PID` heard as "peed", for example
- **A form with the end of the previous word stuck to the front.** The
  recogniser gets the word boundary wrong, so record the run-on form as well

**Do not guess.** If a word makes no sense, ask the person who was speaking. You
cannot recover some errors from the sound alone.

**A word you meant to add may not be in the table.** Check the words you rely
on by running them through the translation step.

---

## 6. The meeting record

**LiveCaption saves a record of every meeting by default.** You do not have to
turn it on.

The files go to **`local/transcripts/`** on the caption PC.

```
local/transcripts/live-caption_2026-09-08_143012.jsonl   appended one sentence at a time
local/transcripts/live-caption_2026-09-08_143012.md      readable form, written at exit
```

### Downloading a record

**The records stay on the caption PC. Download them from the control page.**
You do not have to walk over to that machine, or pull the files out of it over
a remote desktop.

There are two buttons under **Meeting record** on the **This meeting** tab.

| Button | What you get |
|---|---|
| **Readable (.md)** | The form people read. Recognised text and caption, paired |
| **Original (.jsonl)** | One sentence per line, with the measured delays. Use this one to grow the glossary |

The file lands in the download folder of **the machine you opened the control
page on**.

**Only the latest record can be downloaded.** The line under the buttons says
which one it is. For an older meeting, take the file from
`local/transcripts/` on the caption PC.

**You can download during the meeting.** The `.md` then says the meeting is
still going.

**Pressing Stop closes the record** (about three seconds later). The `.md` is
written and a new record starts, so a download after Stop gives you the
finished meeting. A short pause - stop and start again within three seconds -
does not split the record.

### Changing the folder

You do not need to, but you can keep the records outside the repository - for
example when the repository sits in a synced folder. Write the folder in
`.env`.

```
LIVECAPTION_SAVE_DIR=%LOCALAPPDATA%\LiveCaption\transcripts
```

**A folder that does not exist is created. A folder that cannot be written to
is refused, and the record goes to the default folder instead.** To change it
per launch, use `--save-dir`. That wins over `.env`.

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
  on the spot, so it is always current.)
- To keep no record, start with `--no-save`
- If no sentence was produced, no file is written

---

## 7. Commands

For a meeting you use the batch file. The commands below are for setting up, testing, and recovery.

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
| `--device <name>` | Part of the input device name. Default: `CABLE Output` |
| `--web [port]` | Show captions in a browser. The number is the viewer port, 8080 by default |
| `--control-port <port>` | The control page port, 8081 by default |
| `--control-bind [address]` | **Also serve the control page on your tailnet address.** With no value it finds it by itself. 127.0.0.1 always stays. **Addresses outside the Tailscale ranges are refused** (see 2.10) |
| `--tray` | **Go to the task tray.** The log is also written to `local/log/` |
| `--web-bind <address>` | The address the **viewer page** listens on, 127.0.0.1 by default. Use 0.0.0.0 to show the viewer page directly to devices on the same LAN. The control page is not affected |
| `--tunnel` | Open the tunnel at start-up. It is off by default |
| `--no-browser` | Do not open the browser automatically |
| `--no-save` | Keep no record of the meeting |
| `--save-dir <folder>` | Where to write the record. **Wins over `LIVECAPTION_SAVE_DIR` in `.env`.** Default: `local/transcripts/` |
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

## 8. Settings

The settings are in `src/live_caption/config.py`. The defaults come from
measurement, so change them only when you have a reason.

**The four below can be changed from the control page and from `.env`.** You do
not have to edit the code.

### From the control page

On the **Setup** tab, press the **遅延の調整** (delay tuning) row to open it. **The row is folded
because you do not change these values often.**

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
(`IDLE_FLUSH_SEC − 1.1` seconds). Otherwise the speculative translation fires
too early and is thrown away more often, for no gain.

**A typo does not stop the meeting.** A value that is not a number, or one that
is too small, produces a warning and the default is used. **LiveCaption never
ignores a bad value silently**, so read the start-up output.

### The main settings

| Setting | Default | Meaning |
|---|---|---|
| `ASR_MODEL` | `gpt-live-transcribe` | The speech recognition model |
| `ASR_DELAY` | `low` | How long the recogniser waits before it returns text. Waiting longer can improve accuracy, but both `minimal` and `high` got technical terms wrong in our tests |
| `ASR_LANGUAGES` | `("ja", "en")` | **Do not fix this to one language.** A meeting can switch language part way through |
| `ASR_KEYWORD_LIMIT` | 200 | How many glossary words reach the recogniser. **Keep it above the size of your tables.** A warning appears at start-up when words are cut |
| `TRANSLATE_MODEL` | `gpt-4.1-mini` | The translation model |
| `CONTEXT_SENTENCES` | 3 | How many previous sentences go to the translation as context |
| `MAX_CAPTION_CHARS` | 80 / 40 | Longest caption line. The value depends on the direction: 80 for English, 40 for Japanese |
| `FORCE_CUT_CHARS` | 70 / 140 | A sentence longer than this is cut. The value depends on the direction: 70 when listening to Japanese, 140 for English. Lower it if the captions go by too fast |
| `IDLE_FLUSH_SEC` | 2.5 | How long to wait after speech stops before finalising a sentence without an end mark. **Measure the gaps between deltas with `stream_test.py` before lowering it.** A value below those gaps cuts sentences in the middle |
| `IDLE_POLL_SEC` | 0.1 | How often that timer is checked. A smaller value wastes less time waiting |
| `SPECULATE_AFTER_SEC` | 1.4 | After this much silence, send the translation without waiting for the sentence to be final. When it matches, the caption appears about 0.9 s earlier. Misses are thrown away, which costs a little more. `0` turns it off |
| `LINE_INTERVAL_SEC` | 0.6 | The gap between lines sent to Zoom. The caption window is only four lines, so sending them at once pushes the first one out. **It does not apply to the viewer page**, which gets every line at once |
| `WEB_LINES` | 8 | How many lines the viewer page shows |
| `WEB_PORT` | 8080 | The viewer page |
| `CONTROL_PORT` | 8081 | The control page |

---

## 9. When something is wrong

| Symptom | Where to look |
|---|---|
| Nothing happens. No log lines | **Did you press 開始 under いまの状態?** Launching LiveCaption is not enough |
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
| Captions stopped after you connected remotely | **Did you connect with RDP?** It cuts the audio path (see 2.9) |
| Nothing starts at the scheduled time | **Is "Start this meeting automatically" ticked?** It is off by default. Check that the meeting is listed under Right now |
| It joined Zoom but no sound arrives | **Is the "Join with Computer Audio" prompt still shown?** (see 4.4). It may also be stuck in the waiting room, or the passcode may be wrong |
| It does not leave when the meeting ends | Check the silence-stop setting. The hard cap always stops it |
| It keeps running after the meeting ended early | If somebody left a microphone open, the sound continues and it never falls silent. Press Stop now |
| It does not start at logon | Are you logged in? **It cannot run while you are logged out.** Check the `LiveCaption` task in Task Scheduler |
| No tray icon appears | Read the log in `local/log/`. With no window, that is the only place to look |
| The control page is unreachable from the tailnet | Did you start it with `--control-bind`? Is Tailscale up? Is an ACL blocking it? |

**Check the receiving side first.** A successful send is not proof that anything
is displayed. Every send can return 200 while nothing appears on the other
screen, and the cause is on the receiving side.

**Captions do not disappear on a timer.** A line stays until newer lines push it
out. If you miss one, look again instead of sending it a second time.

---

## 10. How it works

You do not need this section to use LiveCaption.

### Why a separate PC

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
PC, **the host's own voice is missing.** The caption PC receives the mixed audio,
and that mix contains every participant.

Because a virtual audio cable is used, LiveCaption opens `CABLE Output` as an
ordinary recording device. LiveCaption does not use the WASAPI loopback API, so
the volume and mute settings do not affect the audio LiveCaption reads.

### Technical terms are corrected in two steps

Do not try to solve technical terms with speech recognition alone.

1. **Recognition.** The glossary words are passed as keywords, so the recogniser
   is more likely to catch the sound of a term
2. **Translation.** The glossary, the replacement rules built from real
   misrecognitions, and the last three sentences as context all go to the model.
   **This step does most of the work**

When the recogniser produces a word that only sounds similar, the translation
step can recover the term from the context and the table. **The translation step
cannot recover everything.** When a misrecognition becomes another technical
term that also makes sense, the translation is fluent and wrong.

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
