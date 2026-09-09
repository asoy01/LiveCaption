# LiveCaption Manual

日本語版は [manual.ja.md](manual.ja.md) にあります。

LiveCaption puts real-time English subtitles on a meeting held in Japanese.
It listens to the meeting audio, recognises the Japanese speech, translates the
speech into English, and shows the English text to the people who need it.

It was written for KAGRA meetings. In those meetings a few people do not speak
Japanese, and the rest of the room speaks Japanese.

**LiveCaption is not limited to Zoom.** It reads the audio from a virtual audio
cable, so it works with any meeting software that plays sound.

---

## 1. How it works

One Windows PC is used only for captions. That PC joins the meeting as a silent
participant. Its microphone stays muted.

```
[host PC]        runs the meeting as usual
     |
[caption PC]
  Zoom ----- joins as a silent participant, microphone muted
   |
   +-- speaker output --> CABLE Input
                             |   (VB-CABLE, a virtual audio cable)
                          CABLE Output --> LiveCaption
                                             |-- speech recognition
                                             |-- translation
                                             +-- output (see below)
```

**Use a separate PC. Do not run LiveCaption on the host PC.** Zoom does not send
your own microphone to your own speaker. If you record the speaker output on the
host PC, the host's own voice is missing. The caption PC receives the mixed audio
from Zoom, and that mix contains every participant.

### Three ways to show the captions

You can use all three at the same time.

| Output | Host rights | How people see it | Leaves your network |
|---|---|---|---|
| Zoom caption API | **Required** | Each person turns on "Show Captions" | Through Zoom |
| Screen share | Not required | You share the viewer page full screen | **No** |
| Hand out a URL | Not required | People open a URL on their own device | Through Cloudflare |

### Delay

It depends on how the sentence ends (measured on 2026-09-09).

| | Recognition | Wait to finalise | Translation | Send | Total |
|---|---|---|---|---|---|
| Ends with an end mark (about 74%) | 0.2–1 s | 0 s | 0.9 s | 0.3 s | **about 1.5–2 s** |
| Ends in silence (about 9%) | same | 2.5 s | 0.9 s | 0.3 s | **about 4 s** |

For a sentence that ends in silence, the translation is sent during the wait
(`SPECULATE_AFTER_SEC`). When it matches, the 0.9 s of translation disappears.

---

## 2. What you need

