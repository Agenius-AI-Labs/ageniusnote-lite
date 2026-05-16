"""AgeniusNote Lite, click-to-dictate or global-hotkey dictate, copy/paste only.

No wake word, no LLM parsing, no database. Pure faster-whisper transcribe with
a clipboard handoff so it drops text into VSCode / Cursor / anywhere the OS
focus is. Reuses `voice_notes_v3.core.audio.Recorder` for capture.

Run:
    python apps/voice-notes-desktop/voice_notes_lite.py

Global hotkey (default Ctrl+Alt+Space) toggles record without stealing focus,
so the transcribed text auto-pastes into the previously focused window.
The in-window mic button is the manual mode: it records, transcribes,
copies, and leaves you to paste yourself.
"""

from __future__ import annotations

import io
import os
import sys
import threading
import time
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent

import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402
import soundfile as sf  # noqa: E402

from PySide6.QtCore import Qt, QObject, QThread, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

SAMPLE_RATE = 16000  # 16 kHz mono — ideal for Whisper


class Recorder:
    """Records from the default mic in a background thread."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = sample_rate
        self.frames: list[np.ndarray] = []
        self.stream = None
        self.recording = False

    def start(self) -> None:
        self.frames = []
        self.recording = True
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, indata, _frame_count, _time_info, _status):
        if self.recording:
            self.frames.append(indata.copy())

    def stop(self) -> bytes:
        """Stop recording and return WAV bytes."""
        self.recording = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if not self.frames:
            return b""
        audio = np.concatenate(self.frames, axis=0)
        buf = io.BytesIO()
        sf.write(buf, audio, self.sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()

DEFAULT_MODEL = os.environ.get("VN_LITE_MODEL", "base.en")
DEFAULT_HOTKEY = os.environ.get("VN_LITE_HOTKEY", "<ctrl>+<alt>+m")
APP_VERSION = "1.0.0"


def _resource_path(relative: str) -> Path:
    """Resolve an asset path that works both in dev and when frozen by PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", _APP_DIR))
    return base / relative


# ---------- DB-free faster-whisper wrapper ----------

_WHISPER_CACHE: dict = {"model": None, "name": "", "device": "", "compute": ""}


def _build_model(model_name: str):
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(model_name, device="cuda", compute_type="float16"), "cuda", "float16"
    except Exception:
        return WhisperModel(model_name, device="cpu", compute_type="int8"), "cpu", "int8"


def _get_model(model_name: str):
    if _WHISPER_CACHE["model"] is None or _WHISPER_CACHE["name"] != model_name:
        model, device, compute = _build_model(model_name)
        _WHISPER_CACHE.update(model=model, name=model_name, device=device, compute=compute)
    return _WHISPER_CACHE["model"]


def transcribe(wav_bytes: bytes, model_name: str = DEFAULT_MODEL) -> tuple[str, dict]:
    import tempfile
    started = time.perf_counter()
    model = _get_model(model_name)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(wav_bytes)
        tmp_path = tmp.name
    try:
        try:
            segments, _info = model.transcribe(
                tmp_path,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
            )
        except TypeError:
            segments, _info = model.transcribe(tmp_path)
        text = " ".join(s.text.strip() for s in segments).strip()
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
    meta = {
        "device": _WHISPER_CACHE["device"],
        "compute": _WHISPER_CACHE["compute"],
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "model": model_name,
    }
    return text, meta


# ---------- Background transcription thread ----------

class TranscribeWorker(QThread):
    finished_text = Signal(str, dict)
    failed = Signal(str)

    def __init__(self, wav: bytes, paste_after: bool, model_name: str, target_handle: object | None):
        super().__init__()
        self.wav = wav
        self.paste_after = paste_after
        self.model_name = model_name
        self.target_handle = target_handle

    def run(self) -> None:
        try:
            text, meta = transcribe(self.wav, self.model_name)
            meta["paste_after"] = self.paste_after
            meta["target_handle"] = self.target_handle
            self.finished_text.emit(text, meta)
        except Exception as exc:
            self.failed.emit(str(exc))


