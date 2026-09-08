# LiveCaption

Real-time English subtitles for meetings held in Japanese.

LiveCaption listens to the meeting audio, recognises the Japanese speech,
translates the speech into English, and shows the English text to the people who
need it. It was written for KAGRA meetings, where a few participants do not
speak Japanese.

**It is not limited to Zoom.** It reads the audio from a virtual audio cable, so
it works with any meeting software that plays sound.

## How it works

One Windows PC is used only for captions. That PC joins the meeting as a silent
participant, with its microphone muted.

```
[host PC]        runs the meeting as usual
     |
[caption PC]
  Zoom ----- joins as a silent participant, microphone muted
   |
   +-- speaker output --> CABLE Input
                             |   (VB-CABLE, a virtual audio cable)
                          CABLE Output --> LiveCaption
                                             |-- speech recognition (gpt-live-transcribe)
                                             |-- translation (gpt-4.1-mini)
                                             +-- output
```

**A separate PC is required.** Zoom does not send your own microphone to your
own speaker, so recording the speaker output on the host PC loses the host's
own voice. The caption PC receives the mixed audio from Zoom, and that mix
contains every participant.

Delay from speech to caption is about 3 seconds.

## Three ways to show the captions

You can use all three at the same time.

| Output | Host rights | How people see it | Leaves your network |
|---|---|---|---|
| Zoom caption API | Required | Each person turns on "Show Captions" | Through Zoom |
| Screen share | Not required | You share the viewer page full screen | No |
| Hand out a URL | Not required | People open a URL on their own device | Through Cloudflare |

## Handling technical terms

Technical terms are handled in two steps, not one.

1. **Recognition.** The glossary is passed to the recogniser as keywords, so it
   is more likely to hear a term correctly.
2. **Translation.** The whole glossary goes into the translation prompt,
   together with replacement rules built from misrecognitions seen in real
   meetings, and the last few sentences as context. **This is the step that does
   the work.** Even when the recogniser produces something that only sounds
   similar, the translation step can recover the correct term.

The glossary is `docs/glossary.tsv`. Growing it is the main ongoing task.

## Quick start

You need a Windows PC, [VB-CABLE](https://vb-audio.com/Cable/),
[pixi](https://pixi.sh), and an OpenAI API key.

```bash
pixi install
cp .env.example .env          # then put your OPENAI_API_KEY in it
pixi run python scripts/cable_loopback.py   # check the VB-CABLE path
```

Then double-click `StartLiveCaption.bat`. The control page opens in your
browser. Everything is done from there: start and stop caption generation,
choose the input device, register the Zoom token, hand out a URL to
participants, read the meeting record, and quit.

**Do not screen-share the control page.** It shows the Zoom caption token.
Share the viewer page instead.

## Documentation

| Document | Contents |
|---|---|
| [docs/manual.md](docs/manual.md) | Manual: install, run a meeting, the glossary, options, troubleshooting |
| [docs/manual.ja.md](docs/manual.ja.md) | The same manual in Japanese |
| [docs/test-procedure.md](docs/test-procedure.md) | Staged test for bringing up a new caption PC, and the checklist for the day |
| [docs/glossary.tsv](docs/glossary.tsv) | The term table |

## Layout

```
StartLiveCaption.bat   what you double-click for a meeting
run.py                 start-up and command line options
src/live_caption/      the application
scripts/               one-off measurement and check scripts
docs/                  manual, test procedure, glossary
data/recordings/       audio used for comparing recognisers (not in git)
local/                 working files (not in git)
```

The record of each meeting is written to **your Downloads folder** as
`live-caption_<date>.jsonl` and `.md`. Nothing is written inside the repository.

## Status

The application works. All four stages of the bring-up test passed on
2026-09-07, and English captions were seen on a participant's screen in a real
Zoom meeting.

Not done yet:

- A meeting with real non-Japanese participants. Every test so far was one
  person speaking alone
- A run of a full meeting, 30 minutes or longer, with participants opening the
  handed-out URL on their own devices

## Requirements

- Windows. No GPU needed
- Python 3.12, built with pixi. The version is pinned because the audio
  libraries have wheels for it
- An OpenAI API key. Speech recognition and translation both use it

## Licence

Not decided yet.
