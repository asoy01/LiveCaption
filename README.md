# LiveCaption

Real-time subtitles for meetings, in the other language.

LiveCaption listens to the meeting audio, recognises what people say, translates
it, and shows the result to the people who need it. You choose the direction for
each meeting: **English captions for a meeting held in Japanese**, or **Japanese
captions for a meeting held in English**. When the other language comes up during
the meeting, it is passed through instead of being translated.

**It is not limited to Zoom.** It reads the audio from a virtual audio cable, so
it works with any meeting software that plays sound.

## Quick start

You need a Windows PC used only for captions,
[VB-CABLE](https://vb-audio.com/Cable/), [pixi](https://pixi.sh), and an OpenAI
API key.

```powershell
git clone https://github.com/asoy01/LiveCaption.git
cd LiveCaption
pixi install
copy .env.example .env        # then put your OPENAI_API_KEY in it
pixi run python scripts/cable_loopback.py   # check the VB-CABLE path
```

`.env` stays in this folder, the top of the repository, next to `pixi.toml`.

Then double-click `StartLiveCaption.bat`. The control page opens in your
browser. Everything is done from there: start and stop caption generation,
choose the input device and the direction, register the Zoom token, hand out a
URL to participants, read the meeting record, and quit.

**Do not screen-share the control page.** It shows the Zoom caption token.
Share the viewer page instead.

To start it from the Start menu instead of finding this folder every time,
double-click `InstallToStartMenu.bat` once. After that, press the Windows key,
type "livecaption", and press Enter.

Full instructions are in [docs/manual.md](docs/manual.md).

## Three ways to show the captions

You can use all three at the same time.

| Output | Host rights | How people see it | Leaves your network |
|---|---|---|---|
| Zoom caption API | Required | Each person turns on "Show Captions" | Through Zoom |
| Screen share | Not required | You share the viewer page full screen | No |
| Hand out a URL | Not required | People open a URL on their own device | Through Cloudflare |

## How it works

One Windows PC is used only for captions. That PC joins the meeting as a silent
participant, with its microphone muted.

```
[host PC]        runs the meeting as usual
     |
[caption PC]
  meeting software ----- joins as a silent participant, microphone muted
   |
   +-- speaker output --> CABLE Input
                             |   (VB-CABLE, a virtual audio cable)
                          CABLE Output --> LiveCaption
                                             |-- speech recognition (gpt-live-transcribe)
                                             |-- translation (gpt-4.1-mini)
                                             +-- output
```

**A separate PC is required.** Meeting software does not send your own
microphone to your own speaker, so recording the speaker output on the host PC
loses the host's own voice. The caption PC receives the mixed audio, and that mix
contains every participant.

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

**`etc/glossary/` is not in Git.** Personal names and organisation names make the
tables work better, and you do not want to share those.

## Documentation

| Document | Contents |
|---|---|
| [docs/manual.md](docs/manual.md) | Manual: install, run a meeting, the glossary, options, troubleshooting |
| [docs/manual.ja.md](docs/manual.ja.md) | The same manual in Japanese |
| [docs/test-procedure.md](docs/test-procedure.md) | Staged test for bringing up a new caption PC, and the checklist for the day |
| [docs/glossary-example.tsv](docs/glossary-example.tsv) | A sample glossary table. Copy it into `etc/glossary/` |

## Layout

```
StartLiveCaption.bat     what you double-click for a meeting
InstallToStartMenu.bat   puts LiveCaption in the Start menu (run once)
run.py                   start-up and command line options
src/live_caption/        the application
scripts/                 one-off measurement and check scripts
docs/                    manual, test procedure
etc/                     the application icon, and your glossary tables (not in git)
data/recordings/         audio used for comparing recognisers (not in git)
local/                   working files (not in git)
```

The record of each meeting is written to **your Downloads folder** as
`live-caption_<date>.jsonl` and `.md`. Nothing is written inside the repository.

## Requirements

- Windows. No GPU needed
- Python 3.12, built with pixi. The version is pinned because the audio
  libraries have wheels for it
- An OpenAI API key. Speech recognition and translation both use it.
  Recognition costs $0.017 per minute, and silence is billed too

## Licence

BSD 3-Clause. See [LICENSE](LICENSE).

The libraries this project depends on, VB-CABLE, and the OpenAI API each come
with their own terms. This licence covers only the code in this repository.