# ---------- Global hotkey bridge ----------

class HotkeyBridge(QObject):
    triggered = Signal()

    def __init__(self, combo: str):
        super().__init__()
        self.combo = combo
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False
        try:
            self._listener = keyboard.GlobalHotKeys({self.combo: self._fire})
            self._listener.start()
            return True
        except Exception:
            return False

    def _fire(self) -> None:
        self.triggered.emit()

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


# ---------- Cross-platform foreground capture + paste ----------
#
# The "handle" is opaque and platform-defined:
#   Windows : int HWND
#   macOS   : str  bundle identifier of the frontmost app
#   Linux   : None (unsupported for now)


def _capture_foreground() -> object | None:
    """Snapshot the OS-foreground app so we can restore it before paste."""
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = int(ctypes.windll.user32.GetForegroundWindow())
            return hwnd or None
        except Exception:
            return None
    if sys.platform == "darwin":
        return _macos_frontmost_bundle()
    return None


def _restore_foreground(handle: object | None) -> None:
    if not handle:
        return
    if sys.platform == "win32":
        _restore_foreground_win32(int(handle))
    elif sys.platform == "darwin" and isinstance(handle, str):
        _macos_activate_bundle(handle)


def _restore_foreground_win32(hwnd: int) -> None:
    """SetForegroundWindow with AttachThreadInput to bypass Windows focus
    stealing prevention."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        cur_fg = user32.GetForegroundWindow()
        if cur_fg == hwnd:
            return

        fg_tid = user32.GetWindowThreadProcessId(cur_fg, None)
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        cur_tid = kernel32.GetCurrentThreadId()

        attached_fg = False
        attached_target = False
        if fg_tid and fg_tid != cur_tid:
            attached_fg = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if target_tid and target_tid != cur_tid and target_tid != fg_tid:
            attached_target = bool(user32.AttachThreadInput(cur_tid, target_tid, True))

        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)

        if attached_fg:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
        if attached_target:
            user32.AttachThreadInput(cur_tid, target_tid, False)
    except Exception:
        pass


def _macos_frontmost_bundle() -> str | None:
    import subprocess
    script = (
        'tell application "System Events" to get bundle identifier of '
        'first process whose frontmost is true'
    )
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=2,
        )
        bundle = (r.stdout or "").strip()
        return bundle or None
    except Exception:
        return None


def _macos_activate_bundle(bundle_id: str) -> None:
    import subprocess
    script = f'tell application id "{bundle_id}" to activate'
    try:
        subprocess.run(["osascript", "-e", script], timeout=2)
    except Exception:
        pass


def _send_paste_async(handle: object | None, delay_ms: int = 120) -> None:
    """Restore the captured foreground app, then simulate the platform's paste
    shortcut. Runs in a background thread so the Qt event loop can finish
    updating the clipboard before the keypress goes out."""
    def _go():
        try:
            from pynput.keyboard import Controller, Key
        except Exception:
            return
        time.sleep(delay_ms / 1000.0)
        _restore_foreground(handle)
        # Settle delay so the target window is truly focused before paste.
        time.sleep(0.06)
        kb = Controller()
        modifier = Key.cmd if sys.platform == "darwin" else Key.ctrl
        with kb.pressed(modifier):
            kb.press('v')
            kb.release('v')
    threading.Thread(target=_go, daemon=True).start()


# ---------- UI ----------

QSS = """
* { outline: 0; }

