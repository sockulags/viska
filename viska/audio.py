"""Mikrofoninspelning: 16 kHz mono float32, formatet Whisper vill ha.

Strömmen hålls normalt öppen hela tiden och skriver till en liten
ringbuffert i RAM. När en inspelning startar tas ~0,25 s ur ringbufferten
med som förstart — att öppna en WASAPI-ström tar 100–300 ms och skulle
annars klippa första stavelsen. Ljudet lämnar aldrig processen.

PortAudio fryser enhetslistan vid init och följer inte Windows byte av
standardmikrofon. En vakthund upptäcker att callbacken tystnat (enheten
försvann) och bygger då om PortAudio; traymenyn kan också trigga det
manuellt via restart().
"""

import logging
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BLOCK = 1600  # 100 ms per callback-block


class Recorder:
    def __init__(self, input_device=None, keep_open=True, preroll_s=0.25):
        self._device = input_device
        self._keep_open = keep_open
        self._preroll_blocks = max(0, int(preroll_s * SAMPLE_RATE / BLOCK))
        self._ring = deque(maxlen=self._preroll_blocks + 2)
        self._chunks = None  # None = spelar inte in
        # _lock skyddar buffertarna (delas med ljudcallbacken och får bara
        # hållas ögonblick); _stream_lock serialiserar öppna/stänga/byta
        # ström mellan hotkey-tråden och trayns omstart. Håll aldrig _lock
        # under strömoperationer: stream.stop() väntar in callbacken, som
        # själv tar _lock.
        self._lock = threading.Lock()
        self._stream_lock = threading.Lock()
        self._stream = None
        self._last_frame_t = 0.0
        if keep_open:
            try:
                with self._stream_lock:
                    self._open_stream()
            except Exception:
                log.exception("Kunde inte öppna mikrofonen vid start")

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._chunks is not None

    def start(self) -> None:
        with self._stream_lock:
            self._ensure_stream()
            with self._lock:
                preroll = []
                if self._keep_open and self._preroll_blocks:
                    preroll = list(self._ring)[-self._preroll_blocks:]
                self._chunks = preroll

    def stop(self) -> np.ndarray:
        with self._stream_lock:
            with self._lock:
                chunks, self._chunks = self._chunks, None
            if not self._keep_open:
                self._close_stream()
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)[:, 0]

    def restart(self) -> None:
        """Byt till den mikrofon som nu är systemets standard."""
        with self._stream_lock:
            with self._lock:
                self._chunks = None
            self._close_stream()
            self._reset_portaudio()
            if self._keep_open:
                self._open_stream()

    # -- Internt --------------------------------------------------------------

    def _callback(self, indata, frames, time_info, status) -> None:
        self._last_frame_t = time.monotonic()
        with self._lock:
            if self._chunks is not None:
                self._chunks.append(indata.copy())
            else:
                self._ring.append(indata.copy())

    def _ensure_stream(self) -> None:
        if self._stream is not None:
            if time.monotonic() - self._last_frame_t < 1.0:
                return
            log.warning("Ljudströmmen har tystnat (enhet borta?) – bygger om")
            self._close_stream()
            self._reset_portaudio()
        self._open_stream()

    def _open_stream(self) -> None:
        kwargs = dict(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK,
            device=self._device,
            callback=self._callback,
        )
        try:
            stream = sd.InputStream(**kwargs)
        except sd.PortAudioError:
            # WASAPI shared mode kan neka 16 kHz utan auto-konvertering.
            stream = sd.InputStream(
                **kwargs, extra_settings=sd.WasapiSettings(auto_convert=True)
            )
        stream.start()
        self._stream = stream
        self._last_frame_t = time.monotonic()

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _reset_portaudio() -> None:
        # Privata API:er, men etablerat mönster för att få PortAudio att se
        # nya enheter (python-sounddevice #47/#382). Kräver att alla strömmar
        # är stängda först.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            log.exception("Kunde inte starta om PortAudio")
