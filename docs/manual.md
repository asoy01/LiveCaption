# LiveCaption Manual

日本語版は [manual.ja.md](manual.ja.md) にあります。

LiveCaption puts real-time subtitles on a meeting. At the moment it translates
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
participant. The reason is in section 9, "How it works".

---

## 1. What you need

| Item | Notes |
|---|---|
| A Windows PC | Used only for captions.|
| VB-CABLE | A free virtual audio cable. [vb-audio.com/Cable/](https://vb-audio.com/Cable/) |
| An OpenAI API key | Speech recognition and translation both call the OpenAI API |
| pixi | Builds the Python environment. [pixi.sh](https://pixi.sh) |
| Git | Used to fetch the repository |
| An account for the caption PC in your meeting software, such as Zoom | A free account is enough |
| Host or co-host rights | Only if you want to use the Zoom caption API. The other ways do not need it |

Speech recognition costs **$0.017 per minute**, so about one US dollar per hour.
The translation cost is small enough to ignore next to that.

Silence is billed too, so press stop during a break.

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
and the sound breaks up. LiveCaption opens the device at 48000 Hz and converts
the audio to 24000 Hz itself.

### 2.3 Install pixi

pixi builds the Python environment. **You do not have to install Python
separately.** pixi brings the version this project needs.

Open PowerShell and run one of these.

```powershell
winget install prefix-dev.pixi
```

```powershell
powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
```

**Open a new PowerShell window afterwards**, or the change to PATH is not
picked up. Then check that it is there.

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

The packages go into a `.pixi` folder there. **Your system Python is left
alone.** The first run takes a few minutes.

### 2.6 Write the API key into `.env`

**The file goes in the top of the repository**, in the same folder as
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

The quickest way is to copy the sample, inside the folder you cloned in 2.4.

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

`.env` is not committed to Git; it is listed in `.gitignore`. Your key cannot be
published by accident.

**The value in `.env` wins over the environment variable of the same name.**
If you set the key in both places, `.env` is the one that is used.

### 2.7 Set up the meeting software on the caption PC

- Set the display name to something like `Live Captions`. The name appears in
  the participant list, so make it clear what this PC is doing
- Speaker: **`CABLE Input`**
- Microphone: **muted**
- Turning on "Hide non-video participants" saves bandwidth

### 2.8 Check that the audio path works

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

### 2.9 Put LiveCaption in the Start menu (optional)

Double-click **`InstallToStartMenu.bat`**. It adds a `LiveCaption` entry to the
current user's Start menu, so you do not have to find this folder before a
meeting.

```
Windows key  ->  type "livecaption"  ->  Enter
```

To keep it in sight, right-click LiveCaption in the Start menu and choose
**Pin to Start** or **Pin to taskbar**.

No administrator rights are needed. It creates one shortcut, at
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\LiveCaption.lnk`.

**Run it again if you move or rename this folder.** The shortcut holds an
absolute path, so it stops working when the folder moves.

The icon comes from `etc/LiveCaption.ico`. To change it, edit
`scripts/make_icon.py`, run it, and register the shortcut again. To remove the
entry, run the file from a terminal with `-Remove`.

```powershell
InstallToStartMenu.bat -Remove
```

### 2.10 If you operate the caption PC remotely

You can put the caption PC in another room. **Do not use RDP (Remote Desktop).**

RDP creates a separate session, locks the console session, and redirects the
audio to "remote audio". **The path to `CABLE Input` that your meeting software
was using is cut, and the captions stop.**

Use a tool that drives the console session itself, such as a VNC-style remote
desktop. Those leave the audio device setup alone.

---

## 3. Run a meeting

### Step 1. Start the app

Double-click **`StartLiveCaption.bat`**. If you did step 2.9, you can use
**LiveCaption** in the Start menu instead. The control page opens in your
browser.

```
http://localhost:8081              control page   <- never share this one
http://localhost:8080/v/<random>   viewer page    <- share this one
```

**Never screen-share the control page.** It shows the Zoom caption token.

**LiveCaption starts with caption generation stopped.** No audio is taken in and
no API is called until you press start. You can launch the app before you join
the meeting, and the small talk during setup does not reach the recogniser.

### Step 2. Join the meeting from the caption PC

Set the speaker in your meeting software to `CABLE Input`, and mute the
microphone.

### Step 3. Check the input device

On the control page, look at **音声の入力** (audio input). It should be
`CABLE Output`. If it is not, pick the right one from the list. **The change
takes effect as soon as you pick it.** There is no apply button. Press
**一覧を更新** (refresh the list) if you plugged in a device just now.

The list shows the host API as well as the name, because Windows shows
`CABLE Output` three times: once for MME, once for DirectSound, once for WASAPI.

```
2: CABLE Output (VB-Audio Virtual (MME, 16 ch)
```

Any of the three works. MME is the default and is fine.

### Step 4. Check the direction, then start caption generation

Look at **字幕の向き** (caption direction) on the control page. **Choose it for
each meeting.**

| Choice | What kind of meeting | Captions you get |
|---|---|---|
| 日本語 → 英語 | The meeting is held in Japanese | English |
| 英語 → 日本語 | The meeting is held in English | Japanese |

**The change takes effect as soon as you pick it.** There is no apply button.
You can change it in the middle of a meeting: the recogniser is not
reconnected, so captions keep running.

**When the other language comes in, it is passed through, not translated.** In a
日本語 → 英語 meeting, an English sentence is shown as it is. **One choice covers
a meeting that is English in the first half and Japanese in the second.**

**The next start uses the same direction.** It remembers your last choice.

Then press **開始** (start) under **字幕の生成** (caption generation).

**Watch the level meter.** If nobody has spoken yet, the meter does not move.
Ask someone to speak. If the meter stays flat while a person is speaking, the
input device is wrong. Pick another one. You can change the device while
captions are being generated.

### Step 5. Show the captions

There are three ways. **You can use all three at the same time.**

| Output | Host rights | How people see it | Leaves your network |
|---|---|---|---|
| Zoom caption API | **Required** | Each person turns on "Show Captions" | Through Zoom |
| Screen share | Not required | You share the viewer page full screen | **No** |
| Hand out a URL | Not required | People open a URL on their own device | Through Cloudflare |

#### A. Zoom caption API (needs host or co-host rights)

The token can only be made during a meeting, and only by a host or a co-host.

1. The host presses the arrow next to "Captions" in the toolbar and chooses
   **"Copy the API token"**
2. The host sends that token to the caption PC in the meeting chat
3. Paste the token into **APIトークン** under **Zoom字幕** on the control page and
   press **登録** (register). The field is masked, and it clears after you
   register
4. Press **開始** (start). Three warm-up captions are sent first

**Turn off the meeting software's automatic captions.** They share the same
four-line window with LiveCaption's captions. If the automatic captions keep
running, our lines are pushed out of the window.

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

1. Press **閲覧画面を開く** (open the viewer page) under **画面共有で見せる**
2. Press **F11** to make the viewer page full screen
3. Share that browser window in your meeting software

**Screen sharing compresses the picture.** Small text breaks up. After you start
sharing, check on a second device that the text is readable. If it is not, show
fewer lines (`config.WEB_LINES`).

#### C. Hand out a URL

This needs no host rights either. It opens a temporary tunnel through
Cloudflare and gives you a public URL and a QR code.

1. Press **トンネルを開始** (start the tunnel) under **参加者への配信**. The status
   shows `配信: 起動中…` and then `配信: 中`
2. Show the QR code to the people who want captions, or send them the URL.
   **Press URLをコピー (copy the URL) under the URL** and paste it into the
   meeting chat
3. They open the URL on their own phone or laptop

**The tunnel is off by default.** Captions travel through Cloudflare.
**Do not use it for meetings whose content must not leave your organisation.**
Use screen share instead.

### Step 6. Tell the audience

Say this at the start of the meeting, to the people who will read the captions:

- Turn on "Show Captions" in the meeting software
- **Drag the caption area to make it taller.** The default is four lines. Four
  lines is not enough room to read a translation

### Step 7. End the meeting

Press **終了** (quit) under **アプリの終了** on the control page. The browser tab
and the terminal window both close.

---

## 4. The glossary

How well technical terms come out is decided by the tables in `etc/glossary/`.
**This is the main thing you maintain.**

### Build tables for your own field

**Put one file per subject** in `etc/glossary/`, with the extension `.tsv`.
Not in `docs/`. **These are data the app reads, not something to read yourself.**

The tables that ship with the repository are examples. **Replace them with the
words that come up in your own meetings.**

There are two reasons to split them. You can leave out the words a meeting does
not need. And there is a limit on how many words can be passed to the recogniser
(`config.ASR_KEYWORD_LIMIT`, 200 by default). One large table used for every
meeting fills that budget with words the meeting does not need, and the words
that matter fall off the end.

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

**The third column is the important one.** The recogniser still gets things
wrong. When you write down the errors it actually made, the translation step can
recover the correct term from them.

### Choose tables for each meeting

The **用語集** (glossary) row on the control page is folded. **You can see what is
in use without opening it**: the table names and the total word count. Press the
row to open the list, and tick the tables you want. **The change takes effect as
soon as you tick.**

When there are eight or more tables, a filter box appears above the list.
**Tables you have ticked stay visible even when the filter hides the others.**
Being able to untick something you cannot see would leave you not knowing what
you removed. **全部選ぶ** (select all) and **全部外す** (clear all) also apply to
tables hidden by the filter.

Below the ticks you see the total word count and how many words go to the
recogniser. **Watch the limit.** Words past `config.ASR_KEYWORD_LIMIT` never
reach the recogniser. The number turns red when that happens; use fewer tables.

**The choice is remembered.** The next start uses the same combination. You can
also set it at start-up.

```powershell
pixi run caption --web --glossary table1 table2
```

**When the same Japanese term appears in several tables, they are merged.** The
English comes from the first table that has it, and the misrecognitions are
collected from all of them. If two tables disagree on the English, the app says
so on screen and uses the first one.

### Grow the tables after a meeting

1. Open `live-caption_*.md` in your Downloads folder. The header says which
   tables that meeting used
2. Find the terms that came out wrong
3. Add the wrong text that the recogniser actually produced to the third column
   of the table that holds that term

Three kinds of entry belong in the third column.

- **Homophone errors.** A word written with the wrong characters
- **English misrecognitions.** An acronym turned into an ordinary English word;
  `PID` heard as "peed", for example
- **A form with the end of the previous word stuck to the front.** The
  recogniser gets the word boundary wrong, so record the run-on form as well

**Do not guess.** If a word makes no sense, ask the person who was speaking.
Some errors cannot be worked out from the sound alone.

**A word you meant to add may not actually be there.** Check the words you rely
on by running them through the translation step.

---

## 5. The meeting record

**LiveCaption saves a record of every meeting by default.** You do not have to
turn it on.

The files go to **your Downloads folder**.

```
Downloads/live-caption_2026-09-08_143012.jsonl   appended one sentence at a time
Downloads/live-caption_2026-09-08_143012.md      readable form, written at exit
```

The recognised text and the caption are paired. The timestamp is the time the
recognition became final.

The `.jsonl` also holds the measured delays. They are not in the `.md`, but you
need them when you tune the settings.

| Field | Meaning |
|---|---|
| `cut` | Why the sentence was finalised. `punct` (end mark) / `force` (length) / `idle` (silence) / `flush` (at exit) |
| `waited` | For a sentence finalised by silence, how long it actually waited |
| `took` | Seconds spent on translation |
| `total` | **Seconds from finalising to the first caption line.** `total − took` is queueing |
| `spec` | Present when the speculative translation was used. `total` is then smaller than `took` |
| `dir` | The caption direction at that moment |

- To read the record during the meeting, press **途中まで読む** under
  **会議の記録** on the control page
- **The `.md` file is written at exit.** If the PC loses power, rebuild it from
  the `.jsonl` with `pixi run python scripts/transcript_to_md.py`
- To keep no record, start with `--no-save`
- To put it somewhere else, use `--save-dir`
- If no sentence was produced, no file is written

---

## 6. Commands

For a meeting you use the batch file. The rest is for everything else.

```powershell
StartLiveCaption.bat                             # this is what you use for a meeting
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
| `--web [port]` | Show captions in a browser. This is the viewer port, 8080 by default |
| `--control-port <port>` | The control page port, 8081 by default. **It always listens on 127.0.0.1 only** |
| `--web-bind <address>` | The address the **viewer page** listens on, 127.0.0.1 by default. Use 0.0.0.0 to show it directly to devices on the same LAN. The control page is not affected |
| `--tunnel` | Open the tunnel at start-up. It is off by default |
| `--no-browser` | Do not open the browser automatically |
| `--no-save` | Keep no record of the meeting |
| `--save-dir <folder>` | Where to write the record. Default: your Downloads folder |
| `--dry-run` | Send nothing to Zoom; print to the screen only |
| `--from-file <wav>` | Play a WAV in real time instead of using a device. 24 kHz mono |
| `--loop` | Repeat the `--from-file` file |
| `--delay <level>` | Recognition delay and accuracy. `minimal`, `low`, `medium`, `high`, `xhigh`. Default `low` |
| `--model <name>` | The translation model. Default `gpt-4.1-mini` |
| `--check-audio [seconds]` | Show the level and exit. Calls no API |
| `--list-devices` | List the input devices |
| `--cloudflared <path>` | Where `cloudflared` is. Not needed if it is on PATH or in `local/bin` |

**If a port is taken, change both numbers.** Port 8081 belongs to the control
page, so never give 8081 to the viewer page.

```powershell
pixi run caption --web 8090 --control-port 8091
```

---

## 7. Settings

They are in `src/live_caption/config.py`. The defaults come from measurement, so
change them only when you have a reason.

**The four below can be changed from the control page and from `.env`.** You do
not have to edit the code.

### From the control page

Press the **遅延の調整** (delay tuning) row to open it. **It is folded because it
is not something you change often.**

- Edit a number and leave the field: **it takes effect at once. No restart**
- **`.env` に保存** (save to `.env`) makes the next start use the same values.
  The other lines in `.env`, such as `OPENAI_API_KEY`, are left alone
- **既定に戻す** (reset) goes back to the measured defaults
- When a value differs from the default, the folded header says how many

### In `.env`

`.env.example` shows the format.

| Name in `.env` | Setting |
|---|---|
| `LIVECAPTION_IDLE_FLUSH_SEC` | `IDLE_FLUSH_SEC` |
| `LIVECAPTION_SPECULATE_AFTER_SEC` | `SPECULATE_AFTER_SEC` |
| `LIVECAPTION_FORCE_CUT_CHARS` | `FORCE_CUT_CHARS` |
| `LIVECAPTION_LINE_INTERVAL_SEC` | `LINE_INTERVAL_SEC` |

When a value is replaced, the app says so at start-up.

**If you change only `IDLE_FLUSH_SEC`, `SPECULATE_AFTER_SEC` follows it**
(`IDLE_FLUSH_SEC − 1.1` seconds). Without that, the speculative translation
fires too early and is thrown away more often, for no gain.

**A typo does not stop the meeting.** A value that is not a number, or one that
is too small, produces a warning and the default is used. **It is never ignored
silently**, so read the start-up output.

### The main settings

| Setting | Default | Meaning |
|---|---|---|
| `ASR_MODEL` | `gpt-live-transcribe` | The speech recognition model |
| `ASR_DELAY` | `low` | Both `minimal` and `high` got technical terms wrong |
| `ASR_LANGUAGES` | `("ja", "en")` | **Do not fix this to one language.** A meeting can switch language part way through |
| `ASR_KEYWORD_LIMIT` | 200 | How many glossary words reach the recogniser. **Keep it above the size of your tables.** A warning appears at start-up when words are cut |
| `TRANSLATE_MODEL` | `gpt-4.1-mini` | The translation model |
| `CONTEXT_SENTENCES` | 3 | How many previous sentences go to the translation as context |
| `MAX_CAPTION_CHARS` | 80 / 40 | Longest caption line. It depends on the direction: 80 for English, 40 for Japanese |
| `FORCE_CUT_CHARS` | 70 / 140 | A sentence longer than this is cut. It depends on the direction: 70 when listening to Japanese, 140 for English. Lower it if the captions go by too fast |
| `IDLE_FLUSH_SEC` | 2.5 | How long to wait after speech stops before finalising a sentence without an end mark. **Measure the gaps between deltas with `stream_test.py` before lowering it.** Below the gaps that occur while a person is still talking, it cuts sentences in the middle |
| `IDLE_POLL_SEC` | 0.1 | How often that timer is checked. Finer means less wasted waiting |
| `SPECULATE_AFTER_SEC` | 1.4 | After this much silence, send the translation without waiting for the sentence to be final. When it matches, the caption appears about 0.9 s earlier. Misses are thrown away, which costs a little more. `0` turns it off |
| `LINE_INTERVAL_SEC` | 0.6 | The gap between lines sent to Zoom. The caption window is only four lines, so sending them at once pushes the first one out. **It does not apply to the viewer page**, which gets every line at once |
| `WEB_LINES` | 8 | How many lines the viewer page shows |
| `WEB_PORT` | 8080 | The viewer page |
| `CONTROL_PORT` | 8081 | The control page |

---

## 8. When something is wrong

| Symptom | Where to look |
|---|---|
| Nothing happens. No log lines | **Did you press 開始 under 字幕の生成?** Launching the app is not enough |
| `CABLE Output` is not in the list | Is VB-CABLE installed? Did you restart the PC? |
| The level meter does not move | The speaker setting in your meeting software. Both devices at 48000 Hz. **Is 音声の入力 set to `CABLE Output`?** |
| "cannot open the input" | Another app may have the device. Does the device accept 48000 Hz? Pick a different input |
| No recognition lines | `OPENAI_API_KEY` in `.env`. **Is `.env` in the top of the repository, next to `pixi.toml`? Is it named `.env.txt` by mistake?** Also check the network |
| Recognition lines but no caption lines | A translation error should be on the screen |
| Log lines flow, but nobody sees the captions | **Are you looking at the host's screen?** The host never sees them. Did the other person turn on "Show Captions"? Is the token from this meeting? |
| Different captions appear when someone speaks | **The meeting software's automatic captions are running.** Turn them off |
| A term is in the glossary but the recogniser still misses it | It may be cut by `ASR_KEYWORD_LIMIT`. Words at the end of the table do not reach the recogniser |
| Every term of one subject comes out wrong | That subject's table is probably not ticked |
| Captions stopped part way through | The `seq` number went backwards. If you restarted the app, check `local/seq_state.json` |
| Captions go by too fast to read | Ask the readers to make the caption area taller. Lower `config.FORCE_CUT_CHARS` |
| The recogniser reconnects again and again | The network. Turn off incoming video in the meeting software on the caption PC. Try another line |
| Captions stopped after you connected remotely | **Did you connect with RDP?** It cuts the audio path (see 2.10) |

**Check the receiving side first.** A successful send is not proof that anything
is displayed. Every send can return 200 while nothing appears on the other
screen, and the cause is on the receiving side.

**Captions do not disappear on a timer.** A line stays until newer lines push it
out. If you miss one, look again instead of sending it a second time.

---

## 9. How it works

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

Because a virtual audio cable is used, the app opens `CABLE Output` as an
ordinary recording device. It does not use the WASAPI loopback API, so the
volume and mute settings do not affect it.

### Technical terms are handled in two steps

Do not try to solve them with speech recognition alone.

1. **Recognition.** The glossary words are passed as keywords, so the recogniser
   is more likely to catch the sound of a term
2. **Translation.** The glossary, the replacement rules built from real
   misrecognitions, and the last three sentences as context all go to the model.
   **This is the step that does the work**

When the recogniser produces something that only sounds similar, the translation
step can recover the term from the context and the table. **It cannot recover
everything.** When a misrecognition lands on another plausible technical term,
you get a fluent wrong translation.

### Only finished sentences are sent

If you show a partial sentence and rewrite it later, the reader cannot follow.
Captions accumulate and are never replaced, so a partial line stays on screen and
the corrected version appears below it. **The reader sees the same thing twice.**

This is why the translation time cannot be hidden by streaming.

### Delay

It depends on how the sentence ends (measured).

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
- [../etc/glossary/](../etc/glossary/) — the term tables, one file per subject
