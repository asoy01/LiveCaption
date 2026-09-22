"""How the whole thing fits together.

    CABLE Output (or a WAV file)
       | 24 kHz mono PCM, 100 ms at a time
    gpt-live-transcribe (returns deltas continuously)
       |
    Segmenter (cuts sentences; the model returns no boundaries)
       |
    Translator (gpt-4.1-mini; glossary plus replacement rules; English is
                passed through unchanged)
       | one line at a time
    Zoom caption API (POST, saving seq as we go)

Translation takes about 0.9 seconds per sentence (measured, 2026-09-09). In a
meeting, sentences can arrive faster than that, so **we start translating the
next sentence while one is still being translated, but we keep the order in
which we send them.** Captions out of order cannot be read.

**Sentences that become final through silence (about 8.6%) are translated
ahead of time, without waiting for them to be final.** `IDLE_FLUSH_SEC`
cannot go lower (the p99 of the gap between deltas is above the threshold),
so translating during that wait is the only way to make this path faster.

The host's own screen shows no captions, so the log on this screen is the
only way to check that the system is working. Always print what we sent.

There are two ways out for the captions. **You can use either one, or both
at the same time.**

1. The Zoom caption API (`captions.py`). Needs a token taken with host rights
2. Browser captions (`web.py`). Needs no rights. Show them by sharing your
   screen
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path

from . import asr as asr_mod
from . import audio as audio_mod
from . import captions as captions_mod
from . import config, glossary
from . import schedule as schedule_mod
from . import segmenter as segmenter_mod
from . import transcript as transcript_mod
from . import translator as translator_mod
from . import vnc as vnc_mod

# How many translations run at the same time. A larger number still keeps
# the order, but it makes the delay harder to see.
MAX_INFLIGHT = 4


def now() -> str:
    return time.strftime("%H:%M:%S")


def _bare(text: str) -> str:
    """Drop trailing sentence-ending marks and spaces. Used to match a
    speculative translation against the final sentence."""
    return text.rstrip("。！？.!? 　")


class ZoomControl:
    """Lets the browser control page start and stop the Zoom captions.

    **The caller is a thread of the HTTP server.** That is not the main
    event loop, so we hand coroutines (sending the throwaway captions) to it
    with `run_coroutine_threadsafe`.

    The token cannot be taken until the meeting starts. So it cannot be
    decided at startup, and this class lets us set it later.
    """

    def __init__(self, sender: captions_mod.CaptionSender) -> None:
        self.sender = sender
        self.loop: asyncio.AbstractEventLoop | None = None
        # Called when the state changes. Used to keep the browser display in
        # step.
        self.on_change = None

    def status(self) -> dict:
        return self.sender.status()

    def set_token(self, url: str) -> dict:
        """Set the token. Raise captions.TokenError if it is not valid."""
        meeting = self.sender.set_token(url)
        print(f"[{now()}] Zoom        got the token (meeting {meeting}, "
              f"seq starts at {self.sender.seq})")
        self._changed()
        return self.status()

    def set_enabled(self, on: bool) -> dict:
        """Start or stop sending to Zoom."""
        if on and self.sender.dry_run:
            raise captions_mod.TokenError(
                "--dry-run で起動しているので、Zoomへは送らない。"
                "送るなら --dry-run を外して起動し直すこと。"
            )
        if on and not self.sender.base_url:
            raise captions_mod.TokenError("先にトークンを入れること。")

        was_active = self.sender.active
        self.sender.set_enabled(on)
        print(f"[{now()}] Zoom        {'started' if on else 'stopped'} sending")

        # When we start something that was stopped, begin with the throwaway
        # captions. The receiving side cannot turn on "show captions" until
        # captions start arriving.
        if on and not was_active and self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.sender.warmup(), self.loop)
        self._changed()
        return self.status()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self.status())


class EngineControl:
    """Start and stop **the caption generation itself** (recording, speech
    recognition, translation).

    This is a separate gate from sending to Zoom. Stopping here closes the
    audio device and cuts the recognition WebSocket. **No audio is captured,
    and no API is called.**

    When the app starts with a control page (`--web`), **it begins in the
    stopped state.** That lets you start the app before you join the
    meeting, without sending the small talk of the setup to recognition.

    The caller is a thread of the HTTP server.
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {"generating": self.app.generating}

    def set_running(self, on: bool) -> dict:
        self.app.set_generating(on)
        print(f"[{now()}] engine      caption generation "
              f"{'started' if on else 'stopped'}")
        return self.status()


