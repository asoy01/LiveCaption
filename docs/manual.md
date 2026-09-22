# LiveCaption Manual

日本語版は `manual.ja.md` にあります。ウェブ版では、上の言語の切り替えから
読めます。

LiveCaption shows real-time captions for a meeting. It translates between
Japanese and English. You choose the direction for each meeting.

- **English captions for a meeting held in Japanese**, for people who do not
  speak Japanese
- **Japanese captions for a meeting held in English**, for people who find it
  hard to follow spoken English

LiveCaption joins the meeting as a **silent participant** and turns what it
hears into captions. You join the same meeting as usual and speak.

**How to read this document**

| What you want | Where to read |
|---|---|
| Getting it running | Sections 1 and 2. **One Windows PC with Docker Desktop** |
| What to do on the day of a meeting | Section 3 |
| Letting it run with nobody watching | Section 4 |
| Glossary, meeting record, settings, problems | Sections 5 to 8 |
| Running it on Linux | Appendix A (only what differs from section 2) |
| The Tailscale account and its settings | Appendix B |
| Updating, VNC, maintenance commands | Appendix C |
| Windows native, the older way without Docker | Appendix D |
| How it works, and the detailed settings | Appendix E |

There are three ways to run it. **The main text of this document describes the
easiest one, Windows + Docker.**

| Setup | What you need | When to use it |
|---|---|---|
| **Windows + Docker** | One Windows PC and Docker Desktop | **One laptop is enough.** Start it for each meeting |
| **Linux + Docker** | One Linux machine and Docker | Leave it on an always-on machine and let it join scheduled meetings on its own (Appendix A) |
| **Windows native** | **A second Windows PC** and VB-CABLE | Captions for meeting software other than Zoom. Frozen: no new features go into it (Appendix D) |

Under Docker, the meeting software (Zoom), the audio devices and the screen are
all inside the container. Docker is the only thing you install on the machine.
**Running a meeting is the same in all three ways.** The control page, the
schedule, the glossary and the meeting record do not differ.

---

## 1. What you need

