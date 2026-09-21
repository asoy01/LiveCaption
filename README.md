# LiveCaption

Real-time subtitles for meetings, in the other language.

LiveCaption listens to the meeting audio, recognises what people say, translates
it, and shows the result to the people who need it. You choose the direction for
each meeting: **English captions for a meeting held in Japanese**, or **Japanese
captions for a meeting held in English**. When the other language comes up during
the meeting, it is passed through instead of being translated.

**It is not limited to Zoom.** It reads whatever the meeting software plays, so
it works with any of them.

## Quick start (Docker)

You need a Linux machine with Docker, an OpenAI API key, and a Tailscale
account. **Nothing else runs on the host:** the meeting client, the audio
devices, the screen and Tailscale are all inside the container.

```sh
git clone https://github.com/asoy01/LiveCaption.git
cd LiveCaption
cp .env.example .env          # then put your OPENAI_API_KEY in it
docker compose up -d --build
docker compose logs -f        # the address of the control page appears here
```

Open the control page in a browser on any machine in your tailnet:

```
https://livecaption.<your-tailnet>.ts.net:8443
```

Everything is done from there: schedule a meeting and let it join Zoom by
itself, start and stop caption generation, register the Zoom token, hand out a
URL to participants, upload glossary tables, download the meeting record, and
open a VNC view of the container when the meeting client needs attention.

**Do not screen-share the control page.** It shows the Zoom caption token.
Share the viewer page instead.

The container comes back by itself after a crash or a host reboot.

Full instructions are in [docs/manual.md](docs/manual.md), chapter 2.

## Quick start (Windows caption PC)

**This way is frozen.** It still works and nothing was removed, but new
features go into the Docker version only. It needs a Windows PC used only for
captions, [VB-CABLE](https://vb-audio.com/Cable/), [pixi](https://pixi.sh), and
an OpenAI API key.

```powershell
git clone https://github.com/asoy01/LiveCaption.git
cd LiveCaption
pixi install
copy .env.example .env        # then put your OPENAI_API_KEY in it
pixi run python scripts/cable_loopback.py   # check the VB-CABLE path
```

Then double-click `StartLiveCaption.bat`. See chapter 3 of the manual.

## Three ways to show the captions

You can use all three at the same time.

| Output | Host rights | How people see it | Leaves your network |
|---|---|---|---|
| Zoom caption API | Required | Each person turns on "Show Captions" | Through Zoom |
| Screen share | Not required | You share the viewer page full screen | No |
| Hand out a URL | Not required | People open a URL on their own device | Through Tailscale Funnel, or Cloudflare on Windows |

## How it works

LiveCaption joins the meeting as a silent participant, with its microphone
muted. In Docker, the meeting client lives in the container:

```
[host PC]        runs the meeting as usual
     |
[container]
  Zoom ---------------- joins as a silent participant, microphone muted
   |
   +-- output --> meeting        (a PulseAudio null sink)
                     |
                  meeting.monitor --> LiveCaption
                                        |-- speech recognition (gpt-live-transcribe)
                                        |-- translation (gpt-4.1-mini)
                                        +-- output
```

**A separate participant is required.** Meeting software does not send your own
microphone to your own speaker, so recording the speaker output on the host PC
loses the host's own voice. A separate participant receives the mixed audio, and
that mix contains everybody.

**The audio never touches the host.** A null sink inside the container carries
it, so the host needs no sound card, and playing music on the host changes
nothing. On the Windows caption PC, VB-CABLE plays that part instead.

Delay from speech to caption is about 1.5–2 seconds for a sentence that ends
with an end mark, and about 4 seconds for one that trails off into silence.

## Handling technical terms

Technical terms are handled in two steps, not one.

1. **Recognition.** The glossary is passed to the recogniser as keywords, so it
   is more likely to hear a term correctly.
2. **Translation.** The whole glossary goes into the translation prompt,
   together with replacement rules built from misrecognitions seen in real
   meetings, and the last few sentences as context. **This is the step that does
   the work.** Even when the recogniser produces something that only sounds
   similar, the translation step can recover the correct term.

The glossary files live in `etc/glossary/`, one file per subject, and **you pick
which ones to use for each meeting**. Start from
[docs/glossary-example.tsv](docs/glossary-example.tsv) and replace the words with
the ones your own meetings use. Growing these tables is the main ongoing task.

**`etc/glossary/` is not in Git, so a fresh clone has none.** Personal names and
organisation names make the tables work better, and you do not want to share
those. In Docker you upload your tables from the control page; they are kept in
a volume and survive recreating the container.

## Documentation

| Document | Contents |
|---|---|
| [docs/manual.md](docs/manual.md) | Manual: install, run a meeting, the glossary, options, troubleshooting |
| [docs/manual.ja.md](docs/manual.ja.md) | The same manual in Japanese |
| [docs/test-procedure.md](docs/test-procedure.md) | Staged test for bringing up a new caption PC, and the checklist for the day |
| [docs/glossary-example.tsv](docs/glossary-example.tsv) | A sample glossary table. Copy it into `etc/glossary/` |

## Layout

```
compose.yml              what you run for the Docker version
docker/Dockerfile        how the container is built
docker/entrypoint.sh     audio, screen, Tailscale and Zoom set-up inside it
StartLiveCaption.bat     what you double-click on a Windows caption PC
InstallToStartMenu.bat   puts LiveCaption in the Start menu (run once)
run.py                   start-up and command line options
src/live_caption/        the application
scripts/                 one-off measurement and check scripts
docs/                    manual, test procedure
etc/                     the application icon, and your glossary tables (not in git)
data/recordings/         audio used for comparing recognisers (not in git)
local/                   working files (not in git)
```

The record of each meeting is written to **`local/transcripts/`** as
`live-caption_<date>.jsonl` and `.md`. **You download it from the control
page**, so you do not have to reach the machine to read it. In Docker it lives
in the `state` volume. To keep the records somewhere else on Windows, set
`LIVECAPTION_SAVE_DIR` in `.env`.

## Requirements

- **Docker version:** a Linux machine with Docker and Compose v2. No sound
  card, no screen, no GPU
- **Windows version:** Windows and VB-CABLE. No GPU
- Python 3.12, built with pixi. The version is pinned because the audio
  libraries have wheels for it. The container builds the same environment from
  the same `pixi.lock`
- An OpenAI API key. Speech recognition and translation both use it.
  Recognition costs $0.017 per minute, and silence is billed too

## Licence

BSD 3-Clause. See [LICENSE](LICENSE).

The libraries this project depends on, Zoom, VB-CABLE, and the OpenAI API each
come with their own terms. This licence covers only the code in this repository.
