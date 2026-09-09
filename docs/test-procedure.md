# Bring-up test procedure

This document has four stages. **Finish each stage before you start the next
one.** If you run everything at once and it fails, you cannot tell whether the
problem is in the audio, the recognition, or the sending.

Stages 1 and 2 need no meeting. From stage 3 on, you need a meeting.

For everyday use, read [manual.md](manual.md) instead. This document is for
bringing up a new caption PC and for the day of an important meeting.

---

## Preparation, on the caption PC

### 1. Install VB-CABLE

Download it from [vb-audio.com/Cable/](https://vb-audio.com/Cable/).
**Run the installer as administrator, then restart the PC.**

After the restart, check these two devices in the Windows sound settings.

| Device | Format |
|---|---|
| `CABLE Input` (playback) | **48000 Hz**, 16 bit |
| `CABLE Output` (recording) | **48000 Hz**, 16 bit |

**Set both to 48000 Hz.** If the two rates differ, Windows resamples the audio
and the sound breaks up. This app opens the device at 48000 Hz and converts the
audio to 24000 Hz itself.

### 2. Zoom settings on the caption PC

- A free Zoom account is enough
- **Set the display name to something like `Live Captions (EN)`.** The name
  appears in the participant list, so make it clear what this PC is doing
- Speaker: **`CABLE Input`**
- Microphone: **muted**. This PC never speaks
- Turning on "Hide non-video participants" saves bandwidth

### 3. Meeting settings, done by the host

**Turn off Zoom's automatic captions, including translated captions.**

Zoom's automatic captions and our captions share the same four-line window.
If the automatic captions keep running, our lines are pushed out and cannot be
read.

Host toolbar, then "Captions", then the arrow, then stop the automatic captions
and the live transcript.

### 4. Put the project on the PC

```bash
cd C:\Users\Yoichi\Dropbox\src\projects\LiveCaption
pixi install
```

Check that `OPENAI_API_KEY` is set in `.env`. **The value in `.env` wins over the
environment variable of the same name.**

---

## Stage 1. Does the audio arrive? (no meeting, no API)

**If this stage fails, everything after it is wasted effort.**

```bash
pixi run devices
```

Check that `CABLE Output` appears in the list. If it does not, suspect the
VB-CABLE installation or the restart.

**The same name appears three times: once for MME, once for DirectSound, once
for WASAPI.** Look at the number and the host API, not only the name. By default
the app takes the first match, which is MME. That is fine.

**You can also pick the device from the control page.** If you started with
`--web`, the list is on the **音声の入力** (audio input) row. While captions are being
generated, the level meter moves there too, so you do not need `--check-audio`.

**When you have no meeting and no sound source, you can still test the wiring.**
This test plays a sine wave into `CABLE Input` and reads it back from
`CABLE Output`.

```bash
pixi run python scripts/cable_loopback.py
```

If the amplitude comes back, VB-CABLE is working. **Use this first whenever you
suspect the wiring.**

Next, play sound from something other than Zoom (a video, for example; point the
Windows output at `CABLE Input` for a moment):

```bash
pixi run caption --check-audio 20
```

This calls no API, so it needs no key and no meeting.

**What to look at:** the meter moves, and the last line says the sound is
arriving.

If it says the input is almost silent, check these three in order.

1. Is the app that plays the sound sending it to `CABLE Input`?
2. Is `--device` picking up `CABLE Output`? (That is the default.)
3. Are both devices at 48000 Hz?

---

## Stage 2. Recognition and translation (needs a meeting, no token)

Put the caption PC into a Zoom meeting and **set the Zoom speaker to
`CABLE Input`**. Speak Japanese from another device: the host PC or a phone.

```bash
pixi run caption --dry-run
```

`--dry-run` sends nothing to Zoom. It only prints to the screen. No token is
needed.

**What to look at:**

- A recognition line appears a few seconds after someone speaks
- A caption line appears 1 to 2 seconds after that
- The technical terms come out correctly

**Run it for about five minutes and note the terms that came out wrong.** Add
those misrecognitions to the third column of the right table in
`etc/glossary/`. This is what decides the quality on the day.

**Check which tables are ticked under 用語集 (glossary) on the control page
before you start.** A meeting about mirror control needs `Interferometer`; the
morning meeting may not.

**You do not have to take notes during the meeting.** The record is saved in
your Downloads folder. The Japanese text and the English caption are paired, so
you can collect the misrecognitions afterwards. The `.md` file is easier to
read. **If a word makes no sense, ask the speaker. Do not guess.**

---

## Stage 3. Does the caption API work? (tested on its own)

Open a test meeting as the host, on a machine **other than** the caption PC.

1. Toolbar, "Captions", the arrow, "Manual captions setup",
   then **"Copy the API token"**
2. Join the same meeting from a second device and turn on "Show Captions"

```bash
pixi run python scripts/zoom_cc_test.py --auto "<the token URL you copied>"
```

**What to look at:** English text appears on the second device, and the failure
count is zero.

**Check the receiving side first.** The numbers on the sending side (HTTP 200,
zero failures) **are not proof that anything is displayed.** In the test on
2026-09-07 every send returned 200 and nothing appeared. We suspected `seq` and
went down the wrong path. The problem was on the receiving side.

Do it in this order.

1. Turn on "Show Captions" **on a device that is not the host**.
   **The host never sees the captions.**
2. Check that the meeting's automatic captions are off
3. Then send

**Captions do not disappear on a timer.** If you miss one, look again: it is
still there. A line goes away only when newer lines push it out. Look at the
screen calmly instead of sending the same line again.

**Note:** `seq` must increase over the whole meeting session. If you run a
second time in the same meeting, the app continues from `local/seq_state.json`.
If you open a different meeting, get a new token.

---

## Stage 4. End to end (the same setup as the real meeting)

Do this only after stages 1 to 3 have all passed.

```bash
pixi run caption --token "<the token URL you copied>"
```

At start-up, three warm-up captions are sent.
**The receiving side cannot turn on "Show Captions" until captions start
flowing.** So the first few captions reach nobody. That is expected.

**What to look at:**

| Item | Expected |
|---|---|
| The log on screen | Recognition and caption lines keep coming |
| Send failures | 0 |
| Dropped audio | Does not increase |
| Recognition reconnects | 0. If it grows, suspect the network |
| The receiving side | Readable speed. Lines are not scrolling away |

**The host's Zoom window shows no captions.** The log on this screen is your
only way to tell whether it is working.

Press Ctrl+C to stop. A summary is printed.

### The meeting record

**It is saved by default.** You do not have to turn it on.

The files go to **your Downloads folder**.

```
Downloads/live-caption_2026-09-08_143012.jsonl   appended one sentence at a time
Downloads/live-caption_2026-09-08_143012.md      readable form, written at exit
```

The names start with `live-caption_`. The Downloads folder holds many other
files, so a date alone would not tell you what the file is. Use `--save-dir` to
put it somewhere else.

The Japanese text and the English caption are paired. The timestamp is the time
the recognition became final.

- To read the record during the meeting, press **途中まで読む** under
  **会議の記録** on the control page
- **The `.md` file is written at exit.** If the PC loses power, rebuild it from
  the `.jsonl` with `pixi run python scripts/transcript_to_md.py`
- To keep no record, start with `--no-save`
- If no sentence was produced, no file is written

---

## Using it in a meeting you do not host (browser captions)

Only a host or a co-host can create a caption API token. In a meeting you do not
host, show the captions in a browser and share that window.

```bash
pixi run caption --web
```

**With `--web`, caption generation starts stopped.** No audio is taken in and no
API is called until you press start on the control page. You can launch the app
before you join the meeting.

The control page has two columns. **Captions on the left, settings on the
right.** Drag the vertical line between them to change the width.
Double-click it to go back to the default. The width is remembered.

1. Starting the app opens the **control page**
   (`http://localhost:8081`) in the browser
2. Check that **音声の入力** (audio input) is `CABLE Output`. If it is not, pick
   the right one from the list. **The change takes effect as soon as you pick it**
3. Press **開始** (start) under **字幕の生成** (caption generation).
   **Check that the level meter moves**
4. Press **閲覧画面を開く** (open the viewer page) under **画面共有で見せる**
   (show by screen share), then press **F11** for full screen
5. Share that browser window in Zoom

The viewer page has a path you cannot guess (`/v/<random>`).
**`http://localhost:8080/` alone returns 404.** Use the URL shown on the control
page.

### Sending Zoom captions from the browser

**You can enter the token after start-up.** The token does not exist until the
meeting starts, so it is fine to launch without `--token`.

1. Press the gear icon in the top right to open the control page
2. **Stop screen sharing for a moment**, so the token is not shown to everyone
3. Paste the token into **APIトークン** under **Zoom字幕** (Zoom captions) and
   press **登録** (register). The field is masked, and it clears after you register
4. Press **開始** (start). Three warm-up captions are sent
5. Start screen sharing again

Press **停止** (stop) to stop. You can caption only part of a meeting.

**Watch the status in the top right.**

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

**Check all of these:**

- The top right says `生成: 中`
- The meter under **音声の入力** moves. If it says no sound is arriving, the
  input is wrong
- The dot in the top left is green, which means the browser is connected to the
  app
- Lines stack up from the bottom
- The toggle in the top right shows and hides the Japanese text

**Screen sharing compresses the picture.** Small text breaks up. After you start
sharing, check on a second device that the text is readable. If it is not, show
fewer lines (`config.WEB_LINES`).

You can use the Zoom caption API at the same time.

```bash
pixi run caption --token "<URL>" --web
```

If a port is taken, change the numbers. **Give both numbers.** Port 8081 belongs
to the control page, so never give 8081 to the viewer page.

```bash
pixi run caption --web 8090 --control-port 8091
```

---

## On the day

0. You can start `StartLiveCaption.bat` before the meeting.
   **It takes in nothing until you press start**, so small talk during setup
   does not reach the recognition model
1. Join the meeting from the caption PC (speaker `CABLE Input`, microphone muted)
2. Run `pixi run caption --check-audio 10` and confirm the sound arrives
3. The host copies the API token and sends it to the caption PC in the chat
4. Check three things on the control page: **音声の入力** (audio input) is
   `CABLE Output`, the right tables are ticked under **用語集** (glossary) for
   this meeting, and **字幕の向き** (direction) matches the language of the
   meeting — 日本語 → 英語 for a Japanese meeting, 英語 → 日本語 for an English one
5. Press **開始** under **字幕の生成**. Audio starts coming in here.
   **Watch the meter move.** If it does not, pick another input. You can change
   the device while captions are being generated
6. Show the captions. Either way works:
   - `pixi run caption --token "<URL>"` (pass it on the command line)
   - Start with `pixi run caption --web`, then **register the token on the
     control page and press start**. This way you can stop and restart during
     the meeting
7. At the start of the meeting, tell the people who will read the captions:
   - Turn on "Show Captions"
   - **Drag the caption area to make it taller.** Four lines is not enough room
     to read
8. When it is over, press **終了** under **アプリの終了** on the control page. **The tab and the
   terminal window both close**
9. After the meeting, the record is in your Downloads folder as
   `live-caption_*.md`. **Collect the misrecognitions and add them to the third
   column of the right table in `etc/glossary/`.** The next meeting will be
   better. The header of the `.md` says which tables were used

---

## Working out what is wrong

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
| A term is in the glossary but the recogniser still misses it | It may be cut by `config.ASR_KEYWORD_LIMIT`. Words at the end of the table do not reach the recogniser |
| Captions stopped part way through | The `seq` number went backwards. If you restarted the app, check `local/seq_state.json` |
| A term comes out wrong | Add the misrecognition you actually saw to the third column of the right table in `etc/glossary/` |
| A whole subject's terms come out wrong | The table for that subject may not be ticked under 用語集 on the control page |
| Captions go by too fast to read | Ask the readers to make the caption area taller. Lower `config.FORCE_CUT_CHARS` |
| The recogniser reconnects again and again | The network. Turn off incoming video in Zoom on the caption PC. Try another line |

**If a word makes no sense, do not guess and do not put your guess in the
glossary.** "people parkour" turned out to be `p-pol`. Nobody could have worked
that out from the sound alone. Ask the person who was speaking.
