# LiveCaption

Real-time translated subtitles for meetings. LiveCaption listens to the meeting
audio, transcribes what people say, translates it, and shows the captions to
the participants who need them.

You choose the direction for each meeting: **English captions for a meeting
held in Japanese**, or **Japanese captions for a meeting held in English**.

## Where to start

| What you want | Where to read |
|---|---|
| Getting it running on one Windows PC | [Manual, sections 1 and 2](manual.md) |
| What to do on the day of a meeting | [Manual, section 3](manual.md) |
| Letting it run with nobody watching | [Manual, section 4](manual.md) |
| Running it on Linux | [Manual, Appendix A](manual.md) |
| The Tailscale account | [Manual, Appendix B](manual.md) |
| How it works inside | [Manual, Appendix E](manual.md) |
| Bringing up a new caption PC | [Test procedure](test-procedure.md) |

**The manual is one page per language.** Use the table of contents on the right
to move around it, or the search box at the top.

## What you need

One Windows or Linux machine with Docker, an OpenAI API key, and a Tailscale
account. **One laptop is enough**: you join the meeting as usual, while the
Zoom inside the container joins the same meeting as a silent participant.

Transcription costs about one US dollar per hour of meeting.

## Getting it

[Download the ZIP](https://github.com/asoy01/LiveCaption/archive/refs/heads/main.zip)
and follow [section 2 of the manual](manual.md). The same manual is in the
download, so you can read it offline.

## Licence

BSD 3-Clause. See [LICENSE](https://github.com/asoy01/LiveCaption/blob/main/LICENSE).

Zoom, VB-CABLE, the OpenAI API, and the libraries this project depends on each
have their own terms. This licence covers only the code in this repository.