class AudioControl:
    """List the input devices and switch between them.

    **Noticing a wrong choice only when someone speaks is too late.** The
    list also shows the host API (on Windows the same name appears under
    MME, DirectSound and WASAPI).
    While generating, it also returns the recent peak amplitude, so you can
    see with your eyes whether sound is arriving.

    The caller is a thread of the HTTP server.
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def devices(self) -> dict:
        """The inputs you can choose. With --from-file there is no choice."""
        capture = self.app.capture
        if not isinstance(capture, audio_mod.Capture):
            return {"selectable": False, "devices": [], "index": None,
                    "name": "ファイル（--from-file）"}
        return {
            "selectable": True,
            "index": capture.device,
            "name": audio_mod.describe_device(capture.device),
            "devices": [
                {"index": i, "name": name, "channels": ch, "api": api}
                for i, name, ch, api in audio_mod.list_devices()
            ],
        }

    def status(self) -> dict:
        capture = self.app.capture
        st = {
            "selectable": isinstance(capture, audio_mod.Capture),
            "name": "—",
            "index": None,
            "level": 0.0,
            "dropped": 0,
            "error": self.app.audio_error,
        }
        if capture is None:
            return st
        st["dropped"] = getattr(capture, "dropped", 0)
        if st["selectable"]:
            st["index"] = capture.device
            st["name"] = audio_mod.describe_device(capture.device)
            # Outside generation the device is closed, so the meter does not
            # move.
            if self.app.generating:
                st["level"] = round(capture.take_peak(), 4)
        else:
            st["name"] = "ファイル（--from-file）"
        return st

    def set_device(self, index: int | None) -> dict:
        """Switch the input. **While generating, close it and open it
        again.**

        You sometimes notice in the middle of a meeting that no sound is
        arriving. At that point we do not want to make you take three steps:
        stop, choose, start again.
        """
        capture = self.app.capture
        if not isinstance(capture, audio_mod.Capture):
            raise ValueError("--from-file で起動しているので、入力は選べない。")
        if index is not None:
            known = {i for i, _n, _c, _a in audio_mod.list_devices()}
            if index not in known:
                raise ValueError(
                    f"There is no input device {index}. Refresh the list.")

        capture.device = index
        self.app.audio_error = ""
        print(f"[{now()}] audio       input set to "
              f"{audio_mod.describe_device(index)}")
        if self.app.generating:
            # Keep generating, and reopen only the audio device and the
            # recognition connection.
            self.app.request_restart()
        return self.status()


class GlossaryControl:
    """List the glossaries and choose different ones.

    **Different meetings use different words.** You stack the per-subsystem
    glossaries you need, and no more.
    Using one big glossary for every meeting means unrelated words eat the
    recognition keywords, and the words you actually need fall off the end
    at the limit.

    The caller is a thread of the HTTP server.
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {
            "sets": glossary.available(),
            "selected": list(self.app.glossary_names),
            "terms": len(self.app.entries),
            "keywords": len(self.app.keywords),
            "limit": config.ASR_KEYWORD_LIMIT,
            "dropped": self.app.dropped_keywords,
        }

    def select(self, names: list[str]) -> dict:
        """Choose different glossaries. **While generating, reconnect
        recognition so it gets the new keywords.**

        keywords can only be sent when a session starts. Without
        reconnecting, only translation would use the new glossary, and
        recognition would stay on the old one.
        """
        known = {s["name"] for s in glossary.available()}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise ValueError(f"用語集が無い: {', '.join(unknown)}")
        self.app.apply_glossary(names)
        return self.status()

    def read(self, name: str) -> tuple[str, str]:
        """Return the content of a glossary, as `(name, text)`. Used for
        download."""
        return glossary.check_name(name), glossary.read_text(name)

    def upload(self, name: str, text: str) -> dict:
        """Store a glossary. Replace one of the same name.

        **When a glossary in use is replaced, load it again right away.**
        The worst case is a meeting that goes on with the old content after
        you replaced it.
        """
        stem = glossary.save_text(name, text)
        if stem in self.app.glossary_names:
            self.app.apply_glossary(list(self.app.glossary_names))
        return self.status()

    def remove(self, name: str) -> dict:
        """Delete a glossary. If it was in use, drop it from the selection
        and load the rest again."""
        stem = glossary.delete_file(name)
        if stem in self.app.glossary_names:
            self.app.apply_glossary(
                [n for n in self.app.glossary_names if n != stem])
        return self.status()


class TuningControl:
    """Lets the control page change the delay tuning knobs.

    **These are not changed often.** The defaults come from measurement. We
    keep this section collapsed on the control page.

    The caller is a thread of the HTTP server.
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {
            "items": config.tuning(),
            "warning": config.tuning_warning(),
            "env_path": str(config.ENV_PATH),
        }

    def set(self, values: dict) -> dict:
        """Change the values. **They take effect at once. No restart
        needed.**

        A `ValueError` is shown to the person who made the change.
        """
        # **Check every value before setting any of them.** If one is
        # rejected halfway, we must not end up half changed. We set them
        # through `config.set_tuning`, which also remembers whether a person
        # set the value explicitly (to protect it from a direction switch).
        checked = {name: config.coerce_tuning(name, raw) for name, raw in values.items()}
        for name, value in checked.items():
            config.set_tuning(name, value)
        # **`Segmenter` copied the values when it was created.** Without
        # handing them to it, only the numbers on the page would change, and
        # it would keep cutting the old way.
        self.app.segmenter.idle_sec = config.IDLE_FLUSH_SEC
        self.app.segmenter.force_cut = config.FORCE_CUT_CHARS
        if checked:
            print(f"[{now()}] config      " + ", ".join(
                f"{n} set to {v}" for n, v in checked.items()))
        return self.status()

    def save(self) -> dict:
        """Write the current values to `.env`, so the next start uses the
        same values."""
        written = {item["env"]: str(item["value"]) for item in config.tuning()}
        path = config.save_env(written)
        print(f"[{now()}] config      saved to {path}")
        st = self.status()
        st["saved"] = str(path)
        return st


class DirectionControl:
    """Switching the caption direction.

    **You choose one for each meeting.** English captions for a Japanese
    meeting, Japanese captions for an English meeting.
    Unlike the glossary, switching does not reconnect recognition
    (`keywords` does not change with the direction).

    The caller is a thread of the HTTP server.
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {
            "current": config.DIRECTION,
            "items": [
                {"name": d.name, "label": d.label, "lang": d.caption_lang}
                for d in config.DIRECTIONS.values()
            ],
        }

    def select(self, name: str) -> dict:
        """Choose a different direction. **It takes effect at once. No
        restart and no reconnect.**"""
        self.app.apply_direction(str(name))
        return self.status()