| Item | Notes |
|---|---|
| A Windows PC | Used only for captions. No GPU needed. 32 GB of memory is more than enough |
| VB-CABLE | A free virtual audio cable. [vb-audio.com/Cable/](https://vb-audio.com/Cable/) |
| An OpenAI API key | Speech recognition and translation both call the OpenAI API |
| pixi | Builds the Python environment. [pixi.sh](https://pixi.sh) |
| A Zoom account for the caption PC | A free account is enough |
| Host or co-host rights | Only if you want to use the Zoom caption API |

---

## 3. Install

Do this once on the caption PC.

### 3.1 Install VB-CABLE

Download it from [vb-audio.com/Cable/](https://vb-audio.com/Cable/).
**Run the installer as administrator, then restart the PC.**

### 3.2 Set both CABLE devices to 48000 Hz

After the restart, open the Windows sound settings and check these two devices.

| Device | Format |
|---|---|
| `CABLE Input` (playback) | **48000 Hz**, 16 bit |
| `CABLE Output` (recording) | **48000 Hz**, 16 bit |

**Set both to 48000 Hz.** If the two rates differ, Windows resamples the audio
and the sound breaks up. LiveCaption opens the device at 48000 Hz and converts
the audio to 24000 Hz itself.

### 3.3 Install the project

```bash
cd C:\Users\Yoichi\Dropbox\src\projects\LiveCaption
pixi install
```

### 3.4 Write the API key into `.env`

Copy `.env.example` to `.env` and fill in your key.

```
OPENAI_API_KEY=sk-...
```

**The value in `.env` wins over the environment variable of the same name.**
If you set the key in both places, `.env` is the one that is used.

### 3.5 Set up Zoom on the caption PC

- Set the display name to something like `Live Captions (EN)`. The name appears
  in the participant list, so make it clear what this PC is doing
- Speaker: **`CABLE Input`**
- Microphone: **muted**. This PC never speaks
- Turning on "Hide non-video participants" saves bandwidth

### 3.6 Check that the audio path works

This test needs no meeting and no API key. It plays a sine wave into
`CABLE Input` and reads it back from `CABLE Output`.

```bash
pixi run python scripts/cable_loopback.py
```

If the amplitude comes back, VB-CABLE is working.

```
--- what the app picks by default: 2 'CABLE Output (VB-Audio Virtual ' [MME] ---
  intervals 39, with sound 39 (100%)  peak 0.299  dropped 0
  => the path works
```

### 3.7 Put LiveCaption in the Start menu (optional)

Double-click **`InstallToStartMenu.bat`**. It adds a `LiveCaption` entry to the
Start menu of the current user, so you do not have to find this folder before a
meeting.

```
Windows key  ->  type "livecaption"  ->  Enter
```

To keep it in sight, find LiveCaption in the Start menu, right-click it, and
choose **Pin to Start** or **Pin to taskbar**.

No administrator rights are needed. The entry is one shortcut file, written to
`%APPDATA%\Microsoft\Windows\Start Menu\Programs\LiveCaption.lnk`.

**Run it again if you move or rename this folder.** The shortcut holds the full
path, so it stops working after the folder moves.

The icon comes from `etc/LiveCaption.ico`. To change it, edit
`scripts/make_icon.py`, run it, and register the shortcut again.

To take the entry out of the Start menu, run the batch file with `-Remove` from
a terminal:

```bash
InstallToStartMenu.bat -Remove
```

---

## 4. Run a meeting

### Step 1. Start the app

Double-click **`StartLiveCaption.bat`**, or pick **LiveCaption** in the Start menu
if you registered it in step 3.7. The control page opens in your browser.

```
http://localhost:8081     control page   <- do NOT screen-share this page
http://localhost:8080/v/<random>   viewer page    <- share this one
```

**Never screen-share the control page.** It shows the Zoom caption token.

**The control page is written in Japanese.** This manual gives the Japanese
label first, with an English translation after it.

**LiveCaption starts with caption generation stopped.** It takes in no audio and
calls no API until you press **start**. You can launch the app before you join
the meeting. Small talk during setup does not reach the recognition model.

### Step 2. Join the meeting from the caption PC

Set the Zoom speaker to `CABLE Input` and mute the microphone.

### Step 3. Check the input device

On the control page, look at **音声の入力** (audio input). It should be
`CABLE Output`. If it is not, pick the right one from the list. **The change
takes effect as soon as you pick it.** There is no apply button.
Press **一覧を更新** (refresh the list) if you plugged in a device just now.

The list shows the host API as well as the name, because Windows shows
`CABLE Output` three times: once for MME, once for DirectSound, once for WASAPI.

```
2: CABLE Output (VB-Audio Virtual (MME, 16 ch)
```

Any of the three works. MME is the default and is fine.

### Step 4. Start caption generation

Press **開始** (start) under **字幕の生成** (caption generation).

**Watch the level meter.** If nobody has spoken yet, the meter does not move.
Ask someone to speak. If the meter stays flat while a person is speaking, the
input device is wrong. Pick another one. You can change the device while
captions are being generated.

### Step 5. Show the captions

Pick one or more of the three ways below.

#### A. Zoom caption API (needs host or co-host rights)

The token can only be created during the meeting, and only by the host or a
co-host.

1. The host clicks the arrow next to "Captions" in the toolbar, then
   **"Copy the API token"**
2. The host sends the token to the caption PC through the meeting chat
3. On the control page, paste the token into **APIトークン** under
   **Zoom字幕** (Zoom captions) and press **登録** (register).
   The input field is masked, and it clears after you register
4. Press **開始** (start). Three warm-up captions are sent first

**Turn off Zoom's own automatic captions.** Zoom's automatic captions and
LiveCaption's captions share the same four-line window. If the automatic
captions keep running, they push LiveCaption's lines out of the window.

**Watch the status in the top right of the control page.**

| Display | Meaning |
|---|---|
| `Zoom: 未登録` | No token yet |
| `Zoom: 停止中` | Token registered. Press 開始 to send |
| `Zoom: 送信中` | Captions are being sent |
| `Zoom: 送信中（失敗 3）` | **Sending is failing.** The token expired, or the meeting changed |
| `生成: 停止中` | **No captions are being made.** Nothing is sent, even if Zoom is set to sending |
| `生成: 中` | Captions are being made |

**Do not miss the failure count.** Zoom answers a wrong token with a non-200
status and no explanation. Get a new token and register it again.

#### B. Screen share

This needs no host rights, and the captions never leave your network.

1. On the control page, press **閲覧画面を開く** (open the viewer page) under
   **画面共有で見せる** (show by screen share)
2. Press **F11** to make the viewer page full screen
3. Share that browser window in Zoom

**Screen sharing compresses the picture.** Small text breaks up. After you start
sharing, check on a second device that the text is readable. If it is not,
show fewer lines (`config.WEB_LINES`).

#### C. Hand out a URL

This needs no host rights either. It opens a temporary tunnel through
Cloudflare and gives you a public URL and a QR code.

1. On the control page, press **トンネルを開始** (start the tunnel) under
   **参加者への配信** (delivery to participants). The status shows
   `配信: 起動中…` and then `配信: 中`
2. Show the QR code to the people who want captions, or send them the URL
3. They open the URL on their own phone or laptop

**The tunnel is off by default.** Captions travel through Cloudflare, so do not
use it for meetings that discuss unpublished results. Use screen share instead.

### Step 6. Tell the audience

Say this at the start of the meeting, to the people who will read the captions:

- Turn on "Show Captions" in Zoom
- **Drag the caption area to make it taller.** The default is four lines. Four
  lines is not enough room to read a translation

### Step 7. End the meeting

Press **終了** (quit) under **アプリの終了** (quit the app) on the control page.
The browser tab and the terminal window both close.

---

## 5. The meeting record

**LiveCaption saves a record of every meeting by default.** You do not have to
turn it on.

The files go to **your Downloads folder**.

```
Downloads/live-caption_2026-09-08_143012.jsonl   appended one sentence at a time
Downloads/live-caption_2026-09-08_143012.md      readable form, written at exit
```

Each entry pairs the Japanese text from the recogniser with the English caption.
The timestamp is the time the recognition became final.

The `.jsonl` also carries measured timings. They do not appear in the `.md`, but
you need them to tune the delay later.

| Field | Meaning |
|---|---|
| `cut` | Why the sentence was finalised: `punct` (end mark), `force` (length), `idle` (silence), `flush` (at exit) |
| `waited` | Seconds actually waited, when the sentence was finalised by silence |
| `took` | Seconds the translation took |
| `total` | **Seconds from finalisation to the first caption.** `total − took` is the queue |
| `spec` | Present when a speculative translation was used. Then `total` is smaller than `took` |

- To read the record during the meeting, press **途中まで読む** (read so far)
  under **会議の記録** (meeting record) on the control page
- **The `.md` file is written when the app exits.** If the PC loses power,
  rebuild the `.md` from the `.jsonl` with
  `pixi run python scripts/transcript_to_md.py`
- To keep no record, start the app with `--no-save`
- To put the files somewhere else, use `--save-dir`
- If no sentence was produced, no file is written

---

## 6. The glossary

The tables in `etc/glossary/` decide how well the technical terms come out.
**These files are the main thing you maintain.**

### Pick the tables for the meeting

**There is one file per subject, and you choose which ones to use.** They live
in `etc/glossary/`, not in `docs/`: they are data the app reads, not something
to read yourself.

| File | Terms | When to use it |
|---|---|---|
| `KAGRA_basic.tsv` | 23 | **Always.** People, organisations, the site, vibration isolation, vacuum, cryogenics |
| `Interferometer.tsv` | 45 | Meetings about mirrors, optics, or the length degrees of freedom |
| `LVK.tsv` | 0 | Empty. For LIGO-Virgo-KAGRA terms, when you need them |

`KAGRA_basic` alone sends 41 words to the recogniser. With `Interferometer` it
sends 124. The limit is 200.

On the control page, the **用語集** (glossary) row is folded. It shows what is
in use right now: the names and the total number of terms. Click it to open the
list, then tick the tables you want. **The change takes effect as soon as you
tick a box.**

The list scrolls inside its own box, so it never pushes the rest of the settings
off the screen however many tables you have. Once there are 8 or more tables, a
search box appears above the list. **A table you have ticked stays visible even
when the search hides the others**, so you can always see and untick what is in
use. **全部選ぶ** (select all) and **全部外す** (clear all) change every table at
once. Both act on every table, including the ones the search box is hiding. If captions are being
generated, the recogniser reconnects so that the new keywords reach it.

The row under the boxes shows the total number of terms and how many words go to
the recogniser. **Watch the limit.** Words past `config.ASR_KEYWORD_LIMIT` never
reach the recogniser, and the page says so in red when that happens. If you see
it, tick fewer tables.

**Your choice is remembered.** The next start uses the same combination. It is
printed at start-up and shown on the control page, so you can see what is in use.

You can also choose at start-up:

```bash
pixi run caption --web --glossary KAGRA_basic Interferometer
```

**When the same Japanese term is in two tables, the two entries are merged.**
The English comes from the first table that has it, and the misrecognitions from
the third column are collected from every table. If two tables give different
English for the same term, the app prints a warning and uses the first one.

### The file format

One term per line, three columns separated by tabs:

```
Japanese (correct spelling)  <TAB>  English  <TAB>  common misrecognitions (comma separated)
```

The glossary is used in two places:

1. **Recognition.** The Japanese and English spellings are passed to the
   recogniser as keywords, so it is more likely to hear the term correctly
2. **Translation.** The whole table is put into the translation prompt, together
   with replacement rules built from the third column

**The third column is the important one.** The recogniser will still make
mistakes. When you write down the mistake it actually makes, the translation
step can recover the correct term from it.

How to grow the table after a meeting:

1. Open `live-caption_*.md` in your Downloads folder. The header says which
   tables were used
2. Look for terms that came out wrong in the English
3. Add the wrong Japanese text to the third column of that term's line, in
   whichever table holds that term
4. **Add English misrecognitions too.** For example "people" for `p-pol`
5. **Add forms where the end of the previous word is stuck to the front.**
   For example `無観測系` for `干渉計`: the leading `む` belongs to the word before

**Do not guess.** If a word makes no sense, ask the person who was speaking.
"people parkour" turned out to be `p-pol`. Nobody could have worked that out
from the sound alone.

**Check that a term you added is really there.** On 2026-09-08 the line for
`牛場` had no `Woza` in it, although a note said it did. Run the term through
the translation step and confirm.

**Put a new term in the table where it belongs.** A term used in every KAGRA
meeting goes in `KAGRA_basic`. A term only a mirror-control meeting uses goes in
`Interferometer`. Keeping them apart is what lets you leave out the words a
meeting does not need.

---

## 7. Command line reference

The batch file is the normal way to start. These commands are for everything else.

```bash
StartLiveCaption.bat                             # what you use for a meeting
InstallToStartMenu.bat                           # put LiveCaption in the Start menu (once)

pixi run web                                     # the same thing, from a terminal
pixi run caption --web                           # the same thing again
pixi run caption --token "<URL>" --web           # Zoom captions and browser captions
pixi run caption --token "<URL>"                 # Zoom captions only
pixi run caption --web --no-save                 # keep no record
pixi run caption --check-audio 20                # show the input level only (no API)
pixi run devices                                 # list the input devices
pixi run caption --from-file recording.wav --dry-run   # replay a WAV file in real time
pixi run python scripts/transcript_to_md.py      # rebuild the .md after a crash
pixi run python scripts/cable_loopback.py        # test the VB-CABLE path
```

Options:

| Option | Meaning |
|---|---|
| `--token <URL>` | The Zoom caption API token. You can also paste it into the control page later |
| `--device <name>` | Part of the input device name. Default: `CABLE Output` |
| `--web [port]` | Show captions in a browser. Viewer port, default 8080 |
| `--control-port <port>` | Control page port, default 8081. **Always listens on 127.0.0.1 only** |
| `--web-bind <address>` | Address the **viewer** page listens on, default 127.0.0.1. Use 0.0.0.0 to let other machines on the LAN open it. The control page ignores this |
| `--tunnel` | Open the tunnel at start-up. Off by default |
| `--no-browser` | Do not open the browser automatically |
| `--no-save` | Keep no record of the meeting |
| `--save-dir <folder>` | Where to write the record. Default: your Downloads folder |
| `--dry-run` | Do not send to Zoom. Print to the screen only |
| `--from-file <wav>` | Read a WAV file in real time instead of a device. 24 kHz mono |
| `--loop` | Repeat the file given by `--from-file` |
| `--delay <level>` | Recognition delay and accuracy. `minimal`, `low`, `medium`, `high`, `xhigh`. Default `low` |
| `--model <name>` | Translation model. Default `gpt-4.1-mini` |
| `--glossary <name> ...` | Which tables in `etc/glossary/` to use. Several can be given. Default: the combination you chose last |
| `--check-audio [sec]` | Show the input level and exit. Calls no API |
| `--list-devices` | List the input devices |
| `--cloudflared <path>` | Where `cloudflared` is. Not needed if it is on PATH or in `local/bin` |

**If a port is taken, change both numbers.** Port 8081 belongs to the control
page, so never give 8081 to the viewer page.

```bash
pixi run caption --web 8090 --control-port 8091
```

---

## 8. Settings

These live in `src/live_caption/config.py`. The default values come from
measurements, so change them only when you have a reason.

**The four below can be changed from the control page and from `.env`,** so you
never have to edit the code.

### From the control page

Press the **遅延の調整** (delay tuning) row to open it. **It is folded, because
these are not settings you change often.**

- Edit a number and leave the field. **It takes effect at once. No restart.**
- **`.env` に保存** (save to .env) keeps the value for the next start. The other
  lines in `.env`, including `OPENAI_API_KEY`, are left alone
- **既定に戻す** (restore defaults) puts back the measured values
- When a value differs from its default, the folded row says how many

### In `.env`

`.env.example` shows how to write them.

| Name in `.env` | Setting it changes |
|---|---|
| `LIVECAPTION_IDLE_FLUSH_SEC` | `IDLE_FLUSH_SEC` |
| `LIVECAPTION_SPECULATE_AFTER_SEC` | `SPECULATE_AFTER_SEC` |
| `LIVECAPTION_FORCE_CUT_CHARS` | `FORCE_CUT_CHARS` |
| `LIVECAPTION_LINE_INTERVAL_SEC` | `LINE_INTERVAL_SEC` |

When a value is replaced, the app prints `[設定] .env で差し替えた: …` at start-up.

**If you change `IDLE_FLUSH_SEC` alone, `SPECULATE_AFTER_SEC` follows it
automatically** (`IDLE_FLUSH_SEC − 1.1` seconds). Without that, the speculative
translation would fire too early and only waste calls.

**A typo cannot stop a meeting.** A value that is not a number, or is too small,
is reported at start-up and the default is used. It is **not** ignored silently,
so read the start-up messages.

| Setting | Default | What it does |
|---|---|---|
| `ASR_MODEL` | `gpt-live-transcribe` | The speech recognition model |
| `ASR_DELAY` | `low` | `minimal` and `high` both missed technical terms |
| `ASR_LANGUAGES` | `("ja", "en")` | The KAGRA morning meeting is English first, then Japanese. Do not fix it to one language |
| `ASR_KEYWORD_LIMIT` | 200 | How many glossary words reach the recogniser. **Keep this above the number of words in your table.** The app warns at start-up if words are cut |
| `TRANSLATE_MODEL` | `gpt-4.1-mini` | The translation model |
| `CONTEXT_SENTENCES` | 3 | How many earlier sentences are given to the translator as context |
| `MAX_CAPTION_CHARS` | 80 | Longest English line sent to Zoom |
| `FORCE_CUT_CHARS` | 70 | A Japanese sentence longer than this is cut. Lower it if the captions go by too fast |
| `IDLE_FLUSH_SEC` | 2.5 | Seconds of silence after which a sentence is finalised even without an end mark. **Measure the delta intervals with `stream_test.py` before lowering it.** A value below the interval seen during continuous speech cuts the speaker off mid-sentence |
| `IDLE_POLL_SEC` | 0.1 | How often the timer above is checked. A finer value wastes less time |
| `SPECULATE_AFTER_SEC` | 1.4 | After this much silence, the translation is sent without waiting for the sentence to be finalised. When it matches, the caption appears about 0.9 s sooner; when it does not, the call is thrown away and costs money. `0` turns it off |
| `LINE_INTERVAL_SEC` | 0.6 | Gap between lines sent to Zoom. The caption area shows as few as 4 lines, so sending them at once pushes the first line out. **It does not apply to the viewer page**, which gets every line at once |
| `WEB_LINES` | 8 | Lines shown on the viewer page |
| `WEB_PORT` | 8080 | Viewer page |
| `CONTROL_PORT` | 8081 | Control page |

---

## 9. Troubleshooting

| Symptom | Where to look |
|---|---|
| Nothing happens. No log lines | **Did you press 開始 under 字幕の生成?** Launching the app is not enough |
| `CABLE Output` is not in the list | Is VB-CABLE installed? Did you restart the PC? |
| The level meter does not move | The Zoom speaker setting. Both devices at 48000 Hz. **Is 音声の入力 set to `CABLE Output`?** |
| "cannot open the input" | Another app may have the device. Does the device accept 48000 Hz? Pick a different input |
| No recognition lines | `OPENAI_API_KEY` in `.env`. The network |
| Recognition lines but no caption lines | A translation error should be on the screen |
| Log lines flow, but nobody sees the captions | **Are you looking at the host's screen?** The host never sees them. Did the other person turn on "Show Captions"? Is the token from this meeting? |
| Different captions appear when someone speaks | **Zoom's automatic captions are running.** Turn them off |
| A term is in the glossary but still comes out wrong | It may be cut by `ASR_KEYWORD_LIMIT`. Words at the end of the table do not reach the recogniser |
| Captions stopped part way through | The `seq` number went backwards. If you restarted the app, check `local/seq_state.json` |
| Captions go by too fast to read | Ask the readers to make the caption area taller. Lower `config.FORCE_CUT_CHARS` |
| The recogniser reconnects again and again | The network. Turn off incoming video in Zoom on the caption PC. Try another line |

**Check the receiving side first.** A successful send is not proof that anything
is displayed. On 2026-09-07 every send returned HTTP 200 and nothing appeared on
screen. The problem was on the receiving side.

**Captions do not disappear on a timer.** A line stays until newer lines push it
out. If you missed one, look again instead of sending it a second time.

---

## Related documents

- [test-procedure.md](test-procedure.md) — the staged test used to bring up a
  new caption PC, and the checklist for the day of the meeting
- [../etc/glossary/](../etc/glossary/) — the term tables, one file per subject