| Item | Notes |
|---|---|
| One Windows PC | An Intel or AMD CPU. **It does not run on an Apple Silicon Mac**, because Zoom publishes no arm64 build for Linux. 16 GB of memory or more is recommended |
| Docker Desktop | You install it in 2.1. It uses WSL2 |
| An OpenAI API key | Used for both transcription and translation. Create one at [platform.openai.com](https://platform.openai.com) |
| A Tailscale account | Used to reach the control page, and to give the viewer URL to people. The free tier is enough. **Appendix B.1 covers how to create one** |
| A Zoom meeting | LiveCaption itself needs no Zoom account. It joins without signing in, unless the meeting is set to "authenticated users only" (4.6) |

**To put captions in the Zoom caption area, you need the meeting host or a
co-host to help you** (3.4 A). The other ways of showing captions, screen share
and giving people a URL, need no rights at all.

Transcription costs **$0.017 per minute**, so about one US dollar per hour.
Translation costs much less. Silence is billed too, so press Stop for a long
break (3.3).

---

## 2. Install (Windows + Docker)

You do this once. It takes about thirty minutes, not counting downloads.

**Type the commands in PowerShell.** Open it by typing "PowerShell" in the Start
menu. On Linux you type the same commands in a shell (Appendix A).

**There are three kinds of box below. The sentence in front of each box tells
you which kind it is.**

| The sentence in front | What is in the box |
|---|---|
| "Run this in PowerShell" | A command you type |
| "Write this in `<filename>`" | Text you put in that file with an editor. Not a command |
| Anything else | Something the program prints, or a URL you open. You never type it |

**In this section, the PC that runs Docker is called the host.** That is
Docker's own term. From section 3 on, the host means the host of the Zoom
meeting.

### 2.1 Install Docker Desktop

Get Docker Desktop from
[docker.com](https://www.docker.com/products/docker-desktop/). The installer
turns WSL2 on for you. Restart the PC if it asks you to.

Start Docker Desktop and wait until the corner of its window says "Engine
running". Run this in PowerShell to check it as well.

```powershell
docker version
```

**16 GB of memory may not be enough.** The container itself uses about 220 MB,
but WSL2 reserves memory as well. If the machine runs out of memory, write this
in `%USERPROFILE%\.wslconfig` (create the file if it is not there).

```ini
[wsl2]
memory=8GB
```

### 2.2 Download LiveCaption

Download the ZIP from GitHub and unpack it.

1. Open [github.com/asoy01/LiveCaption](https://github.com/asoy01/LiveCaption),
   press the green **Code** button, then **Download ZIP**. The direct link is
   [main.zip](https://github.com/asoy01/LiveCaption/archive/refs/heads/main.zip)
2. Unpack it. You get a folder called `LiveCaption-main`. **The LiveCaption
   folder is the one with `compose.yml` directly inside it** (some unpackers
   create two nested folders with the same name)
3. Put that folder somewhere without spaces or non-English characters in the
   path, for example `C:\LiveCaption`. You can rename the folder

Every command below runs inside the LiveCaption folder. Run this in PowerShell
to go there.

```powershell
cd C:\LiveCaption
```

To fetch it with Git instead, so that you can update with `git pull`, see
Appendix C.1.

### 2.3 Create `.env` (the API key)

Every setting goes in one file called `.env`. Run this in PowerShell to copy the
sample and open it in Notepad.

```powershell
copy .env.example .env
notepad .env
```

In Notepad, write this in `.env`. Replace `sk-...` with your own key and save
the file.

```ini
OPENAI_API_KEY=sk-...
```

**On a laptop you start only for a meeting, write one more line in `.env`.**

```ini
LIVECAPTION_RESTART=no
```

Without that line, LiveCaption starts every time Docker Desktop starts, and
joins any meeting you have scheduled. On a machine that stays powered on, leave
the line out.

**The file is called `.env`, not `.env.txt`.** The `copy` command above gets the
name right. Leave the other settings alone for now (section 7).

### 2.4 Start it

Run this in PowerShell.

```powershell
docker compose up -d --build
```

The first run builds the image and takes about ten minutes. After that it takes
seconds.

To watch it start, run this in PowerShell.

```powershell
docker compose logs -f
```

The first time, it prints a Tailscale URL and waits. **Go on to 2.5.**

```
Tailscale:  TS_AUTHKEY is not set. Open the URL below to connect by hand.

To authenticate, visit:

	https://login.tailscale.com/a/110e718b012a76

Tailscale:  No address yet. LiveCaption keeps trying in the background.
```

Press Ctrl+C to stop watching the log. LiveCaption keeps running.

### 2.5 Approve the container on Tailscale

**The LiveCaption container is a machine on your Tailscale network (your
tailnet).** The control page can only be opened from inside that tailnet, and
the viewer URL is delivered through Tailscale Funnel.

**Do step 2 before step 3.** In the other order the control page gets no https
URL (run `docker compose restart` if that happens).

1. Open the Tailscale admin console
   ([login.tailscale.com/admin](https://login.tailscale.com/admin)). **If you
   have no account, create one first: see Appendix B.1.** Changing which account
   a tailnet belongs to is difficult later
2. In the admin console, turn on **DNS** → **HTTPS Certificates** →
   **Enable HTTPS**. Both the https control page and the viewer delivery need
   it. **You do this once per tailnet**
3. Open the URL from the log in 2.4 and approve the machine with your Tailscale
   account. **You do this once.** The state is kept in a Docker volume
4. `livecaption` appears under **Machines** in the admin console. Open the "..."
   menu on the right and choose **Disable key expiry**. Without this the
   container disconnects from the tailnet after 180 days and **the viewer URL
   stops working without warning**

**Install Tailscale on the machine you open the control page from, and sign in
with the same account.** That machine can be the host itself
([tailscale.com/download](https://tailscale.com/download)).

Once you approve it, the log shows the address and the control page URL.
LiveCaption is already running, so there is nothing to restart.

```
Tailscale:  100.x.x.x  (livecaption.tail1234.ts.net)
[10:24:31] control     also open over https: https://livecaption.tail1234.ts.net:8443
```

Appendix B covers using it on more than one machine, registering with an auth
key, Tailnet Lock, and tailnets with other people in them.

### 2.6 The control page URL

Open the control page in a browser on any machine on your tailnet.

```
https://livecaption.<your-tailnet>.ts.net:8443
```

`<your-tailnet>` is the name from the log in 2.5, something like `tail1234`.
**Open the full name exactly as the log printed it.** A short name or an IP
address does not match the certificate.

**Never screen-share the control page.** It shows the Zoom caption token. The
page you share is the viewer page (3.4 B).

### 2.7 Upload your glossary

Technical terms are translated correctly only if you give LiveCaption a
glossary.
**The ZIP has no tables in it.** The tables hold personal names and
organisation names, so they are not distributed with LiveCaption (section 5).

1. Open the sample `docs/glossary-example.tsv` in a text editor, replace the
   words with the ones your own meetings use, and save it under another name,
   for example `MyProject.tsv`. The format is in section 5
2. On the control page, open the **Setup** tab → **Glossary** → **Upload**, and
   send that file

The tables are kept in a Docker volume, so they survive recreating the
container. **You can start with the sample as it is and add to it later.**

### 2.8 Check that it works

You can test the whole path with a recording, without joining a meeting. You
need a 24 kHz mono WAV, for example a recording of a past meeting converted to
that format. Put it in a folder without spaces or non-English characters in the
path, such as `C:\samples`. The examples below call the file
`C:\samples\test.wav`.

Stop the running LiveCaption first, then run this in PowerShell. Type the second
command on **one line**.

```powershell
docker compose stop
docker compose run --rm -v "C:\samples:/samples:ro" engine --from-file /samples/test.wav --dry-run
docker compose start
```

If the transcript and the translation appear, your API key and your setup are
right. `--dry-run` sends nothing to Zoom.

If you have no recording, skip this check. You can confirm the same
thing in section 3 by joining a meeting of your own.

**The install is finished.**

---

## 3. Running a meeting

### 3.1 Open the control page

**If LiveCaption runs resident, there is nothing to start.** Open the control
page in a browser.

**If you set `LIVECAPTION_RESTART=no`,** start LiveCaption first. Start Docker
Desktop, then use either of these.

**From the Docker Desktop window.** Open **Containers** on the left and press
**Start** (the play button) on the `livecaption` row. **From the second time
on this is the easiest way**, and it needs no PowerShell.

**From PowerShell.** Go to the LiveCaption folder and run this in PowerShell.

```powershell
docker compose up -d
```

**If `livecaption` is not in the list, it has not been created yet.** Do the
install in section 2 first.

Then open the control page.

```
https://livecaption.<your-tailnet>.ts.net:8443
```

**LiveCaption starts with caption generation stopped.** Until you start caption
generation, no audio is read and no API is called, so you can start LiveCaption
before you join the meeting.

#### Reading the control page

Captions are on the left, controls on the right. Drag the divider to change the
width; double-click it to go back to the default. The right side has three tabs.

| Tab | What is in it | When you look at it |
|---|---|---|
| **This meeting** | Right now, the meeting being delivered, how people see it, meeting record | Every meeting |
| **Manage meetings** | Add, edit and delete schedules | Before a meeting |
| **Setup** | Audio input, glossary, delay tuning, VNC, restart | When you set the machine up |

- **Everything you use on the day is under This meeting.** You rarely open
  Setup once the machine is set up
- **Audio input** on the Setup tab says `pulse` under Docker. That is the
  virtual audio device inside the container, and you never change it
- **A red dot on the This meeting tab means something there has failed.** The
  dot appears so that you notice while another tab is open
- The three entries under "How people see it" are folded. Even folded, the right
  side of each row says `delivering` or `sending`
- The control page can be Japanese or English. Pick the language in the
  **日本語 / English** box in the header; the page reloads at once and the next
  start uses the same language. **The page opens in Japanese the first time, so
  switch it to English before you follow this manual.** Only the control page
  changes: the captions, the transcript and the meeting record are never
  translated, and the viewer page is in English already

### 3.2 Join the meeting

**LiveCaption joins Zoom by itself.**

1. On the **Manage meetings** tab, add a row and fill in the name and the Zoom
   invitation URL (`https://zoom.us/j/...`). **A personal link (`/my/...`) does
   not work**; the URL has to carry a meeting number
2. On the **This meeting** tab, select that meeting with the circle on the left
3. Press **Start this meeting now**. The line under the button says what will
   happen

```
Delivery -> join Zoom -> caption generation -> post to the chat
```

**"Start this meeting now" starts the delivery (the viewer URL), joins Zoom and
starts caption generation, all together.** It takes tens of seconds. The
progress is shown under **Right now**. To end it, press **Stop now**.

The level meter in 3.3 tells you whether LiveCaption joined. If it did not,
start VNC from the **Setup** tab and look at the Zoom window: you then see a
waiting room, a passcode prompt, or a sign-in prompt (Appendix C.3).

In a meeting where the host uses AI Companion, a dialog appears as LiveCaption
joins. **Leave it alone.** The captions work even if nobody answers it.

### 3.3 Caption direction, and starting

Look at **Caption direction** on the **This meeting** tab and choose it for each
meeting.

| Choice | What kind of meeting | Captions you get |
|---|---|---|
| 日本語 → 英語 | The meeting is held in Japanese | English |
| 英語 → 日本語 | The meeting is held in English | Japanese |

The change takes effect as soon as you pick it, and you can change it in the
middle of a meeting. **A sentence spoken in the caption language is shown as it
is, not translated**, so one choice covers a meeting that is English in the
first half and Japanese in the second. The next start uses the same direction.

If you used "Start this meeting now", caption generation is already running. If
you joined Zoom by hand, or you are starting again after a stop, press **Start**
at the top under **Right now**.

**Watch the level meter.** If the meter stays flat while a person is speaking,
no audio is arriving: either LiveCaption did not join Zoom, or it did not join
the meeting audio (section 8).

**Stop stops all three.** It closes caption generation, the delivery to
participants (the viewer URL), and the sending to the Zoom captions. It closes
all three so that you never believe you stopped while the captions keep going.
The viewer URL stops working, so Stop is not a button to press at every break.

### 3.4 How to show the captions

There are three ways, and you can use all three at the same time.

| Way | Host rights | What the reader does | Leaves your network |
|---|---|---|---|
| A. Zoom captions | **Required** | Turns manual captions on | Through Zoom |
| B. Screen share | Not required | Watches the viewer page you share | **No** |
| C. Give people a URL | Not required | Opens a URL on their own device | Through Tailscale |

#### A. Zoom captions (needs the host or a co-host to help)

This puts the captions in the Zoom caption area. The token can only be made
during a meeting, and only by a host or a co-host.

1. The host presses the arrow next to "Captions" in the toolbar, opens
   **"Manual captions setup"** and turns manual captions on. **"Copy the API
   token" does not appear until manual captions are on**
2. From the same place, the host chooses **"Copy the API token"**
3. You receive that token from the host, for example in the meeting chat
4. On the control page, open **How people see it** → **Into the Zoom captions**,
   paste the token and press **Register**. The field is masked, and it clears
   after you register
5. Press **Start** in that same box. This starts the sending to Zoom and is not
   the **Start** at the top of the page. Three warm-up captions go out first.
   **Nobody can turn manual captions on until captions start arriving**

To let the host paste the token instead of sending it to you, use the host URL
(4.5).

**Ask the host to turn Zoom's automatic captions off.** If they keep running,
LiveCaption's captions are pushed off the screen before people can read them.

If the top right shows something like `Zoom: sending (3 failed)`, the token has
expired or the meeting changed. Get a new token and register it again.

#### B. Screen share

Screen share needs no host rights, and the captions never leave your network.

1. On the control page, open **How people see it** → **Show it by screen share**
   → **Open the viewer page**
2. Press **F11** to make the viewer page full screen
3. Share that browser window from the PC you attend the meeting on

The viewer page opens by its tailnet name, so the PC you share from has to be on
the tailnet. **Screen sharing compresses the picture and small text becomes hard
to read.** Check on a second device that the text is readable.

#### C. Give people a URL

This needs no host rights. You give people the viewer URL and a QR code, and
they open it on their own phone or laptop. **The viewer URL belongs to the
meeting and never changes, so you can give it out in advance**: it appears as
soon as you add the meeting on the Manage meetings tab, and you can put it in
the invitation next to the Zoom link.

1. Select the meeting on the **This meeting** tab and open **How people see it**
   → **In a browser**
2. Press **Start delivering**. (If you used "Start this meeting now", it is
   already delivering.) The status shows `delivering`
3. Copy the URL, or press **Save the QR code** to get a PNG, and put it in the
   chat or the invitation. If you ticked the box on the Manage meetings tab,
   LiveCaption posts it to the Zoom chat by itself (4.4)

- **Only the selected meeting is delivered.** The other meetings' URLs do not
  open that day
- **A URL that never changes is also easier to leak.** Only the random text at
  the end protects it. If it leaks, delete that meeting and make a new one
- **Do not give people a URL for meetings whose content must not leave your
  organisation.** The captions pass through Tailscale's relay. Use screen share
  instead
- If Start delivering fails the first time, Funnel is not enabled yet. See
  Appendix B.6

### 3.5 Tell the audience

Say this at the start of the meeting, to the people who will read the captions:

- **Each person turns manual captions on** (for A). Nobody sees the captions
  until they do
- **Drag the caption area to make it taller.** The default is four lines, which
  is not enough room to read a translation

### 3.6 Ending

**You do not have to quit LiveCaption after a meeting.** Once you press "Stop
now" or "Stop", no API is called and nothing is billed. Leave it until the next
meeting.

On a laptop with `LIVECAPTION_RESTART=no`, stop LiveCaption when you are done.
**There are two ways to stop it, and they differ in how much work starting it
again takes.**

| How you stop it | The container | Starting it again |
|---|---|---|
| **Stop in Docker Desktop**, or `docker compose stop` | stays | **press Start in Docker Desktop** |
| `docker compose down` | is removed | `docker compose up -d` in PowerShell |

**Stop is enough for everyday use.** The settings, the list of meetings, the
records and the Tailscale registration are kept in the volumes, so either way
of stopping keeps them. **Do not use `down -v`**: that deletes the volumes too.

---

## 4. Scheduled meetings

**If you enter a schedule, LiveCaption runs at the set time on its own.** This
suits an always-on machine (Appendix A), but it works the same on a laptop as
long as you started it before the meeting.

It does four things by itself, the same four as "Start this meeting now" in 3.2:

1. Selects the meeting and starts delivering
2. Joins Zoom
3. Starts caption generation
4. Leaves and stops when the meeting is over

**The Zoom caption token is the one thing LiveCaption cannot get by itself**,
because only the host can create it, and only during the meeting. Section 4.5
covers how to receive it.

### 4.1 Entering a schedule

Fill in the meeting row on the **Manage meetings** tab.

| Field | Meaning |
|---|---|
| Start | When the meeting starts |
| Weekly | Repeat on the same weekday at the same time |
| Zoom | The invitation URL (`https://zoom.us/j/...`) or the meeting number. Empty: it does not join |
| Minutes before | Start delivering and join Zoom this many minutes early. Default 2 |
| Stop after silence | If no transcript appears for this many minutes, the meeting is treated as over. Default 10 |
| Hard cap | Stop after this many minutes even if sound continues. Default 180 |
| Start this meeting automatically | **Only meetings with this ticked run on their own** |

**Automatic is off by default.** Running it automatically sends captions out
with nobody watching. Do not tick it for meetings whose content must not leave
your organisation.

Weekly is the only kind of repeat.

### 4.2 Watching what it does

**Right now**, at the top of the **This meeting** tab, shows the state.

```
Right now
[Start] [Stop]  Stopped
Waiting
2026-09-25 10:00  Morning meeting    in 23 min
2026-09-27 13:00  Collaborators      in 2 d 3 h
[Stop now] [Skip the next one]
```

While a meeting runs, it shows the time left before the silence timeout and
before the hard cap.

- **A failure stays on screen until you dismiss it.** With nobody watching, a
  failure that disappears by itself is a failure nobody sees. Read it, then
  dismiss it
- **Skip the next one** skips a single occurrence. The schedule itself stays
- If two meetings overlap, the running one continues and the later one is
  skipped

### 4.3 Starting without a schedule

That is **Start this meeting now** in 3.2. Use it for a meeting you never put in
the schedule, or one that starts early. It follows exactly the same path as a
scheduled run.

### 4.4 Posting to the Zoom chat

**LiveCaption can post the viewer URL and a QR code to the meeting chat once
captions start.** Tick "Post the URL and QR code to the Zoom chat" on the Manage
meetings tab. **It is off by default.**

The message is in English. Only the text in brackets changes with the caption
direction, between `Japanese to English` and `English to Japanese`.

```
Live captions for this meeting (Japanese to English):
https://livecaption.<your-tailnet>.ts.net/v/xxxxxxxx
Open the link in any browser. No app or sign-in needed.
```

**It posts twice: at the start time and three minutes later** (for "Start this
meeting now", when you press it and three minutes later). **Zoom does not show
people what was said before they joined**, so one message at the start time
would miss everyone who joins on the hour, and a single later message would miss
everyone already there.

**Anyone who joins more than three minutes late gets nothing.** That is Zoom's
behaviour, and LiveCaption cannot change it. For a meeting where people
arrive late, put the viewer URL in the Zoom invitation as well, or press
**Post to the Zoom chat** by hand under **How people see it** → **In a
browser**.

The posting works by driving the Zoom window on screen, so there is no way to
confirm that the message arrived. Appendix E.3 has the details and the
weaknesses.

### 4.5 The host URL

**Even in a meeting you do not host, you can show Zoom captions if the host
helps.**

Each meeting has a **separate secret URL** for the host. Press **Show the host
URL** on the Manage meetings tab.

**Give the host URL to the host only.** Do not confuse it with the viewer URL.
Anyone holding the host URL can push captions into that meeting.

When the host opens it in a browser, a page appears where they paste the token.
Once they send it, LiveCaption starts sending captions to Zoom.

The host URL is accepted only:

- for the meeting being delivered now
- within 30 minutes of the scheduled time, or while that meeting is running
- once (after that it is refused; press "Accept once more" on the control page
  to reopen it)

**If the URL leaks, delete that meeting and make a new one.**

### 4.6 Notes on the Zoom side

- **A meeting set to "authenticated users only" cannot be joined without signing
  in.** In that case, and only then, open the Zoom window over VNC and sign in
  (Appendix C.3). The sign-in is kept in a volume, so you do it once
- If it joins but no audio arrives for five minutes, the control page reports it.
  It is a waiting room, a wrong passcode, or an update dialog. Look at the
  screen over VNC
- **The only way LiveCaption can leave Zoom is to quit Zoom.** It never quits a
  meeting a person joined by hand: it quits only the meetings it joined itself

---

## 5. The glossary

The glossary decides how accurately technical terms are translated. **The
glossary is the part of LiveCaption you maintain.**

### Building your tables

**Put one file per subject**, with the extension `.tsv`. There is a sample at
[glossary-example.tsv](glossary-example.tsv). Replace the words with the ones
your own meetings use, and upload it from the control page (2.7).

**The ZIP has no tables in it.** Personal names and organisation names make the
tables work much better, and such names do not belong in a distribution.
Your tables stay on your own machine.

There are two reasons to split the terms into several tables. First, you can
exclude the words a meeting does not need. Second, there is a limit on how many
words reach the transcription model (200 by default). One large table used for
every meeting fills that limit with words the meeting does not need, and the
words that matter are cut.

### Format

One term per line, three columns separated by tabs.

```
Japanese (correct form)  <TAB>  English  <TAB>  common misrecognitions (comma separated)
```

The table is used in two places.

1. **Transcription.** The Japanese and English forms are passed to the
   transcription model as keywords, which makes it more likely to hear the term
   correctly
2. **Translation.** The whole table goes into the translation prompt, and the
   third column becomes replacement rules

**The third column is the important one.** Transcription still makes mistakes.
When you write down the errors transcription actually made, the translation step
can recover the correct term from them.

**The columns are separated by tabs. With spaces, the whole line is read as one
Japanese term, and neither the English nor the misrecognitions are registered.**
You cannot see the difference by looking at the file.

### Choosing tables for each meeting

The **Glossary** row on the **Setup** tab is folded. **You can see what is in use
without opening it**: the table names and the total word count. Press the row to
open the list and tick the tables you want. **The change takes effect as soon as
you tick.**

Below the ticks you see the total word count and how many words go to the
transcription model. **The count turns red when words are cut.** Use fewer
tables: the words past the limit never reach transcription.

- When there are eight or more tables, a filter box appears above the list.
  Tables you have ticked stay visible even when the filter hides the others
- **Select all** and **Clear all** also apply to tables hidden by the filter
- **The choice is remembered.** The next start uses the same combination
- When the same Japanese term appears in several tables, the entries are merged.
  The English comes from the first table that has it, the misrecognitions are
  collected from all of them, and a disagreement about the English is shown on
  screen

### Updating the tables after a meeting

1. Open the `.md` of the meeting record (download it from the control page). The
   header says which tables that meeting used
2. Find the terms that were translated wrongly
3. Add the wrong text that transcription actually produced to the third column
   of the table that holds that term
4. Upload the table again

Three kinds of entry belong in the third column.

- **Homophone errors.** A word written with the wrong characters
- **English misrecognitions.** An acronym turned into an ordinary English word:
  `PID` heard as "peed", for example
- **A word with the end of the previous word attached to its front.**
  Transcription gets the word boundary wrong, so **record the joined form,
  including the previous word**, because that is what transcription writes

**Do not guess.** If a word makes no sense, ask the person who was speaking. You
cannot recover some errors from the sound alone.

**Before the meeting, test a word you added.** Speak a sentence that contains it
and check that the caption shows the right English. Writing it in the table is
not proof that it works.

---

## 6. The meeting record

**LiveCaption saves a record of every meeting by default.** Each transcript line
is paired with the caption translated from it. Use these pairs when you extend
the glossary.

### Downloading a record

**The records stay inside the container. Download them from the control page.**

There are two buttons under **Meeting record** on the **This meeting** tab.

| Button | What you get |
|---|---|
| **Readable (.md)** | The version people read. Transcript and caption, paired |
| **Original (.jsonl)** | One sentence per line, with the measured delays. Use it to rebuild the `.md` after a crash, or to look at the delays |

The file is saved in the download folder of **the machine you opened the control
page on**.

- **Only the latest record can be downloaded.** The line under the buttons says
  which one it is. For older records see Appendix C.4
- **You can download during the meeting.** The `.md` then says the meeting is
  still going
- **Pressing Stop closes the record** (about three seconds later), so a download
  after Stop gives you the finished meeting. A pause does not split the record
  if you start again within those seconds
- If no sentence was produced, no file is written

There is no way to switch the record off for a single meeting. Delete it
afterwards instead (Appendix C.4).

---

## 7. Settings

**Every setting goes in `.env`**: the API key, the machine-specific choices and
the delay tuning are all in that one file. The sample with the explanations is
`.env.example`.

| Name in `.env` | What it decides | Where it is explained |
|---|---|---|
| `OPENAI_API_KEY` | **The API key. This one is required** | 2.3 |
| `LIVECAPTION_MEETING_CONTEXT` | **What your meetings are about. It improves technical terms** | below |
| `LIVECAPTION_RESTART` | Whether it runs resident. `no` for a laptop | 2.3 |
| `TS_HOSTNAME` | The container's name on the tailnet. **Change it if you run more than one** | Appendix B.2 |
| `TS_AUTHKEY` | A Tailscale auth key. **Not needed if you approve by hand** | Appendix B.3 |
| `LIVECAPTION_VNC` | Whether VNC runs from start-up | Appendix C.3 |
| `LIVECAPTION_SAVE_DIR` | Where the meeting record is written | Appendix C.4 |
| `LIVECAPTION_GLOSSARY_DIR` | Where the glossary tables are kept | Appendix E.4 |
| `LIVECAPTION_IDLE_FLUSH_SEC` and three more | Delay tuning | Appendix E.4 |

### Telling it what your meetings are about

**After the glossary, this is the setting that matters most.** One sentence
describing the field goes into both the transcription prompt and the translation
prompt, and it tells the models which field to read every technical term in.

```ini
LIVECAPTION_MEETING_CONTEXT=A weekly meeting about radio astronomy. Topics include receivers, calibration and observation scheduling.
```

The default names no field. It works, but technical terms come out wrong more
often. **Write the sentence in the language your meetings are mostly held in.**

**This is not the same as the glossary.** The glossary gives the translation of
one term; this sentence tells the model which field to read every term in. Use
both.

### Editing `.env`

**After you edit `.env`, recreate the container**, because `.env` is read only at
start-up. Run this in PowerShell.

```powershell
docker compose up -d
```

`docker compose restart` does not read it again.

**The four delay settings can also be changed from the control page.** Press the
**Delay tuning** row on the **Setup** tab to open the fields. A number takes
effect as soon as you leave the field. **Save to `.env`** keeps it for the next
start, and **Reset to defaults** puts back the measured values. Appendix E.4
explains what they mean.

**A typo cannot stop a meeting.** A value that is not a number, or is too small,
is reported at start-up and ignored, and the default is used.

---

## 8. When something is wrong

| Symptom | Where to look |
|---|---|
| Nothing happens, no captions | **Did you press Start under Right now?** Starting LiveCaption is not enough |
| The control page does not open | Is the container running (`docker compose ps`)? Does `docker compose logs` show a Tailscale address? Is the machine you are on connected to the tailnet? **Open the full `https://` name** (a short name or an IP does not match the certificate) |
| The log says https could not be served | Did you enable HTTPS in the Tailscale admin console (2.5)? Enable it, then `docker compose restart` |
| The log keeps showing the authentication URL | Open it in a browser and approve the machine (2.5) |
| It cannot join Zoom | **Start VNC and look at the Zoom window** (Appendix C.3): a waiting room, a wrong passcode, or a meeting that wants you signed in |
| It joined but the level meter stays flat | A waiting room, a wrong passcode, or it did not join the meeting audio. Look at the screen over VNC. After five minutes the control page reports it too |
| An AI Companion dialog appears on joining | **Leave it alone.** The captions work even if nobody answers it. Zoom has no setting to stop it |
| No transcript lines appear | `OPENAI_API_KEY` in `.env`. **Is `.env` in the same folder as `compose.yml`? Is it called `.env.txt`?** After a fix, `docker compose up -d` |
| Transcript appears but no captions | The translation error is shown on the page |
| Nothing in the Zoom caption area | **Are you looking at the host's screen?** (the host never sees them). Did the reader turn manual captions on? Is the token from this meeting? Check the failure count in the top right |
| A different set of captions appears | **Zoom's automatic captions are running.** Ask the host to turn them off |
| Start delivering fails | Funnel is not enabled (Appendix B.6) |
| A term in the table is still translated wrongly | Are the words past the limit (section 5)? Is that subject's table ticked? |
| The captions go by too fast to read | Ask people to make the caption area taller. Lower `LIVECAPTION_FORCE_CUT_CHARS` (Appendix E.4) |
| A scheduled meeting does not start | **Is "Start this meeting automatically" ticked?** It is off by default. Check that the schedule is listed under Right now |
| It does not leave when the meeting ends | Check the silence timeout. The hard cap always stops it |
| It keeps running after an early finish | An open microphone means there is no silence. Press Stop now |
| Restart does not stop it | That is resident mode (`unless-stopped`). To really stop it, `docker compose stop` (Appendix C.5) |
| The glossary is empty | The ZIP has no tables (2.7). Upload yours from the control page |
| The meeting record cannot be found | It is in a Docker volume. Downloading it from the control page is the quick way |
| Running the container kills the host's network (Linux) | **That is ConnMan** (Appendix A.2). It breaks tens of seconds later, so checking right after start-up tells you nothing |

**Check the receiving side first.** Sending can succeed while nothing appears for
the reader, and the cause is usually on the reader's side.

**Captions do not disappear with time.** They stay until new lines push them
out, so a reader who looked away can still read them.

Symptoms specific to Windows native are in Appendix D.12.

---

## Appendix A. What differs on Linux + Docker

**This is for an always-on Linux machine.** Use it for scheduled meetings that
run with nobody watching. A machine that is asleep cannot act at the scheduled
time, so turn suspend off.

**Type the commands in a shell.** Read "Run this in PowerShell" in section 2 as
"run this in a shell" throughout. What you write in `.env` and the other files
is the same as in section 2.

**The steps are the same as section 2 as well. Only A.1 to A.3 below differ.**

### A.1 Install Docker (replaces 2.1)

Install Docker Engine and Compose instead of Docker Desktop. The instructions
are at [docs.docker.com](https://docs.docker.com/engine/install/). **Compose v2
or later is required.**

Putting yourself in the `docker` group saves typing `sudo`. Run this in a shell.

```sh
sudo usermod -aG docker $USER
```

**Log out and back in afterwards.** A group change does not apply to your
current session.

For the parts that correspond to 2.2 and 2.3, run this in a shell (only `copy`
becomes `cp`).

```sh
unzip main.zip
cd LiveCaption-main
cp .env.example .env
```

### A.2 If the machine runs ConnMan (not in section 2)

**If the network is managed by ConnMan, fix its configuration first.** ConnMan
is used by some Debian setups and on embedded machines. Run this in a shell to
find out whether ConnMan is running. If it is not in use, skip this section.

```sh
systemctl is-active connman
```

ConnMan tries to manage every interface it finds, and **its default exclusion
list does not contain `docker`, `veth`, `br-` or `tailscale`.** So it takes over
the virtual interfaces Docker creates, fails DHCP on them, assigns a
link-local address, and **points the host's default route at them.** The host's
outbound traffic disappears into the container.

**The symptom is hard to recognise.** DHCP has to time out first, so the host's
network breaks **tens of seconds after the container starts**. From Zoom it
looks like "it joins, then loses audio and drops after 30 to 40 seconds".

Write this in `/etc/connman/main.conf`.

```ini
NetworkInterfaceBlacklist = vmnet,vboxnet,virbr,ifb,ve-,vb-,docker,veth,br-,tailscale
```

Then restart ConnMan and tailscaled. Run this in a shell.

```sh
sudo systemctl restart connman
sudo systemctl restart tailscaled
```

**Do not leave `tailscale` out of that list.** If you exclude only `docker`,
ConnMan takes over `tailscale0` instead and shuts it down. **`tailscale
status` still says "Online: True"** in that state, because the control
connection goes over the wired interface: only the data path is broken, so the
status display cannot show the problem. Run this in a shell to see the truth.

```sh
ip addr show tailscale0
```

### A.3 Resident mode (replaces 2.3)

Leave `LIVECAPTION_RESTART` out of `.env`. With the default (`unless-stopped`),
the container comes back by itself after a host reboot, which is what a
scheduled meeting depends on.

---

## Appendix B. The Tailscale account and its settings

**If you do not have an account yet, read B.1 first.**

B.2 onwards are for the cases 2.5 does not cover. The steps in 2.5 assume one
machine on a tailnet of your own.

### B.1 Creating an account

**You cannot create a Tailscale-only account.** There is no sign-up with an
e-mail address and a password, and **there is no such thing as a Tailscale
password.** You sign in with an account you already have: Google, Microsoft,
GitHub or Apple. (If your employer uses Okta or OneLogin, you can use those
too.)

1. Open [login.tailscale.com/start](https://login.tailscale.com/start)
2. Choose the account you want to sign in with, and sign in
3. You get one tailnet. Its name is chosen for you and looks like
   `tail1234.ts.net`. **That name becomes part of the control page URL and the
   viewer URL** (2.6)

**Decide which account to use before you start.**

- **An account from your employer or university may put you on the same tailnet
  as everyone else on that domain.** If somebody there already made one, you
  join theirs. To keep it to yourself, use a personal account
- **A tailnet created with GitHub or Apple cannot be moved to another sign-in
  method later.** The others (Google, Microsoft and so on) can be moved between
  each other. **If you are unsure, use Google or Microsoft**
- **The machine you open the control page from signs in with the same account.**
  A machine on somebody else's account cannot reach the control page

**The free Personal plan is enough.** It allows up to six users and puts no
limit on the number of devices. LiveCaption uses one container plus the machine
you open the control page on. The current terms are at
[tailscale.com/pricing](https://tailscale.com/pricing).

Once you have an account, go back to 2.5.

### B.2 Running more than one

An approved machine gets a name, `livecaption` by default. **From the second
machine on, write this in `.env` before you start it, to change the name.**

```ini
TS_HOSTNAME=livecaption-note
```

Two machines asking for the same name do not share it. The second one is renamed
to `livecaption-1`, **and the viewer URL you gave people no longer reaches it.**

There is one `.env` per machine. You carry it by hand when you move to another
machine.

### B.3 Registering with an auth key

Instead of approving by hand as in 2.5, you can put an auth key in `.env`. The
container then registers itself unattended. Create the key in the Tailscale
admin console (Settings → Keys → Generate auth key).

| Field | Value | Why |
|---|---|---|
| Reusable | **on** | You need it again whenever you recreate the volume |
| Ephemeral | **off** | An ephemeral node changes its name every time, and the viewer URL stops working |
| Tags | `tag:livecaption` | A tagged device never expires, so you do not need Disable key expiry |

To use the tag, put it in your ACL first (B.5). Funnel needs the node attribute
as well. Add this to the JSON under Access Controls in the admin console.

```json
"tagOwners": { "tag:livecaption": ["autogroup:admin"] },
"nodeAttrs": [ { "target": ["tag:livecaption"], "attr": ["funnel"] } ]
```

**Do not leave the `nodeAttrs` line out.** A tagged device is no longer part of
`autogroup:member`, so without `nodeAttrs` the device loses Funnel. If you do
not use a tag, turn on Disable key expiry as in 2.5.

Write the key you created in `.env`.

```ini
TS_AUTHKEY=tskey-auth-xxxxxxxx
```

Then recreate the container with `docker compose up -d`.

### B.4 If your tailnet has Tailnet Lock

**A signature is needed whether you approve by hand or use an auth key.** The
container is a new node and holds no signing key. An unsigned node is registered
but **cannot connect to any other node.**

To find out whether Tailnet Lock is on, run this in a shell on a machine that
holds a signing key.

```sh
tailscale lock status
```

If it says `Tailnet Lock is NOT enabled.`, skip this section.

**With an auth key, sign the key.** Run this in a shell on a machine that holds
a signing key, and put the signed key in `TS_AUTHKEY` in `.env`.

```sh
sudo tailscale lock sign tskey-auth-xxxxxxxx
```

A node registered with a signed key is signed at registration. The key is
reusable, so one key can register as many signed nodes as you like.

**If you approve by hand, sign the node key.** After approving, run this in a
shell to get the node key.

```sh
docker compose exec engine tailscale lock status
```

Then sign that node key on a machine that holds a signing key.

```sh
sudo tailscale lock sign nodekey:xxxxxxxx
```

**You sign once.** The state is kept in the `tailscale` volume.

**Handle a signed auth key with care.** The key itself is registered on the
tailnet as a trusted signing key, so **anyone holding it can add as many signed,
trusted nodes as they like**, which is exactly what Tailnet Lock is meant to
prevent. Approving by hand is safer in this respect.

### B.5 ACLs — who can reach what

**An ACL decides which machines on your tailnet can connect to which.** You
write one JSON document under Access Controls in the Tailscale admin console.
There is one per tailnet, so **editing it affects your other machines as well.**

**On a tailnet of your own, the default works.** The default is "everyone can
reach everything", and LiveCaption runs without you reading this section. There
are two cases where you do need it.

**(1) Other people are on your tailnet.** Unless you narrow the ACL, they can
open the control page too, and from there quit LiveCaption, start delivering, or
**read the transcript of your meeting.** **The control page has no
authentication.** The only thing protecting it is where it can be reached from,
so on a shared tailnet you need an ACL.

Here is an example that narrows it to your own machines. Use your own user name
instead of `autogroup:member`.

```json
{
  "acls": [
    {"action": "accept", "src": ["your-name@example.com"],
     "dst": ["tag:livecaption:8081,8443,6080,6443"]}
  ]
}
```

**(2) You use Funnel on a tagged node.** The default ACL gives `funnel` to
`autogroup:member`, but **a tagged device is no longer part of
`autogroup:member`.** Without the `nodeAttrs` below, delivery cannot start.

```json
{
  "tagOwners": {"tag:livecaption": ["your-name@example.com"]},
  "nodeAttrs": [{"target": ["tag:livecaption"], "attr": ["funnel"]}]
}
```

**Changes take effect at once**, with no restart. The full syntax is at
[tailscale.com/kb/1018/acls](https://tailscale.com/kb/1018/acls).

### B.6 If Funnel will not start

When "Start delivering" fails, one of these two is not enabled on the tailnet
side. The reason is printed under the button.

1. **HTTPS certificates.** Admin console → DNS → HTTPS Certificates → Enable
   HTTPS (2.5)
2. **The `funnel` node attribute.** The default ACL gives it to
   `autogroup:member`, but a tailnet with a rewritten ACL, or a tagged node, may
   not have it (`nodeAttrs` in B.5)

---

## Appendix C. Updating, VNC and maintenance

### C.1 Updating

**If you installed from the ZIP**, download the new ZIP, unpack it, and copy it
over the LiveCaption folder. `.env` is not in the ZIP, so it survives. The
glossary, the list of meetings, the records and the Tailscale registration are
in Docker volumes, so they survive replacing the whole folder.

Then rebuild the image. **Updating the folder does not change what is running.**
Run this in PowerShell.

```powershell
docker compose up -d --build
```

**If you prefer Git**, fetch it this way instead of 2.2. Run this in PowerShell.

```powershell
git clone https://github.com/asoy01/LiveCaption.git
cd LiveCaption
```

To update, `git pull` and then the same `docker compose up -d --build`. `.env`
is not in Git, so `git pull` never overwrites it.

### C.2 Docker commands

There is no command to type for a meeting. **These are for setting up and
maintenance.** Run them inside the LiveCaption folder, in PowerShell (on Linux,
run the same ones in a shell).

```powershell
docker compose up -d --build     # build and start: the first time, and after an update
docker compose up -d             # after editing .env (recreates the container)
docker compose logs -f           # start-up messages, and the running log
docker compose ps                # is it running
docker compose restart           # restart without rebuilding
docker compose stop              # stop. It stays down across a host reboot
docker compose start             # bring a stopped one back
docker compose down              # remove the container. The volumes stay

docker compose exec engine bash  # get a shell inside
docker compose cp engine:/app/local/transcripts ./transcripts   # take all the records out
```

**Do not use `docker compose down -v`.** It deletes the volumes, and with them
the list of meetings, the viewer URLs, the Tailscale registration and the Zoom
sign-in.

### C.3 VNC — looking inside the container

**You do not normally need this.** The Zoom inside the container joins meetings
without signing in. There are two occasions to use VNC.

- **When joining gets stuck.** A waiting room, a wrong passcode, an update
  dialog: **you can see which it is.** From outside, all of them look the same
- **For a meeting that needs signing in.** A meeting set to "authenticated
  users only" cannot be joined otherwise. In that case, open the Zoom window
  over VNC and sign in. The state is kept in a volume, so you do it once

Press **VNC** → **Start** on the **Setup** tab. The screen inside the container
is then at this URL.

```
https://livecaption.<your-tailnet>.ts.net:6443/vnc.html
```

**VNC has no password.** The only protection is the boundary of your tailnet.
**Start it when you need it and stop it afterwards.** You can start and stop it
during a meeting.

You can open a terminal inside the container too: right-click on an empty area
and choose `Terminal emulator`.

To share the clipboard with your own PC, connect with a VNC client to
`livecaption.<your-tailnet>.ts.net:5900` instead of using the browser. To run
VNC from start-up, write `LIVECAPTION_VNC=1` in `.env` (do not leave it on).

### C.4 Taking out and deleting records

The records are in `local/transcripts/` in the `state` volume. The control page
gives you only the latest one, so take older ones from the host. Run this in
PowerShell.

```powershell
docker compose cp engine:/app/local/transcripts ./transcripts
```

Records accumulate. **Deleting them is a manual job.** Run this in PowerShell.

```powershell
docker compose exec engine ls /app/local/transcripts
docker compose exec engine rm /app/local/transcripts/live-caption_2026-09-01_*
```

**To write the records to a folder on the host, add a volume in `compose.yml`.**
`LIVECAPTION_SAVE_DIR` in `.env` is a path inside the container, so a path you
write there is still inside the container.

### C.5 How resident mode works

The container runs with `restart: unless-stopped`, so it comes back by itself
after a crash and after a host reboot. **That is why the button on the control
page restarts it instead of stopping it.** To really stop it, run
`docker compose stop` on the host. **Stopping it also removes the control page,
so you cannot start it again remotely.**

`LIVECAPTION_RESTART` in `.env` changes both the resident behaviour and the
button.

| `LIVECAPTION_RESTART` | Resident | Button | What it does |
|---|---|---|---|
| `unless-stopped` (default) | yes | Restart | Stops and comes back within seconds |
| `no` | no | Quit | Stays down. Bring it back with `docker compose up -d` |

---

## Appendix D. Windows native (frozen)

**This way is frozen.** What works still works and nothing was removed, but no
new features go into it. This appendix is for people already running Windows
native. **To set up from scratch, use Docker (section 2).**

**This way needs a Windows PC dedicated to captions**, called the caption PC
below. **It joins the meeting as a silent participant, so you need it in
addition to the PC you speak from.**

#### What is worse

- **It needs two PCs.** The caption PC's sound output stays pointed at
  `CABLE Input`, so **you cannot attend the meeting from that same PC**
- **Applications compete for the sound device.** If another application changes
  the output, **the captions stop without saying anything**
- **It is frozen.** No new features

#### What is better

- **It works with meeting software other than Zoom.** The audio comes from
  `CABLE Output`, so **any software that lets you pick `CABLE Input` as its
  speaker works** (Teams, Google Meet, Webex). Joining and posting to the chat
  automatically are Zoom-only, so with other software a person joins by hand
- **It needs no Docker**, no WSL2 and no image build

You install it once, on the caption PC.

### D.1 Install VB-CABLE

Download it from [vb-audio.com/Cable/](https://vb-audio.com/Cable/). **Run the
installer as administrator, then restart the PC.**

### D.2 Set both CABLE devices to 48000 Hz

After the restart, check these two in the Windows sound settings.

| Device | Format |
|---|---|
| `CABLE Input` (playback) | **48000 Hz**, 16 bit |
| `CABLE Output` (recording) | **48000 Hz**, 16 bit |

**Set both to 48000 Hz.** If the two rates differ, Windows resamples the audio
and the sound breaks up.

### D.3 Install pixi

pixi builds the Python environment. **You do not install Python separately**:
pixi installs the right version.

Run one of these in PowerShell.

```powershell
winget install prefix-dev.pixi
```

```powershell
powershell -ExecutionPolicy ByPass -c "irm -useb https://pixi.sh/install.ps1 | iex"
```

**Open a new PowerShell window afterwards**, or the change to PATH does not
apply. Then run this in PowerShell to check it.

```powershell
pixi --version
```

### D.4 Get LiveCaption

The same as 2.2: unpack the ZIP somewhere without spaces or non-English
characters in the path, for example `C:\LiveCaption`. With Git, run
`git clone https://github.com/asoy01/LiveCaption.git`.

### D.5 Build the Python environment

Run this in PowerShell, inside the LiveCaption folder.

```powershell
pixi install
```

The packages go into a `.pixi` folder. pixi does not touch the system Python.
The first run takes a few minutes.

### D.6 Write the API key

The same as 2.3: create `.env` and write `OPENAI_API_KEY`. Leave
`LIVECAPTION_RESTART` out, as it belongs to Docker.

### D.7 Check that the audio path works

This check needs no meeting and no API key. It plays a sine wave into
`CABLE Input` and reads it back from `CABLE Output`. Run this in PowerShell.

```powershell
pixi run python scripts/cable_loopback.py
```

If the level comes back, VB-CABLE is working.

```
--- what the app picks by default: 2 'CABLE Output (VB-Audio Virtual ' [MME] ---
  39 windows, 39 with sound (100%)  peak 0.299  0 dropped
  => the path works
```

### D.8 Put LiveCaption in the Start menu (optional)

Double-click **`InstallToStartMenu.bat`**. A `LiveCaption` entry appears in the
Start menu.

```
Windows key  ->  type "livecaption"  ->  Enter
```

**That entry opens no window.** It goes to the task tray, and you right-click the
icon for the control page, the log and quit (D.11). To watch it start, run
`StartLiveCaption.bat` by hand instead.

**Run `InstallToStartMenu.bat` again if you move or rename the folder.** To
remove the entry, run `InstallToStartMenu.bat -Remove` in PowerShell.

### D.9 Where the glossary is kept

With Windows native you put the tables straight into `etc/glossary/`. Run this in
PowerShell to copy the sample there.

```powershell
copy docs\glossary-example.tsv etc\glossary\MyProject.tsv
```

The format and the way you choose tables are the same as section 5. You can also
choose the tables at start-up.

```powershell
pixi run caption --web --glossary table1 table2
```

### D.10 What differs when you run a meeting

**Starting.** Double-click `StartLiveCaption.bat`. The control page opens in
your browser.

```
http://localhost:8081              control page   <- never share this one
http://localhost:8080/v/<random>   viewer page    <- share this one
```

**Joining.** Check three things in the meeting software on the caption PC, then
join.

- **Audio output: `CABLE Input`.** If this is wrong, no audio reaches
  LiveCaption
- **Microphone: muted.** This PC never speaks
- **Display name: something like `Live Captions`.** The name appears in the
  participant list, so make it clear what this PC is doing

For automatic joining (section 4), set these in the Zoom client **once**.

- Mute the microphone on joining, and turn video off on joining
- **Do not ask about joining computer audio.** If that prompt is left on,
  LiveCaption joins but no audio arrives, and with nobody watching there is no
  one to answer it. The cause is hard to find

**The input device.** Check that **Audio input** on the **Setup** tab says
`CABLE Output`. If not, pick it from the list; the change takes effect as soon
as you pick it. The list shows the host API as well as the name, because Windows
shows `CABLE Output` three times (MME, DirectSound, WASAPI). Any of the three
works.

**Screen share.** The viewer page is at `http://localhost:8080/v/...`. To open
it from the PC you attend the meeting on, use `--control-bind` (D.13) or give
people a URL.

**Giving people a URL.** There are two routes, chosen under **Route** in **How
people see it** → **In a browser**.

| | Cloudflare | Tailscale |
|---|---|---|
| Preparation | install `cloudflared` on the caption PC | Tailscale on the caption PC, then `tailscale funnel 8080` once and accept the consent page |
| URL | **changes every time you deliver** | **never changes** |
| Can you give it out in advance? | no | **yes** |

To use Cloudflare, install `cloudflared` first. Run this in PowerShell.

```powershell
winget install --id Cloudflare.cloudflared
```

Or download
[cloudflared-windows-amd64.exe](https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe)
and put it in `local/bin/cloudflared.exe` inside the LiveCaption folder.

**With Cloudflare the captions pass through Cloudflare's relay.** For meetings
whose content must not leave your organisation, use screen share.

**Posting to the Zoom chat** (4.4) drives the Zoom chat window on the caption
PC's own screen. If you are sharing that screen, the chat window appears for a
moment. It uses the caption PC's clipboard: text is saved and put back, but an
image or a file in the clipboard is lost.

**Quitting.** Press **Quit** on the **Setup** tab. The browser tab and the
terminal window both close. From the task tray, right-click the icon and choose
quit.

### D.11 Running resident, and starting at logon

Double-click `StartLiveCaptionTray.vbs` to go to the task tray **with no
window**.

| Icon colour | State |
|---|---|
| Blue | Waiting |
| Green | Making captions |
| Red | A failure is showing |

Hovering over the icon shows the next meeting, or the one running now.
Right-clicking gives you the control page, the log and quit. There is no window,
so the log goes to `local/log/`.

**If you edit `StartLiveCaptionTray.vbs`, write it in ASCII only.** With Japanese
comments in it, **double-clicking does nothing at all, with no error.**

To start it at logon, run this in PowerShell. It needs no administrator rights.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
```

Add `-Remove` to undo it.

**Leave the caption PC logged on.** A locked screen is fine; logging off is not,
because LiveCaption cannot run without a session.

### D.12 When something is wrong

| Symptom | Where to look |
|---|---|
| `CABLE Output` is not in the list | Did you install VB-CABLE? Did you restart the PC? |
| The level meter stays flat | Is the meeting software's output `CABLE Input`? Are both devices at 48000 Hz? Does **Audio input** on the Setup tab say `CABLE Output`? |
| "Cannot open the input" | Is another application holding it? Does the device accept 48000 Hz? Pick another input |
| No transcript lines appear | `OPENAI_API_KEY` in `.env`. **Is `.env` in the same folder as `pixi.toml`? Is it called `.env.txt`?** |
| The captions stopped after you connected remotely | **Are you using RDP?** It cuts the audio path (D.13) |
| It joined Zoom but no audio arrives | **Is the "join computer audio" prompt still waiting?** (D.10) |
| It does not start at logon | Are you logged on? Check the `LiveCaption` task in Task Scheduler |
| No tray icon | Read `local/log/` |
| The control page does not open from the tailnet | Did you start it with `--control-bind` (D.13)? Is Tailscale up? |
| The captions stopped part-way | `seq` went backwards. If you restarted the app, check `local/seq_state.json` |
| Transcription reconnects again and again | The network. Turn incoming video off in the meeting software on the caption PC, and try another connection |

### D.13 Operating the caption PC remotely

You can put the caption PC in another room. **RDP (Remote Desktop) does not
work**, because it moves the audio to "remote audio" and cuts the path to
`CABLE Input`, which stops the captions. Use something that drives the console
session itself, such as a VNC-based tool.

The control page is a web page, so **opening it from another machine's browser
is faster and more reliable** than forwarding the whole screen. With Tailscale,
start it like this in PowerShell to serve the control page on your tailnet
address as well.

```powershell
pixi run caption --web --control-bind
```

```
Control page: http://localhost:8081  (for you only. Do not share it)
              http://100.x.x.x:8081  (from inside the tailnet. **Narrow it with an ACL**)
```

- **127.0.0.1 always stays**, so you can still work at the machine itself when
  Tailscale is down
- **The control page has no authentication.** Addresses outside the Tailscale
  ranges are refused by design. If other people are on your tailnet, narrow it
  with an ACL (Appendix B.5)
- It is plain HTTP, so the "Copy the URL" button does not work. The URL is
  selected for you, so press Ctrl+C

### D.14 Commands and options

The first three run by double-clicking. Run the rest in PowerShell, inside the
LiveCaption folder.

```powershell
StartLiveCaption.bat                             # this is what you use for a meeting
StartLiveCaptionTray.vbs                         # go to the task tray, no window
InstallToStartMenu.bat                           # add it to the Start menu (once)

pixi run web                                     # the same thing, from a terminal
pixi run caption --web                           # the same
pixi run caption --token "<URL>" --web           # Zoom captions and browser captions
pixi run caption --web --no-save                 # keep no record
pixi run caption --check-audio 20                # watch the level only (no API)
pixi run devices                                 # list the input devices
pixi run caption --from-file recording.wav --dry-run   # play a WAV in real time
pixi run python scripts/transcript_to_md.py      # rebuild the .md after a crash
pixi run python scripts/cable_loopback.py        # test the VB-CABLE path
```

| Option | Meaning |
|---|---|
| `--token <URL>` | The Zoom caption API token. You can also paste it on the control page later |
| `--direction <way>` | Caption direction, `ja2en` or `en2ja`. Default: the one you chose last |
| `--glossary <name> ...` | Which tables in `etc/glossary/` to use. Default: the combination you chose last |
| `--device <name>` | Part of the input device name. Default: `CABLE Output` |
| `--web [port]` | Show captions in a browser. The number is the viewer port, 8080 by default |
| `--control-port <port>` | The control page port, 8081 by default |
| `--control-bind [address]` | Also serve the control page on your tailnet address (D.13) |
| `--web-bind <address>` | The address the viewer page listens on, 127.0.0.1 by default. Use 0.0.0.0 to show it directly to devices on the same LAN |
| `--tray` | Go to the task tray. The log is also written to `local/log/` |
| `--tunnel` | Open the tunnel at start-up. It is off by default |
| `--no-browser` | Do not open the browser automatically |
| `--no-save` | Keep no record of the meeting |
| `--save-dir <folder>` | Where to write the record. Takes priority over `LIVECAPTION_SAVE_DIR` in `.env` |
| `--dry-run` | Send nothing to Zoom; print to the screen only |
| `--from-file <wav>` | Play a WAV in real time instead of using a device. 24 kHz mono |
| `--loop` | Repeat the `--from-file` file |
| `--delay <level>` | Transcription delay and accuracy. `minimal`, `low`, `medium`, `high`, `xhigh`. Default `low` |
| `--model <name>` | The translation model. Default `gpt-4.1-mini` |
| `--check-audio [seconds]` | Show the level and exit. Calls no API |
| `--list-devices` | List the input devices |
| `--cloudflared <path>` | Where `cloudflared` is. Not needed if it is on PATH or in `local/bin` |

**If a port is taken, change both numbers.** Never give 8081 to the viewer page.

```powershell
pixi run caption --web 8090 --control-port 8091
```

To keep the records outside the LiveCaption folder, for example when the folder
is inside a synced folder, write this in `.env`.

```ini
LIVECAPTION_SAVE_DIR=%LOCALAPPDATA%\LiveCaption\transcripts
```

---

## Appendix E. How it works, and the detailed settings

You do not need this appendix to use LiveCaption.

### E.1 Why it joins as a separate participant

**Under Docker:**

```
[host PC]         runs the meeting as usual
     |
[container]
  Zoom ---------- joins as a silent participant, microphone muted
   |
   +-- output --> meeting (a PulseAudio null sink)
                     |
                  meeting.monitor --> LiveCaption
                                        |-- transcription
                                        |-- translation
                                        +-- output
```

**With Windows native:**

```
[host PC]         runs the meeting as usual
     |
[caption PC]
  meeting software ----- joins as a silent participant, microphone muted
   |
   +-- speaker output --> CABLE Input
                             |   (VB-CABLE, a virtual audio cable)
                          CABLE Output --> LiveCaption
                                             |-- transcription
                                             |-- translation
                                             +-- output
```

**Never run LiveCaption on the Zoom host's own PC.** Meeting software does not
send your own microphone to your own speaker, so recording the speaker output
there **loses the host's own voice.** A separate participant receives the mixed
audio, and that mix contains everybody.

**The audio goes through a virtual audio device.** No loopback API is involved,
so the volume and the mute button do not affect it. Under Docker that device is
a PulseAudio null sink; with Windows native it is VB-CABLE. **The Docker way
never touches the host's sound hardware**, so playing music on the host changes
nothing, and a host with no sound card works.

### E.2 The pipeline and the delay

```
meeting audio
  |
audio capture (24 kHz mono PCM, 100 ms at a time)
  |
transcription: gpt-live-transcribe (WebSocket, the glossary as keywords, ja/en detected automatically)
  |
sentence splitting (on end marks; forced at 70 Japanese characters)
  |
translation: gpt-4.1-mini (glossary, replacement rules, and the last 3 sentences as context)
  | to all three at once
  |-> the Zoom caption API
  |-> the viewer page
  +-> the meeting record
```

**The glossary is used in two steps.** In transcription, the glossary words are
passed as keywords so that proper nouns are heard correctly. In
translation, the glossary, the replacement rules built from real misrecognitions
and the last three sentences of context all go to the model. **Translation is
the step that does the work**: even when transcription produces something that
only sounds similar, translation can recover the term. It cannot fix everything.
A misrecognition that is itself a plausible technical term produces a fluent
wrong translation.

**Only finished sentences are sent.** Showing a partial sentence and rewriting
it later is impossible to follow: Zoom captions accumulate and are never
replaced, so the partial line stays and the corrected version appears below it.
The viewer page also shows the unfinished transcript as it arrives, with a
trailing `…`, because that page can rewrite what it has shown.

The delay depends on how the sentence ends (measured).

| | Transcription | Waiting to finalise | Translation | Sending | Total |
|---|---|---|---|---|---|
| Ends with an end mark (about 74%) | 0.2–1 s | 0 s | 0.9 s | 0.3 s | **about 1.5–2 s** |
| Trails off into silence (about 9%) | as above | 2.5 s | 0.9 s | 0.3 s | **about 4 s** |
| Cut for length (about 17%) | as above | 0 s | 0.9 s | 0.3 s | **about 1.5–2 s** |

For a sentence that trails off, the translation is started speculatively while
waiting (`SPECULATE_AFTER_SEC`); when the guess is right, the 0.9 s of
translation disappears. **The waiting time itself (`IDLE_FLUSH_SEC`) cannot be
lowered**: transcription output can pause for nearly 2.5 s in the middle of
continuous speech, so a lower threshold cuts people off mid-sentence.

### E.3 How the chat posting works, and where it is weak

**Zoom has no API for posting to the chat of a running meeting.** LiveCaption
drives the Zoom window on screen instead. This has three weaknesses.

- **A change in Zoom's own window layout stops the posting silently**, because
  LiveCaption identifies the windows by name
- **There is no way to confirm the message arrived.** LiveCaption only knows
  that it sent the message
- The Zoom chat window comes to the front while it posts (inside the container
  under Docker, where nobody sees it)

**The URL goes first and the QR code second.** In a meeting where the host has
turned file sending off, the QR code is rejected, but the URL has already
arrived.

**It waits for Zoom to finish joining.** The meeting window can take from tens
of seconds to several minutes to appear, so LiveCaption checks every ten
seconds and gives up after ten minutes, recording that in the log. If joining
runs late and pushes the first post past the start time, it leaves at least a
minute before the second one.

A failure never stops the captions; the result goes to the log. To change when
it posts, edit `SCHEDULE_CHAT_AT_MIN` in `src/live_caption/config.py`.

### E.4 The detailed settings

The defaults are in `src/live_caption/config.py`. They come from measurements on
real meeting audio, so override them in `.env` only when you have a reason.

**These four can be set in `.env`** (the control page changes the same four; see
section 7).

| Name in `.env` | The setting it overrides |
|---|---|
| `LIVECAPTION_IDLE_FLUSH_SEC` | `IDLE_FLUSH_SEC` |
| `LIVECAPTION_SPECULATE_AFTER_SEC` | `SPECULATE_AFTER_SEC` |
| `LIVECAPTION_FORCE_CUT_CHARS` | `FORCE_CUT_CHARS` |
| `LIVECAPTION_LINE_INTERVAL_SEC` | `LINE_INTERVAL_SEC` |

An override is reported at start-up. **If you change `IDLE_FLUSH_SEC` and leave
`SPECULATE_AFTER_SEC` out, `SPECULATE_AFTER_SEC` follows automatically**
(`IDLE_FLUSH_SEC − 1.1` seconds).

| Setting | Default | Meaning |
|---|---|---|
| `ASR_MODEL` | `gpt-live-transcribe` | The transcription model |
| `ASR_DELAY` | `low` | `minimal` and `high` both got technical terms wrong in testing |
| `ASR_LANGUAGES` | `("ja", "en")` | **Never fix this to one language.** A meeting can change language part-way through |
| `ASR_KEYWORD_LIMIT` | 200 | How many glossary words reach transcription. A warning appears when words are cut |
| `TRANSLATE_MODEL` | `gpt-4.1-mini` | The translation model |
| `CONTEXT_SENTENCES` | 3 | How many previous sentences go to the model as context |
| `MAX_CAPTION_CHARS` | 80 / 40 | Longest caption line. It differs by direction (English 80, Japanese 40) |
| `FORCE_CUT_CHARS` | 70 / 140 | A sentence longer than this is cut. Lower it if captions go by too fast |
| `IDLE_FLUSH_SEC` | 2.5 | Seconds of silence after which a sentence is finalised without an end mark |
| `SPECULATE_AFTER_SEC` | 1.4 | Seconds of silence after which the translation is started without waiting. `0` turns it off |
| `LINE_INTERVAL_SEC` | 0.6 | Gap between lines sent to Zoom. The viewer page is not affected |
| `WEB_LINES` | 8 | Lines shown on the viewer page |
| `WEB_PORT` / `CONTROL_PORT` | 8080 / 8081 | The viewer page and the control page |

Three more things about `.env`:

- **A value in `.env` overrides an environment variable of the same name.** A
  line with an empty value overrides nothing
- **Under Docker, a value written directly in `environment:` in `compose.yml`
  overrides `.env`.** If you edit `compose.yml`, keep the `${NAME:-default}`
  form so that `.env` still works
- `LIVECAPTION_GLOSSARY_DIR` is where the glossary tables are kept. Under Docker
  `compose.yml` points it inside a volume, so leave it alone

### E.5 What is in the meeting record

The files go to `local/transcripts/`, which under Docker is inside the `state`
volume. **The folder and the script (`transcript_to_md.py`) say "transcript",
while this manual calls the file the meeting record. They are the same files.**

```
local/transcripts/live-caption_2026-09-08_143012.jsonl   appended one sentence at a time
local/transcripts/live-caption_2026-09-08_143012.md      readable form, written at Stop or exit
```

Each transcript line is paired with its caption. The timestamp is the time the
transcript became final. The `.jsonl` also holds the measured delays.

| Field | Meaning |
|---|---|
| `cut` | Why the sentence was finalised. `punct` (end mark) / `force` (length) / `idle` (silence) / `flush` (at exit) |
| `waited` | For a sentence finalised by silence, how long it actually waited |
| `took` | Seconds spent on translation |
| `total` | **Seconds from finalising to the first caption line.** `total − took` is the time it waited in the queue |
| `spec` | Present when the speculative translation was used. `total` is then smaller than `took` |
| `dir` | The caption direction at that moment |

The `.md` file is written when you press Stop and when the app exits. A download
from the control page builds it at that moment, so it is always current. If the
PC loses power, rebuild it from the `.jsonl` with
`scripts/transcript_to_md.py`.

### E.6 The viewer port and the control port

The viewer page (8080) and the control page (8081) are separated by port, not by
path. **That is a security decision.** A tunnel is connected to the viewer port
alone, so nobody who learns the URL can stop LiveCaption. The viewer page sits
behind an unguessable path (`/v/<random>`), and `/` on the viewer port is a 404.

Under Docker, the control page is served only on 127.0.0.1 inside the container
and on the tailnet address, and `tailscale serve` also publishes it over https
(8443) inside the tailnet. Only the viewer page is published on the host's
`127.0.0.1:8080`, by `ports:` in `compose.yml`.

---

## Related documents

- [glossary-example.tsv](glossary-example.tsv) — a sample glossary table
- `.env.example` — every setting you can write, with explanations
- `compose.yml` — the Docker setup, with the volumes and the environment
  variables explained
- [test-procedure.md](test-procedure.md) — the staged bring-up test and the
  checklist for the day. Stages 1 and 2 are for Windows native; under Docker,
  2.8 does the same job