QWidget#root {
    background-color: #05060a;
    color: #d4e3f5;
    font-family: 'Inter', 'Segoe UI', sans-serif;
}
QWidget#header {
    background-color: #0a0e18;
    border-bottom: 1px solid rgba(56, 189, 248, 0.18);
}
QLabel#title {
    font-size: 14px;
    font-weight: 600;
    color: #d4e3f5;
}
QLabel#titleAccent {
    font-size: 14px;
    font-weight: 600;
    color: #38bdf8;
}
QLabel#version {
    font-size: 10px;
    color: #4a6a88;
}
QLabel#status, QLabel#statusRec {
    font-size: 11px;
    color: #7ea8cc;
    padding: 0 2px;
}
QLabel#statusRec {
    color: #f87171;
    font-weight: 600;
}
QTextEdit {
    background-color: #0a0e18;
    color: #d4e3f5;
    border: 1px solid rgba(56, 189, 248, 0.18);
    border-radius: 6px;
    padding: 10px;
    font-family: 'Inter', 'Segoe UI', sans-serif;
    font-size: 13px;
    selection-background-color: rgba(56, 189, 248, 0.30);
    selection-color: #ffffff;
}
QTextEdit:focus {
    border: 1px solid rgba(56, 189, 248, 0.45);
}

QPushButton {
    background-color: transparent;
    color: #a8c0d8;
    border: 1px solid rgba(56, 189, 248, 0.22);
    border-radius: 6px;
    padding: 7px 14px;
    font-size: 12px;
    font-weight: 500;
}
QPushButton:hover {
    color: #d4e3f5;
    border-color: rgba(56, 189, 248, 0.50);
    background-color: rgba(56, 189, 248, 0.05);
}
QPushButton:pressed {
    background-color: rgba(56, 189, 248, 0.10);
    color: #38bdf8;
}
QPushButton:focus {
    border-color: rgba(56, 189, 248, 0.50);
}
QPushButton:checked {
    background-color: rgba(56, 189, 248, 0.08);
    color: #38bdf8;
    border-color: rgba(56, 189, 248, 0.40);
}
QPushButton:disabled {
    color: #3a4a5e;
    border-color: rgba(255, 255, 255, 0.04);
}

QPushButton#record {
    background-color: rgba(56, 189, 248, 0.10);
    color: #38bdf8;
    border: 1px solid rgba(56, 189, 248, 0.45);
    font-weight: 600;
    min-width: 88px;
}
QPushButton#record:hover {
    background-color: rgba(56, 189, 248, 0.18);
    color: #7dd3fc;
    border-color: rgba(56, 189, 248, 0.70);
}
QPushButton#record:pressed {
    background-color: rgba(56, 189, 248, 0.22);
    color: #38bdf8;
}
QPushButton#record:focus {
    border-color: rgba(56, 189, 248, 0.70);
}
QPushButton#record[recording="true"] {
    background-color: rgba(248, 113, 113, 0.12);
    color: #f87171;
    border: 1px solid rgba(248, 113, 113, 0.55);
}
QPushButton#record[recording="true"]:hover {
    background-color: rgba(248, 113, 113, 0.20);
    color: #fca5a5;
    border-color: rgba(248, 113, 113, 0.80);
}
QPushButton#record[recording="true"]:pressed {
    background-color: rgba(248, 113, 113, 0.26);
}
QPushButton#record[recording="true"]:focus {
    border-color: rgba(248, 113, 113, 0.80);
}

