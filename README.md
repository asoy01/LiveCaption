# LiveCaption

Real-time translated subtitles for meetings.

LiveCaption listens to the meeting audio, transcribes what people say, translates
it, and shows the captions to the participants who need them. You choose the
direction for each meeting: **English captions for a meeting held in Japanese**,
or **Japanese captions for a meeting held in English**. When someone speaks in
the caption language, LiveCaption shows that speech without translating it.

**LiveCaption is not limited to Zoom.** It reads the audio that the meeting
software plays, so any meeting software works.

## Quick start (Docker)

You need one machine with Docker, an OpenAI API key, and a Tailscale account.
**Windows with Docker Desktop and Linux with Docker Engine both work.**
The machine runs Docker and nothing else: the meeting software, the audio
devices, the screen and Tailscale are all inside the container.

**One laptop is enough.** You join the meeting as usual, while Zoom inside the
container joins the same meeting as a silent participant.

[Download the ZIP](https://github.com/asoy01/LiveCaption/archive/refs/heads/main.zip),
unpack it, and run these commands in the folder that holds `compose.yml`:

```powershell
copy .env.example .env        # then put your OPENAI_API_KEY in it
docker compose up -d --build
docker compose logs -f        # a Tailscale URL appears here the first time
```

Approve the container on your tailnet. The log then prints the address of the
control page, which you open in a browser on any machine on that tailnet:

```
https://livecaption.<your-tailnet>.ts.net:8443
```

You do everything from the control page: schedule a meeting and let the
container join Zoom by itself, start and stop caption generation, register the
Zoom token, hand out a URL to participants, upload glossary tables, download the
meeting record, and open a VNC view of the container when Zoom is asking to be
signed in or is waiting in a waiting room.

**Do not screen-share the control page.** The control page shows the Zoom
caption token. Share the viewer page instead.

The container restarts by itself after a crash or a host reboot.

Full instructions are in [docs/manual.md](docs/manual.md). Section 2 is the
install, Appendix A lists the differences on Linux, and Appendix B covers
creating the Tailscale account.

## Quick start (Windows native)

**The Windows native version is frozen.** It still works and nothing was
removed, but new features go into the Docker version only. It needs a Windows
PC used only for captions, [VB-CABLE](https://vb-audio.com/Cable/),
[pixi](https://pixi.sh), and an OpenAI API key.

```powershell
pixi install
copy .env.example .env        # then put your OPENAI_API_KEY in it
pixi run python scripts/cable_loopback.py   # check the VB-CABLE path
```

Then double-click `StartLiveCaption.bat`. See Appendix D of the manual.

## Three ways to show the captions

You can use all three at the same time.

| Output | Host rights | How people see it | Leaves your network |
|---|---|---|---|
| Zoom caption API | Required | Each person turns on "Show Captions" | Through Zoom |
| Screen share | Not required | You share the viewer page full screen | No |
| Give people a URL | Not required | People open a URL on their own device | Through Tailscale Funnel, or Cloudflare on Windows |

Section 3.4 of [the manual](docs/manual.md) covers all three, including how to
set up Cloudflare on the Windows native version.

## How it works

LiveCaption joins the meeting as a silent participant, with its microphone
muted. In Docker, the meeting software runs inside the container:

```
[host PC]        runs the meeting as usual
     |
[container]
  Zoom ---------------- joins as a silent participant, microphone muted
   |
   +-- output --> meeting        (a PulseAudio null sink)
                     |
                  meeting.monitor --> LiveCaption
                                        |-- transcription (gpt-live-transcribe)
                                        |-- translation (gpt-4.1-mini)
                                        +-- output
```

**A separate participant is required.** Meeting software does not send your own
microphone to your own speaker, so recording the speaker output on the host PC
loses the host's own voice. A separate participant receives the mixed audio, and
that mix contains every participant.

**The host audio devices are not used.** A null sink inside the container
carries the audio, so the host needs no sound card, and playing music on the host
changes nothing. In the Windows native version, VB-CABLE does this instead.

The delay from speech to caption is about 1.5–2 seconds for a sentence that ends
with a full stop or a question mark, and about 4 seconds for a sentence that
ends in silence.

## Handling technical terms

LiveCaption uses the glossary in two steps, not one.

1. **Transcription.** The glossary is passed to the transcription model as
   keywords, so the model is more likely to hear a term correctly.
2. **Translation.** The whole glossary goes into the translation prompt,
   together with replacement rules built from misrecognitions seen in real
   meetings, and the last few sentences as context. **This step matters most.**
   Even when transcription produces something that only sounds similar, the
   translation step can recover the correct term.

The glossary files are in `etc/glossary/`, one file per subject, and **you pick
which ones to use for each meeting**. Start from
[docs/glossary-example.tsv](docs/glossary-example.tsv) and replace the words with
the ones your own meetings use. Extending these tables is the main ongoing task.

**`etc/glossary/` is not distributed, so a fresh download has none.** Personal
names and organisation names make the tables work better, and you do not want to
share such names. In Docker you upload your tables from the control page; the
tables are kept in a volume and survive recreating the container.

## Documentation

| Document | Contents |
|---|---|
| [docs/manual.md](docs/manual.md) | Manual: install, run a meeting, the glossary, settings, troubleshooting. Linux, Tailscale, maintenance and Windows native are in the appendices |
| [docs/manual.ja.md](docs/manual.ja.md) | The same manual in Japanese |
| [docs/test-procedure.md](docs/test-procedure.md) | Staged test for bringing up a new caption PC, and the checklist for the day |
| [docs/glossary-example.tsv](docs/glossary-example.tsv) | A sample glossary table. Upload it from the control page, or copy it into `etc/glossary/` |

## Layout

```
compose.yml              what you run for the Docker version
docker/Dockerfile        how the container is built
docker/entrypoint.sh     audio, screen, Tailscale and Zoom set-up inside it
StartLiveCaption.bat     what you double-click with Windows native
InstallToStartMenu.bat   puts LiveCaption in the Start menu (run once)
run.py                   start-up and command line options
src/live_caption/        the application
scripts/                 one-off measurement and check scripts
docs/                    manual, test procedure
etc/                     the application icon, and your glossary tables (not in git)
data/recordings/         audio used for comparing speech recognition engines (not in git)
local/                   working files (not in git)
```

Each meeting record is written to **`local/transcripts/`** as
`live-caption_<date>.jsonl` and `.md`. **You download the record from the
control page**, so you do not have to go to the machine to read it. In Docker
the records are stored in the `state` volume. To keep the records somewhere else
on Windows, set `LIVECAPTION_SAVE_DIR` in `.env`.

## Requirements

- **Docker version:** Windows with Docker Desktop, or Linux with Docker Engine.
  Compose v2. No sound card, no screen, no GPU. **Intel or AMD only**, because
  the image is amd64
- **Windows native version:** Windows, VB-CABLE and pixi. No GPU
- Python 3.12, built with pixi. The version is pinned because the audio
  libraries have wheels for it. The container builds the same environment from
  the same `pixi.lock`
- An OpenAI API key. Transcription and translation both use it. Transcription
  costs $0.017 per minute, and silence is billed too. Translation costs much
  less

## Licence

BSD 3-Clause. See [LICENSE](LICENSE).

Zoom, VB-CABLE, the OpenAI API, and the libraries this project depends on each
have their own terms. This licence covers only the code in this repository.