class RecordControl:
    """The meeting record. **It collects on the caption PC, and you download
    it from the control page** (2026-09-20).

    It goes in `local/transcripts/`. **The page cannot change that.** The
    caption PC runs all the time, and we operate it over the tailnet. The
    original problem was that starting a remote desktop session just to
    fetch a record is a nuisance, so **once you can download it, there is no
    reason left to move the folder.** Anyone who wants to change it uses
    `LIVECAPTION_SAVE_DIR` in `.env`.

    (On the morning of 2026-09-20, the folder could be chosen from the page:
    a folder picker, and a list of folders for remote use. Both were removed
    once downloading worked.)

    The caller is a thread of the HTTP server.
    """

    def __init__(self, app: "App") -> None:
        self.app = app

    def status(self) -> dict:
        return {
            "dir": str(self.app.settings.transcript_dir),
            "saving": self.app.settings.save,
        }

    def latest_path(self) -> Path | None:
        """The `.jsonl` you can download. **The newest one that has content
        in it.**

        **Skip the empty ones.** When a meeting ends, the app opens the next
        record and waits (`roll_transcript`). Returning the newest file as
        it is would hand you a record with not one sentence in it when you
        download after a meeting.

        We do not accept a string from the page that names a file. **What we
        return is decided here.** The control page has no authentication, so
        we do not give it a way to pass a path.
        """
        directory = Path(self.app.settings.transcript_dir)
        for item in transcript_mod.scan(directory):
            if item["lines"]:
                return directory / f"{item['stem']}.jsonl"
        return None

    def latest(self) -> dict:
        """Tell the page which record can be downloaded. `item` is None when
        there is none."""
        path = self.latest_path()
        item = None
        if path is not None:
            for found in transcript_mod.scan(Path(self.app.settings.transcript_dir)):
                if found["stem"] == path.stem:
                    item = found
                    break
        return {"saving": self.app.settings.save, "item": item}