QPushButton#pasteToggle {
    color: #4a6a88;
    border-color: rgba(255, 255, 255, 0.08);
}
QPushButton#pasteToggle:hover {
    color: #7ea8cc;
    border-color: rgba(255, 255, 255, 0.18);
    background-color: rgba(255, 255, 255, 0.02);
}
QPushButton#pasteToggle:checked,
QPushButton#pasteToggle[on="true"] {
    color: #34d399;
    border-color: rgba(52, 211, 153, 0.45);
    background-color: rgba(52, 211, 153, 0.06);
}
QPushButton#pasteToggle:checked:hover,
QPushButton#pasteToggle[on="true"]:hover {
    background-color: rgba(52, 211, 153, 0.12);
    border-color: rgba(52, 211, 153, 0.70);
}
"""


class LiteWindow(QWidget):
    def __init__(self, hotkey_combo: str, model_name: str):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("AgeniusNote Lite")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(460, 320)

        icon_path = _resource_path("assets/icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.model_name = model_name
        self.hotkey_combo = hotkey_combo
        self.recorder: Recorder | None = None
        self.recording = False
        self.worker: TranscribeWorker | None = None
        self.auto_paste = True  # auto-paste only happens on hotkey triggers
        self._target_handle: object | None = None  # captured at recording start (HWND on Windows, bundle id on macOS)

        # Don't auto-activate when the window is shown / restacked. The flag
        # keeps the window from stealing focus from VSCode/Cursor when it
        # repaints after a transcription finishes.
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # Header bar — "Agenius" in cyan, "Note Lite" in light
        title_agenius = QLabel("Agenius")
        title_agenius.setObjectName("titleAccent")
        title_rest = QLabel("Note Lite")
        title_rest.setObjectName("title")
        version_lbl = QLabel(f"v{APP_VERSION}")
        version_lbl.setObjectName("version")
        header_row = QHBoxLayout()
        header_row.setContentsMargins(14, 10, 14, 10)
        header_row.setSpacing(2)
        header_row.addWidget(title_agenius)
        header_row.addWidget(title_rest)
        header_row.addStretch(1)
        header_row.addWidget(version_lbl)
        header = QWidget()
        header.setObjectName("header")
        header.setLayout(header_row)

        self.status = QLabel(self._status_idle())
        self.status.setObjectName("status")

        # Transcript
        self.transcript = QTextEdit()
        self.transcript.setPlaceholderText(
            f"Press {self._human_combo()} anywhere to dictate into the focused window.\n"
            "Or click Record to dictate into this box."
        )

        # Buttons
        self.btn_record = QPushButton("Record")
        self.btn_record.setObjectName("record")
        self.btn_record.setProperty("recording", False)
        self.btn_record.clicked.connect(self._toggle_manual)

        self.btn_copy = QPushButton("Copy")
        self.btn_copy.clicked.connect(self._copy)

        self.btn_clear = QPushButton("Clear")
        self.btn_clear.clicked.connect(lambda: self.transcript.clear())

        self.btn_paste_toggle = QPushButton("Auto-paste  on")
        self.btn_paste_toggle.setObjectName("pasteToggle")
        self.btn_paste_toggle.setCheckable(True)
        self.btn_paste_toggle.setChecked(True)
        self.btn_paste_toggle.clicked.connect(self._toggle_paste)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_record)
        btn_row.addWidget(self.btn_copy)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_paste_toggle)

        # Layout — header strip stretches edge-to-edge, body has padding
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(header)
        body = QVBoxLayout()
        body.setContentsMargins(12, 10, 12, 12)
        body.setSpacing(8)
        body.addWidget(self.status)
        body.addWidget(self.transcript, 1)
        body.addLayout(btn_row)
        body_widget = QWidget()
        body_widget.setLayout(body)
        root.addWidget(body_widget, 1)

        self.setStyleSheet(QSS)

        # In-window shortcut: Ctrl+Shift+R to toggle manual record while focused
        QShortcut(QKeySequence("Ctrl+Shift+R"), self, self._toggle_manual)

        # Global hotkey
        self.hotkey = HotkeyBridge(hotkey_combo)
        self.hotkey.triggered.connect(self._toggle_hotkey)
        self._hotkey_ok = self.hotkey.start()
        if not self._hotkey_ok:
            self.status.setText(
                f"Global hotkey unavailable (pynput missing?). Click Record. Model: {model_name}"
            )

        # Tracks whether the current recording was started via the global hotkey.
        # Only hotkey-driven sessions auto-paste into the focused window.
        self._session_via_hotkey = False

    # ---- helpers ----

    def _human_combo(self) -> str:
        return (
            self.hotkey_combo
            .replace("<ctrl>", "Ctrl")
            .replace("<alt>", "Alt")
            .replace("<shift>", "Shift")
            .replace("<cmd>", "Cmd")
            .replace("<space>", "Space")
        )

    def _status_idle(self) -> str:
        dev = ""
        if _WHISPER_CACHE["device"]:
            dev = f"  ·  {_WHISPER_CACHE['device']}/{_WHISPER_CACHE['compute']}"
        return f"Ready  ·  {self.model_name}{dev}  ·  hotkey {self._human_combo()}"

    def _set_recording_ui(self, recording: bool) -> None:
        self.recording = recording
        self.btn_record.setText("Stop" if recording else "Record")
        self.btn_record.setProperty("recording", recording)
        self.btn_record.style().unpolish(self.btn_record)
        self.btn_record.style().polish(self.btn_record)
        if recording:
            self.status.setObjectName("statusRec")
            self.status.setText("● Recording…")
        else:
            self.status.setObjectName("status")
            self.status.setText(self._status_idle())
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    # ---- record toggles ----

    def _toggle_manual(self) -> None:
        self._toggle(via_hotkey=False)

    def _toggle_hotkey(self) -> None:
        self._toggle(via_hotkey=True)

    def _toggle(self, via_hotkey: bool) -> None:
        if self.recording:
            self._stop_and_transcribe()
        else:
            self._session_via_hotkey = via_hotkey
            # Snapshot whatever window/app is in the OS foreground RIGHT NOW —
            # this is what we'll restore before paste.
            if via_hotkey:
                handle = _capture_foreground()
                # Defensive: if our own window is somehow foreground, skip it.
                if sys.platform == "win32" and isinstance(handle, int):
                    try:
                        own_hwnd = int(self.winId())
                    except Exception:
                        own_hwnd = 0
                    if handle == own_hwnd:
                        handle = None
                self._target_handle = handle
            else:
                self._target_handle = None
            self._start()

    def _start(self) -> None:
        try:
            self.recorder = Recorder()
            self.recorder.start()
            self._set_recording_ui(True)
        except Exception as exc:
            self.status.setText(f"Mic error: {exc}")

    def _stop_and_transcribe(self) -> None:
        if not self.recorder:
            return
        try:
            wav = self.recorder.stop()
        except Exception as exc:
            self.status.setText(f"Stop error: {exc}")
            self._set_recording_ui(False)
            return
        self.recorder = None
        self._set_recording_ui(False)
        if not wav:
            self.status.setText("No audio captured.")
            return
        self.status.setText("Transcribing…")
        paste_after = self.auto_paste and self._session_via_hotkey
        self.worker = TranscribeWorker(wav, paste_after, self.model_name, self._target_handle)
        self.worker.finished_text.connect(self._on_transcribed)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_transcribed(self, text: str, meta: dict) -> None:
        if not text:
            self.status.setText(f"Empty transcript ({meta['elapsed_ms']} ms)")
            return
        # Hotkey + auto-paste sessions: text is going to the focused window,
        # so don't write it into the in-window box at all (clears any stale
        # text from a previous manual session). Manual-button sessions
        # append to the box (notepad mode).
        if meta.get("paste_after"):
            self.transcript.clear()
        else:
            existing = self.transcript.toPlainText()
            if existing:
                self.transcript.setPlainText(existing.rstrip() + "\n\n" + text)
            else:
                self.transcript.setPlainText(text)
        # Always set clipboard.
        QGuiApplication.clipboard().setText(text)
        dev = f"{meta['device']}/{meta['compute']}" if meta.get("device") else "?"
        elapsed = meta["elapsed_ms"]
        if meta.get("paste_after"):
            _send_paste_async(meta.get("target_handle"))
            self.status.setText(f"Pasted ({elapsed} ms, {dev})")
        else:
            self.status.setText(f"Copied ({elapsed} ms, {dev})")

    def _on_failed(self, err: str) -> None:
        self.status.setText(f"Transcribe failed: {err}")

    # ---- button actions ----

    def _copy(self) -> None:
        text = self.transcript.toPlainText().strip()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        self.status.setText("Copied.")

    def _toggle_paste(self) -> None:
        self.auto_paste = self.btn_paste_toggle.isChecked()
        self.btn_paste_toggle.setText(
            f"Auto-paste  {'on' if self.auto_paste else 'off'}"
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        self.hotkey.stop()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("AgeniusNote Lite")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Agenius AI Labs")
    icon_path = _resource_path("assets/icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    win = LiteWindow(DEFAULT_HOTKEY, DEFAULT_MODEL)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