class App:
    def __init__(self, settings: config.Settings, web=None) -> None:  # noqa: ANN001
        self.settings = settings
        self.web = web
        # **Decide the direction before anything else.** `Segmenter` copies
        # `FORCE_CUT_CHARS` when it is created, and `Translator` picks its
        # prompt from the direction. Replacing it later would leave those
        # two, and only those two, on the old direction.
        self.direction = config.apply_direction(
            settings.direction or config.direction_selection()
        )
        # The control page language. **It has nothing to do with the caption
        # direction.** You may put out Japanese captions for an English
        # meeting while the control page is in English.
        config.apply_ui_lang(config.ui_lang_selection())
        # **The glossaries in use are decided by name.** With nothing given
        # at startup, we use the last choice.
        self.glossary_names: tuple[str, ...] = tuple(
            settings.glossary_names if settings.glossary_names is not None
            else glossary.selection()
        )
        entries = glossary.load(self.glossary_names)
        self.entries = entries
        # Words cut off at the limit never reach the recognition stage. We
        # do not drop them silently; we print how many on the startup screen
        # (on 2026-09-07, 27 words were dropped and nobody noticed).
        every = glossary.keywords(entries, limit=None)
        self.keywords = every[: config.ASR_KEYWORD_LIMIT]
        self.dropped_keywords = len(every) - len(self.keywords)
        self.asr = asr_mod.Asr(
            keywords=self.keywords,
            delay=settings.delay,
            languages=settings.languages,
        )
        self.segmenter = segmenter_mod.Segmenter()
        self.translator = translator_mod.Translator(entries, settings.translate_model)
        self.sender = captions_mod.CaptionSender(
            settings.caption_url or None, dry_run=settings.dry_run
        )
        self.zoom = ZoomControl(self.sender)
        self.engine = EngineControl(self)
        self.audio = AudioControl(self)
        self.glossary = GlossaryControl(self)
        self.tuning = TuningControl(self)
        self.dir_control = DirectionControl(self)
        # The scheduler that runs scheduled meetings with nobody watching
        # (schedule.py).
        self.scheduler = schedule_mod.Scheduler(self)
        # VNC (vnc.py). **Only usable when running in a container.**
        # On Windows there is no DISPLAY, so the control page says it is not
        # available.
        self.vnc = vnc_mod.Vnc()
        # Set in run(). We keep it so the control page can switch the input.
        self.capture = None
        # Why the audio device could not be opened. Cleared once it opens.
        self.audio_error = ""
        # When the last sentence became final (`time.monotonic`). The
        # silence watchdog uses it.
        # **We look at sentences, not at the volume.** The reason is in
        # `_dispatch`.
        self.last_sentence_at = time.monotonic()
        # Whether caption generation (recording, recognition, translation)
        # is running.
        # **With a control page, we begin in the stopped state.** That lets
        # you start the app before you join the meeting. Without one there
        # is no button to press, so we start at once (run.py decides and
        # passes `start_now`).
        self.generating = False
        # These tell _pipeline to start and to stop. When one is set, the
        # other is cleared.
        self._gen_on = asyncio.Event()
        self._gen_off = asyncio.Event()
        self._gen_off.set()
        # Set when the input device is switched. Generation continues, and
        # only the audio device and recognition are opened again.
        self._restart = asyncio.Event()
        # When we last handed the unfinished transcript to the viewer page
        # (`_show_partial`).
        self._partial_at = 0.0
        # The meeting record. **We keep the recognized sentence and the
        # caption as a pair.**
        # To pair them, we carry the translation task and the original
        # sentence together (_dispatch / _post).
        self.transcript = (
            transcript_mod.Transcript(settings.transcript_dir, meta=self._transcript_meta())
            if settings.save
            else None
        )
        if web is not None:
            # Let the browser control page start and stop the Zoom captions.
            web.control = self.zoom
            # Let it start and stop the caption generation itself.
            web.engine = self.engine
            # The list of input devices, and switching between them.
            web.audio = self.audio
            # The list of glossaries, and choosing different ones.
            web.glossary = self.glossary
            # The delay tuning knobs. Rarely changed, so the page keeps this
            # collapsed.
            web.tuning = self.tuning
            # The caption direction. Chosen per meeting.
            web.direction = self.dir_control
            # Shut the caption app down from the same page.
            web.on_shutdown = self.request_stop
            # Show on the control page that records have collected.
            web.transcript = self.transcript
            # Where the records go. The folder picker also opens from here.
            web.records = RecordControl(self)
            # The state of the schedule, and stopping, skipping and clearing
            # failures.
            web.scheduler = self.scheduler
            # VNC. **It can be started and stopped during a meeting.**
            web.vnc = self.vnc
            # **Not started by default.** It has no authentication, so you
            # open it from the page when you need it.
            if os.environ.get(config.VNC_ENV, "").strip().lower() in (
                    "1", "true", "yes", "on"):
                st = self.vnc.start()
                if st["on"]:
                    print(f"VNC:          started on port {st['web_port']}. "
                          "**There is no authentication.**")
                else:
                    print(f"VNC:          cannot start ({st['error']})")
        self.sentences: asyncio.Queue[segmenter_mod.Cut] = asyncio.Queue()
        self.inflight: asyncio.Queue = asyncio.Queue(maxsize=MAX_INFLIGHT)
        self.stats = {"sentences": 0, "lines": 0}
        # The speculative translation. We hold exactly one
        # (text we sent, task).
        self._spec: tuple[str, asyncio.Task] | None = None
        # Set when something outside asks us to shut down. run() watches it
        # and starts cleaning up.
        self.stop_requested = asyncio.Event()

    def request_stop(self, reason: str = "outside") -> None:
        """Shut the caption app down. **Called from another thread.**

        It comes from the browser control page (a thread of the HTTP
        server), so we hand the Event to the main event loop with
        `call_soon_threadsafe`.
        It lands in the same place as Ctrl+C. run.py does the cleanup.
        """
        print(f"[{now()}] shutdown    asked to stop by {reason}")
        loop = self.zoom.loop
        if loop is None:
            self.stop_requested.set()
        else:
            loop.call_soon_threadsafe(self.stop_requested.set)

    def _transcript_meta(self) -> dict:
        """The information written at the top of a record.

        **Build it from the current state on every call.** We switch records
        per meeting, so reusing the values from startup would make a record
        lie after the direction or the glossary was changed part way
        through.
        """
        return {
            "asr": config.ASR_MODEL,
            "delay": self.settings.delay,
            "languages": ",".join(self.settings.languages),
            "translate": self.settings.translate_model,
            # The direction we started with. **It can change part way
            # through, so we also record it for each sentence.**
            "direction": self.direction.name,
            "glossary": len(self.entries),
            "glossary_sets": ", ".join(self.glossary_names) or "(なし)",
            "dry_run": self.settings.dry_run,
        }

    def roll_transcript(self, label: str = "") -> Path | None:
        """Close the current record, write the `.md`, and open the next one.
        Return the path we closed.

        **Do not wait for the process to end.** When the app runs all the
        time, `report()` is called weeks later. Until then not one `.md`
        would be written, and dozens of meetings would collect in a single
        `.jsonl`. We cut the record here at the end of each meeting.

        **Replace `web.transcript` too.** Without that, the control page
        gets the wrong idea of which record we are writing.

        `close()` deletes a record with no sentences in it, so calling this
        both before and after a meeting leaves no empty file.

        Only the main event loop calls this (the scheduler, and
        `_close_record_soon`, which runs a little after a stop).
        """
        if self.transcript is None:
            return None
        done = self.transcript.close()
        if done is not None:
            print(f"[{now()}] record      wrote: {done}")
        self.transcript = transcript_mod.Transcript(
            self.settings.transcript_dir, meta=self._transcript_meta(), label=label)
        self.transcript.open()
        if self.web is not None:
            self.web.transcript = self.transcript
        return done

    def set_generating(self, on: bool) -> None:
        """Start or stop caption generation. **Called from another thread.**

        We set `generating`, which the display uses, right here. The control
        page redraws its state from the answer to the request, so waiting
        for the event loop would make it look one beat behind.
        `_pipeline` does the actual start and stop (the audio device and the
        recognition connection).
        """
        if on == self.generating:
            return
        if on:
            # **Do not carry an earlier failure forward.** `audio_error` is
            # cleared only when the device opens and when the device is
            # switched. Left in place, the page would keep showing an old
            # failure, and **you could not tell from the state whether it
            # started** (with nobody watching, that is the only clue you
            # have).
            self.audio_error = ""
            # The starting point for the silence watchdog. Do not count the
            # time while it was stopped.
            self.last_sentence_at = time.monotonic()
        self.generating = on
        loop = self.zoom.loop
        if loop is None:
            self._flip(on)
        else:
            loop.call_soon_threadsafe(self._flip, on)

    def apply_glossary(self, names: list[str] | tuple[str, ...]) -> None:
        """Choose different glossaries and apply them to both recognition
        and translation.

        **Called from another thread.**

        The translation prompt can be rebuilt on the spot. The `keywords`
        for recognition can only be sent when a session starts, so while
        generating we reconnect.
        """
        names = tuple(names)
        entries = glossary.load(names)
        every = glossary.keywords(entries, limit=None)

        self.glossary_names = names
        self.entries = entries
        self.keywords = every[: config.ASR_KEYWORD_LIMIT]
        self.dropped_keywords = len(every) - len(self.keywords)
        self.asr.keywords = self.keywords
        # Rebuild the translation prompt. This does not affect a sentence
        # that is already being translated.
        self.translator.system = translator_mod.build_system(entries)
        glossary.remember(names)

        label = ", ".join(names) or "(none)"
        print(f"[{now()}] glossary    glossary set to {label} "
              f"({len(entries)} terms, {len(self.keywords)} sent to "
              "transcription)")
        if self.dropped_keywords:
            print(f"       **{self.dropped_keywords} terms were cut by the "
                  "limit. They never reach the transcription stage.**")
        if self.generating:
            # keywords can only be sent when a session starts. Reconnect.
            self.request_restart()

    def apply_direction(self, name: str) -> None:
        """Switch the caption direction. **Called from another thread.**

        **We do not reconnect recognition.** `keywords` always passes both
        the Japanese and the English side of the glossary, so changing the
        direction sends the same words in the same order. This is where it
        differs from choosing a different glossary.

        `config.apply_direction()` replaces the constants, and this method
        hands them to the three places that hold a copy: the translation
        prompt, `Segmenter` and `CaptionSender`.
        """
        d = config.apply_direction(name)
        self.direction = d
        # **Throw away the speculative translation that is running.** It is
        # being translated with the old prompt, so using it would put out
        # one caption in the wrong direction right after the switch.
        #
        # **A task can only be cancelled from inside the loop that runs
        # it.** We are in a thread of the HTTP server here, so we hand it to
        # the loop (the same shape as `set_generating`).
        loop = self.zoom.loop
        if loop is None:
            self._drop_speculation()
        else:
            loop.call_soon_threadsafe(self._drop_speculation)
        self.translator.system = translator_mod.build_system(self.entries)
        # Throw away the recent context too, because the caption language
        # changes across the switch.
        self.translator.history.clear()
        # **`Segmenter` copied the value when it was created.** Without
        # handing it over, it would keep cutting the old way.
        self.segmenter.force_cut = config.FORCE_CUT_CHARS
        self.sender.lang = d.caption_lang
        config.remember_direction(d.name)
        print(f"[{now()}] direction   captions set to {d.label} "
              f"({config.MAX_CAPTION_CHARS} chars per line, forced cut at "
              f"{config.FORCE_CUT_CHARS} chars, Zoom lang={d.caption_lang})")

    def request_restart(self) -> None:
        """Open the audio device and recognition again. **Called from
        another thread.**"""
        loop = self.zoom.loop
        if loop is None:
            self._restart.set()
        else:
            loop.call_soon_threadsafe(self._restart.set)

    def _flip(self, on: bool) -> None:
        if on:
            self._gen_off.clear()
            self._gen_on.set()
        else:
            self._gen_on.clear()
            self._gen_off.set()

    # --- The work of each stage ---------------------------------------------

    async def _pipeline(self, capture) -> None:
        """Run capture and recognition only while generation is started.

        **On stop, close the audio device and cut the recognition
        WebSocket.** While stopped, no audio may be captured and nothing may
        be sent to the API.

        On stop, throw away the text that has collected part way through a
        sentence. Throw it away without putting it out. A caption line
        appearing after the stop leaves the reader with no idea what
        happened.
        """
        while True:
            await self._gen_on.wait()
            # **People do pick a device that cannot be opened.** A USB
            # device that was unplugged, or one that does not accept 48 kHz.
            # Crashing here would stop this loop for good, with no reason
            # shown on the page. Catch it, and go back to the stopped state.
            try:
                capture.start()
            except Exception as exc:  # noqa: BLE001 - do not crash mid-meeting
                # **People do pick a device that cannot be opened.** A USB
                # device that was unplugged, or one that does not accept
                # 48 kHz. Crashing here would stop this loop for good, with
                # no reason shown on the page. Catch it, and go back to the
                # stopped state.
                self.audio_error = f"{type(exc).__name__}: {exc}"
                print(f"[{now()}] audio       cannot open the input: "
                      f"{self.audio_error}")
                print("       Pick another device under Audio input on the "
                      "control page.")
                self.generating = False
                self._flip(False)
                continue
            self.audio_error = ""
            self.asr.resume()
            asr = asyncio.create_task(self.asr.run(capture, self._on_delta))
            # If we are already sending to Zoom, this is where the stream
            # begins for the receiving side.
            if self.sender.active:
                await self.sender.warmup()

            # Leave when either a stop or an input switch arrives, whichever
            # comes first.
            # **Do not rely on the order in which the events are set.** That
            # way, a switch asked for while the throwaway captions are being
            # sent (about 1 second) is not lost.
            waits = [
                asyncio.create_task(self._gen_off.wait()),
                asyncio.create_task(self._restart.wait()),
            ]
            try:
                await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            finally:
                # **We pass through here on shutdown too.** The recognition
                # task was created separately, so without closing it the
                # connection would stay open. We do not wait here; waiting
                # can bring a second CancelledError in the middle of the
                # cancellation.
                for w in waits:
                    w.cancel()
                self.asr.stop()
                asr.cancel()
            await asyncio.gather(asr, *waits, return_exceptions=True)
            capture.stop()
            self.segmenter.reset()
            # We threw away the text that had collected, so clear the
            # unfinished line on the page too.
            self._show_partial(force=True)
            # We threw away the partial text, so throw away the speculative
            # translation of it as well.
            self._drop_speculation()
            # When we left because of a switch, _gen_on is still set, so we
            # simply open everything again.
            self._restart.clear()
            # **If we left because of a stop, cut the record.** Stop means
            # "this meeting is over" (it closes delivery and the Zoom
            # captions too), so we close the record there and write the
            # `.md`. We do not wait here; waiting would delay reopening if
            # generation starts again right away.
            if not self.generating:
                asyncio.create_task(self._close_record_soon())

    async def _close_record_soon(self) -> None:
        """Cut the record a little after the stop.

        **Do not close it right away.** At the moment you stop, the last
        sentence may still be in translation. Cutting immediately would drop
        that one sentence into the next record, and **a record with one line
        in it would become the "newest" one.**

        **If generation starts again within that time, do not cut.** If you
        only stopped for a break, the meeting record stays as one file.
        """
        await asyncio.sleep(config.STOP_ROLL_WAIT_SEC)
        if self.generating:
            return
        self.roll_transcript()

    def _on_delta(self, delta: str) -> None:
        cuts = self.segmenter.feed(delta)
        for cut in cuts:
            self.sentences.put_nowait(cut)
        # Do not thin out right after a sentence becomes final; the
        # unfinished text would stay on the page.
        self._show_partial(force=bool(cuts))

    def _show_partial(self, force: bool = False) -> None:
        """Hand the unfinished transcript to the viewer page.

        **For the few seconds until a sentence is final, the page shows
        nothing.** The speaker's words only collect in `Segmenter`, and to
        the reader everything looks stopped. Sending what has arrived puts
        the text on the page almost at the same time as the voice (measured
        at 0.24 seconds).

        **Do not send this to translation or to the Zoom captions.** Neither
        one can replace a line it has already put out, so sending
        unfinished text would leave it there next to the corrected version.
        The browser is the only one that can replace a line.

        Deltas arrive with a median gap of 0.01 seconds, so passing every
        one of them would keep the long poll running without a break. We
        thin them out to one per `PARTIAL_INTERVAL_SEC`.
        **On a final sentence and on a stop, always pass it with `force`.**
        That keeps the thinning from leaving unfinished text on the page.
        """
        if self.web is None or not config.WEB_PARTIAL:
            return
        nowt = time.monotonic()
        if not force and nowt - self._partial_at < config.PARTIAL_INTERVAL_SEC:
            return
        self._partial_at = nowt
        self.web.partial(self.segmenter.buffer)

    # --- Speculative translation --------------------------------------------

    def _speculate(self) -> None:
        """After enough silence, send the translation without waiting for
        the sentence to be final.

        If the text has not changed by the time the sentence becomes final,
        we use that result as it is.
        **The safety margin of `IDLE_FLUSH_SEC` stays the same, and only the
        translation time disappears.**
        This is the only way left to make the silence wait faster; the
        threshold cannot go lower.
        """
        if not config.SPECULATE_AFTER_SEC:
            return
        if self.segmenter.silent_for() < config.SPECULATE_AFTER_SEC:
            return
        text = self.segmenter.buffer.strip()
        if not text or not segmenter_mod.has_content(text):
            return
        if self._spec is not None and self._spec[0] == text:
            return                      # do not send the same text twice
        self._drop_speculation()
        self.stats["spec_fired"] = self.stats.get("spec_fired", 0) + 1
        self._spec = (
            text,
            asyncio.create_task(self.translator.translate(text, remember=False)),
        )

    def _take_speculation(self, text: str):
        """Return the speculative translation task if it can be used.
        Otherwise throw it away and return None."""
        if self._spec is None:
            return None
        spec_text, task = self._spec
        # Allow a difference in the trailing sentence-ending mark alone.
        # Recognition sometimes adds the final period late, and the text is
        # the same, so translating it again would gain nothing.
        if _bare(spec_text) != _bare(text):
            self._drop_speculation()
            return None
        self._spec = None
        self.stats["spec_used"] = self.stats.get("spec_used", 0) + 1
        return task

    def _drop_speculation(self) -> None:
        """Throw away a speculative translation we have decided not to use.

        Cancelling does not stop the HTTP round trip that is already running
        (`asyncio.to_thread` cannot be interrupted part way). It only means
        we no longer read the result.
        """
        if self._spec is not None:
            self._spec[1].cancel()
            self._spec = None

    async def _watch_idle(self) -> None:
        """When the speaker stops, make the sentence final even with no
        sentence-ending mark."""
        while True:
            await asyncio.sleep(config.IDLE_POLL_SEC)
            self._speculate()
            for cut in self.segmenter.flush_if_idle():
                self.sentences.put_nowait(cut)
                # Clear the unfinished line here too, when a sentence became
                # final through silence.
                self._show_partial(force=True)

    async def _dispatch(self) -> None:
        """Take sentences and start translating them. We stack the tasks in
        order, so that the order is kept."""
        while True:
            cut = await self.sentences.get()
            # **This is what the silence watchdog measures from.** We look
            # at "did it become a sentence", not at the volume. The Zoom
            # mixed audio always carries background noise, so deciding
            # silence from the amplitude would never stop, even connected to
            # an empty room.
            self.last_sentence_at = time.monotonic()
            self.stats["sentences"] += 1
            self.stats[f"cut_{cut.reason}"] = self.stats.get(f"cut_{cut.reason}", 0) + 1
            print(f"[{now()}] transcript  {cut.text}")
            if self.web is not None:
                self.web.asr(cut.text)
            # If the speculation hit, use it. The whole translation time
            # disappears.
            task = self._take_speculation(cut.text)
            used_spec = task is not None
            if task is None:
                task = asyncio.create_task(self.translator.translate(cut.text))
            # Carry the original sentence and the time recognition finished
            # along with it. This pairs the recognized text with the caption
            # in the record, and keeps the time in the record from becoming
            # "the time the translation was done".
            # When the queue is full we wait. That is what limits how many
            # translations run at the same time.
            await self.inflight.put((cut, time.time(), task, used_spec))

    async def _post(self) -> None:
        """Wait for each translation in order, and send it as a caption."""
        while True:
            cut, heard_at, task, used_spec = await self.inflight.get()
            lines, took = await task
            # The measured time from the final sentence to the first
            # caption. We print it separately from the translation time
            # itself; the difference is the queue and the overhead.
            # **For a sentence where speculation hit, total is smaller than
            # took**, because the translation finished before the sentence
            # became final.
            total = time.time() - heard_at
            if used_spec and lines:
                # A speculative translation was not added to the context. We
                # add it here, now that we have decided to use it.
                self.translator.remember(cut.text)
            # **Hand every line to the viewer page first.** The 0.6 second
            # interval below works around a Zoom display limit (the caption
            # window holds only 4 lines at its smallest, and sending them
            # together pushes the first one out); the viewer page, with 8
            # lines, does not need it. These used to be done together, which
            # delayed the viewer page for Zoom's sake.
            if self.web is not None:
                for line in lines:
                    self.web.caption(line)
            # Print the measurements on the first line only. From the second
            # line on, we line up the columns with spaces of the same width.
            # Every character here is half width, so the count of characters
            # is the width.
            head = f"(total {total:.1f}s / trans {took:.1f}s{' spec' if used_spec else ''})"
            cont = " " * len(head)
            sent = 0
            for i, line in enumerate(lines):
                if i:
                    # Sending them all at once is too fast to read. The
                    # window holds only 4 lines at its smallest.
                    await asyncio.sleep(config.LINE_INTERVAL_SEC)
                # Always print to the screen, whether or not we sent it to
                # Zoom. The mark at the start of the line shows when we did
                # not send it.
                mark = head if not i else cont
                if await self.sender.send(line):
                    self.stats["lines"] += 1
                    sent += 1
                    print(f"[{now()}] caption     {mark} {line}")
                else:
                    print(f"[{now()}] caption     {mark} {line}   (not sent to Zoom)")
            # Record it even when translation failed and lines is empty. The
            # recognized text alone is worth keeping.
            if self.transcript is not None:
                self.transcript.add(
                    cut.text, lines, sent, when=heard_at,
                    cut=cut.reason, waited=cut.waited, took=took, total=total,
                    spec=used_spec, direction=config.DIRECTION,
                )

    # --- Startup -----------------------------------------------------------

    async def run(self, capture, start_now: bool = True) -> None:
        """With `start_now=False`, wait in the stopped state; you start it
        from the control page."""
        # Keep it, so the control page can switch the input.
        self.capture = capture
        print("=" * 70)
        print("LiveCaption")
        print("=" * 70)
        print(f"Direction:    {self.direction.label} "
              f"({config.MAX_CAPTION_CHARS} chars per line, forced cut at "
              f"{config.FORCE_CUT_CHARS} chars)")
        print(f"Glossary:     {', '.join(self.glossary_names) or '**none selected**'}"
              f"  {len(self.entries)} terms"
              f" ({len(self.keywords)} sent to transcription, "
              f"limit {config.ASR_KEYWORD_LIMIT})")
        if self.dropped_keywords:
            print(f"  **{self.dropped_keywords} terms were cut by the limit. "
                  "They never reach the transcription stage.**")
            print("  Raise config.ASR_KEYWORD_LIMIT.")
        print(f"Recognition:  {config.ASR_MODEL}  delay={self.settings.delay}"
              f"  languages={list(self.settings.languages)}")
        print(f"Translation:  {self.settings.translate_model}")
        print(f"Audio input:  {self.audio.status()['name']}")
        if self.settings.dry_run:
            print("**--dry-run: nothing is sent to Zoom.**")
        elif self.sender.base_url:
            print(f"Caption POST: {self.sender.base_url[:55]}...")
            print(f"              seq starts at {self.sender.seq}")
        else:
            print("Zoom output:  **stopped** (no token)")
            if self.web is not None:
                print("  Enter the token on the control page "
                      "(the gear at the top right) and start it there.")
        if self.web is not None:
            print(f"Control page: {self.web.control_url()}  "
                  "(for you only. Do not share it)")
            # **If it is being served, always show it.** The worst case is
            # a page being served when you did not think it was. There is no
            # authentication, so whoever can reach it can use it.
            for extra in self.web.control_urls_extra():
                print(f"              {extra}  "
                      "(from inside the tailnet. **Narrow it with an ACL**)")
            print(f"Viewer page:  {self.web.viewer_url()}"
                  f"  ({self.web.lines} lines. It stays inside)")
            state = self.web.tunnel.status()["state"] if self.web.tunnel else "off"
            if state == "on":
                print(f"Delivery URL: {self.web.public_url()}  "
                      "(hand this to the participants)")
            elif state == "starting":
                # The URL appears a few seconds later. When it does, run.py
                # prints it to the terminal.
                print("Delivery URL: starting (it appears here once it is ready)")
            else:
                print("Delivery URL: **stopped** (press Start delivering on "
                      "the control page to get a URL for the participants)")
        if self.transcript is not None:
            # We create the file here, so you learn before the meeting
            # starts whether the location can be written to.
            self.transcript.open()
            print(f"Record:       {self.transcript.path}")
            print("  Each sentence is written as it becomes final, "
                  "with the transcript and the caption as a pair.")
        else:
            print("Record:       **not kept** (--no-save)")
        if start_now:
            print("Captions:     starting now")
        else:
            print("Captions:     **stopped** (until you press Start on the "
                  "control page, nothing is recorded, transcribed or "
                  "translated)")
        if self.web is not None:
            print("Press Ctrl+C, or press Quit on the control page, to stop.")
        else:
            print("Press Ctrl+C to stop.")
        print("-" * 70)

        # Hand over the loop, so that a start from the browser can send the
        # throwaway captions.
        self.zoom.loop = asyncio.get_running_loop()

        # **Clean up on SIGTERM too.** Docker `stop` and `restart` send it.
        # Python's default is to die at once, **without passing through
        # `finally`.** We measured the following (2026-09-21):
        #
        #   The `.md` of the record is not written. The `.jsonl` has been
        #   appended to, so the content survives, but it never reaches a
        #   readable form. **Stopping in the middle of a meeting does
        #   exactly this.**
        #   The empty `.jsonl` of a run with no sentences is not deleted,
        #   and they pile up
        #   We die without leaving Zoom or stopping delivery
        #
        # The entry point is the same as "shut down" on the control page.
        # **Do not build a second path.**
        # Windows has no `add_signal_handler`. Ctrl+C works as before.
        try:
            self.zoom.loop.add_signal_handler(
                signal.SIGTERM, self.request_stop, "SIGTERM")
        except (NotImplementedError, RuntimeError, AttributeError, ValueError):
            pass
        if start_now:
            self.set_generating(True)

        tasks = [
            asyncio.create_task(self._pipeline(capture)),
            asyncio.create_task(self._watch_idle()),
            asyncio.create_task(self._dispatch()),
            asyncio.create_task(self._post()),
            # The scheduler that runs scheduled meetings. **We run it even
            # when there is no schedule at all.** That removes one branch in
            # the behavior, and it costs one sleeping coroutine.
            # **This must never return.** If it did, the wait below would
            # finish and the app would shut down.
            asyncio.create_task(self.scheduler.run_forever()),
            # When the browser asks us to shut down, this completes and the
            # wait below finishes.
            asyncio.create_task(self.stop_requested.wait()),
        ]
        # With --from-file, wait a little after the file has played, then
        # finish.
        finished = getattr(capture, "finished", None)
        if finished is not None:
            tasks.append(asyncio.create_task(self._wait_end(finished)))

        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass
        finally:
            self.asr.stop()
            # Do not leave the peephole open. It has no authentication, so
            # we close it before we finish.
            self.vnc.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_end(self, finished: asyncio.Event) -> None:
        await finished.wait()
        # Room for the last sentence to be recognized, translated and sent
        await asyncio.sleep(12)
        for cut in self.segmenter.flush():
            print(f"[{now()}] transcript  {cut.text}")
            heard_at = time.time()
            if self.web is not None:
                self.web.asr(cut.text)
            lines, took = await self.translator.translate(cut.text)
            sent = 0
            for line in lines:
                if await self.sender.send(line):
                    self.stats["lines"] += 1
                    sent += 1
                print(f"[{now()}] caption     {line}")
                if self.web is not None:
                    self.web.caption(line)
            if self.transcript is not None:
                self.transcript.add(
                    cut.text, lines, sent, when=heard_at,
                    cut=cut.reason, took=took, total=time.time() - heard_at,
                    direction=config.DIRECTION,
                )

    def report(self, capture=None) -> None:
        """The summary printed at the end. Written synchronously, so it can
        be called after Ctrl+C."""
        print("-" * 70)
        print(f"Final sentences: {self.stats['sentences']}")
        # A breakdown by the reason a sentence became final. **The share of
        # the silence wait is where you start when tuning the delay.**
        breakdown = [
            (label, self.stats.get(f"cut_{key}", 0))
            for key, label in (
                ("punct", "end mark"), ("force", "forced cut"),
                ("idle", "silence"), ("flush", "at exit"),
            )
        ]
        total_cuts = sum(n for _, n in breakdown) or 1
        print("  " + "  ".join(
            f"{label} {n} ({100 * n / total_cuts:.1f}%)" for label, n in breakdown if n
        ))
        fired = self.stats.get("spec_fired", 0)
        if fired:
            used = self.stats.get("spec_used", 0)
            # **What we threw away is money spent for nothing.** This is
            # where you see whether it pays off.
            print(f"Speculative translations: {fired} sent, {used} used "
                  f"({fired - used} thrown away)")
        print(f"Lines sent to Zoom: {self.sender.sent} "
              f"({self.sender.failed} failed)")
        print(f"Transcription reconnects: {self.asr.reconnects}")
        if capture is not None:
            print(f"Dropped audio blocks: {getattr(capture, 'dropped', 0)}")
        if self.web is not None:
            print(f"Browser caption viewers: {self.web.viewers}")
        if self.segmenter.buffer.strip():
            print(f"Text never sent: {self.segmenter.buffer.strip()[:80]}")
        if self.transcript is not None:
            # **This is where the `.md` is written for the first time.** The
            # `.jsonl` has been on disk all along.
            md = self.transcript.close()
            if md is not None:
                print(f"Record: {self.transcript.path}")
                print(f"        {md}")
            elif self.transcript.error:
                print(f"Record: could not be kept ({self.transcript.error})")
            else:
                print("Record: no sentence became final, so nothing was kept.")
