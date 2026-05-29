"""AgeniusNote Lite, click-to-dictate or global-hotkey dictate, copy/paste only.

No wake word, no LLM parsing, no database. Pure faster-whisper transcribe with
a clipboard handoff so it drops text into VSCode / Cursor / anywhere the OS
focus is.

Run:
    python apps/voice-notes-desktop/voice_notes_lite.py

Global hotkey (default Ctrl+Alt+M) toggles record without stealing focus,
so the transcribed text auto-pastes into the previously focused window.
The in-window mic button is the manual mode: it records, transcribes,
copies, and leaves you to paste yourself.
"""

from __future__ import annotations

import io
import json
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent

# PyInstaller windowed (--noconsole) builds run with no console, so
# sys.stdout/sys.stderr are None. The first-run Whisper model download goes
# through huggingface_hub + tqdm, which write progress to sys.stderr and crash
# with "'NoneType' object has no attribute 'write'" -- so the model never
# downloads. Point the missing streams at the null device (discards writes).
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")  # noqa: SIM115


def _user_models_dir() -> Path:
    """Per-user, writable directory for cached Whisper model weights.

    Kept outside the .app bundle so signed/notarized installs don't need
    write access to /Applications and re-runs survive app updates.
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "AgeniusNote Lite"
    elif sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        base = Path(root) / "AgeniusNote Lite"
    else:
        base = Path.home() / ".local" / "share" / "agenius-note-lite"
    return base / "models"


def _settings_path() -> Path:
    """User-writable settings file, alongside the model cache dir."""
    return _user_models_dir().parent / "settings.json"


def load_settings() -> dict:
    """Load persisted UI settings. Returns {} on any problem so a corrupt or
    missing file never blocks launch."""
    try:
        path = _settings_path()
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_settings(data: dict) -> None:
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


import numpy as np  # noqa: E402
import sounddevice as sd  # noqa: E402
import soundfile as sf  # noqa: E402

from PySide6.QtCore import Qt, QObject, QThread, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication, QIcon, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
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
DEFAULT_DEVICE = (os.environ.get("VN_LITE_DEVICE", "cpu") or "cpu").strip().lower()
if DEFAULT_DEVICE not in {"cpu", "cuda", "auto"}:
    DEFAULT_DEVICE = "cpu"

# Optional mouse-button hotkey. Off by default so we never hijack normal
# clicks. Set VN_LITE_MOUSE_BUTTON to one of: x1 / back, x2 / forward, middle.
# VN_LITE_MOUSE_MODE picks toggle (click to start, click to stop, matching the
# keyboard hotkey) or hold (push-to-talk: press to record, release to stop).
DEFAULT_MOUSE_BUTTON = (os.environ.get("VN_LITE_MOUSE_BUTTON", "") or "").strip().lower()
DEFAULT_MOUSE_MODE = (os.environ.get("VN_LITE_MOUSE_MODE", "toggle") or "toggle").strip().lower()
if DEFAULT_MOUSE_MODE not in {"toggle", "hold"}:
    DEFAULT_MOUSE_MODE = "toggle"
# Suppress the bound mouse button's normal action (so x1/x2 stop also firing
# browser back/forward). Default on, since binding a button means repurposing it.
DEFAULT_MOUSE_SUPPRESS = (
    os.environ.get("VN_LITE_MOUSE_SUPPRESS", "1") or "1"
).strip().lower() not in {"0", "false", "no", "off"}


def _read_app_version() -> str:
    """Read app version from packaging/VERSION.

    Works in both dev (file lives at repo_root/packaging/VERSION) and frozen
    PyInstaller mode (file is bundled into the app via the spec's `datas`).
    Returns "0.0.0" if the file cannot be located, so a missing-file bug never
    crashes the UI.
    """
    candidates = [
        # Frozen mode: PyInstaller unpacks data files relative to sys._MEIPASS.
        Path(getattr(sys, "_MEIPASS", _APP_DIR)) / "packaging" / "VERSION",
        # Dev mode: source tree has packaging/ next to this file.
        _APP_DIR / "packaging" / "VERSION",
    ]
    for path in candidates:
        try:
            if path.is_file():
                value = path.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            continue
    return "0.0.0"


APP_VERSION = _read_app_version()


def _resource_path(relative: str) -> Path:
    """Resolve an asset path that works both in dev and when frozen by PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", _APP_DIR))
    return base / relative


# ---------- DB-free faster-whisper wrapper ----------

_WHISPER_CACHE: dict = {"model": None, "name": "", "pref": "", "device": "", "compute": ""}


_HF_REPO_FOR_SIZE = {
    # faster-whisper ships pre-converted CT2 weights under Systran/* on HF Hub.
    "tiny":     "Systran/faster-whisper-tiny",
    "tiny.en":  "Systran/faster-whisper-tiny.en",
    "base":     "Systran/faster-whisper-base",
    "base.en":  "Systran/faster-whisper-base.en",
    "small":    "Systran/faster-whisper-small",
    "small.en": "Systran/faster-whisper-small.en",
    "medium":   "Systran/faster-whisper-medium",
    "medium.en":"Systran/faster-whisper-medium.en",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
}


def _resolve_model_path(model_name: str) -> str:
    """Download (if needed) and return an absolute path to a faster-whisper CT2
    model directory.

    Why this exists: passing a HF repo id directly to WhisperModel triggers
    download from inside libctranslate2 native code. When that download fails
    (no internet, partial cache, sandbox permissions), libctranslate2's spdlog
    error path segfaults instead of raising a clean Python exception. Doing
    the download here means any failure surfaces as a normal Python error we
    can show in the UI.
    """
    if os.path.isdir(model_name):
        return model_name

    repo_id = _HF_REPO_FOR_SIZE.get(model_name, model_name)
    cache_root = _user_models_dir()
    cache_root.mkdir(parents=True, exist_ok=True)

    from huggingface_hub import snapshot_download

    local_dir = snapshot_download(
        repo_id=repo_id,
        cache_dir=str(cache_root),
        local_files_only=False,
        # Only what ctranslate2 actually needs for inference.
        allow_patterns=[
            "config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.txt",
            "preprocessor_config.json",
        ],
    )

    # Sanity check before handing to ctranslate2 — its native loader will
    # crash inside spdlog if model.bin is missing or zero-length.
    bin_path = Path(local_dir) / "model.bin"
    if not bin_path.exists() or bin_path.stat().st_size < 1024:
        raise RuntimeError(
            f"Model {model_name} downloaded but model.bin is missing or truncated "
            f"at {bin_path}. Delete {cache_root} and try again."
        )
    return local_dir


def _build_model(model_name: str, device_pref: str):
    from faster_whisper import WhisperModel
    pref = (device_pref or "cpu").lower()
    resolved = _resolve_model_path(model_name)
    if pref == "cuda":
        try:
            return WhisperModel(resolved, device="cuda", compute_type="float16"), "cuda", "float16"
        except Exception:
            return WhisperModel(resolved, device="cpu", compute_type="int8"), "cpu", "int8"
    if pref == "auto":
        try:
            return WhisperModel(resolved, device="cuda", compute_type="float16"), "cuda", "float16"
        except Exception:
            return WhisperModel(resolved, device="cpu", compute_type="int8"), "cpu", "int8"
    return WhisperModel(resolved, device="cpu", compute_type="int8"), "cpu", "int8"


def _get_model(model_name: str, device_pref: str):
    pref = (device_pref or "cpu").lower()
    if pref not in {"cpu", "cuda", "auto"}:
        pref = "cpu"
    if (
        _WHISPER_CACHE["model"] is None
        or _WHISPER_CACHE["name"] != model_name
        or _WHISPER_CACHE["pref"] != pref
    ):
        model, device, compute = _build_model(model_name, pref)
        _WHISPER_CACHE.update(
            model=model,
            name=model_name,
            pref=pref,
            device=device,
            compute=compute,
        )
    return _WHISPER_CACHE["model"]


def _run_transcribe(model, wav_path: str) -> str:
    try:
        segments, _info = model.transcribe(
            wav_path,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
    except TypeError:
        segments, _info = model.transcribe(wav_path)
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe(
    wav_bytes: bytes,
    model_name: str = DEFAULT_MODEL,
    device_pref: str = DEFAULT_DEVICE,
) -> tuple[str, dict]:
    import tempfile
    started = time.perf_counter()
    model = _get_model(model_name, device_pref)
    fallback_reason: str | None = None
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(wav_bytes)
        tmp_path = tmp.name
    try:
        try:
            text = _run_transcribe(model, tmp_path)
        except Exception as exc:
            if _WHISPER_CACHE["device"] != "cuda":
                raise
            # CUDA DLL loading can fail at first transcribe() even when model
            # construction appeared to succeed. Retry once on CPU.
            fallback_reason = f"{type(exc).__name__}: {exc}"
            model = _get_model(model_name, "cpu")
            text = _run_transcribe(model, tmp_path)
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
        "device_pref": device_pref,
        "fallback_reason": fallback_reason,
    }
    return text, meta


# ---------- Background transcription thread ----------

class TranscribeWorker(QThread):
    finished_text = Signal(str, dict)
    failed = Signal(str)

    def __init__(
        self,
        wav: bytes,
        paste_after: bool,
        model_name: str,
        device_pref: str,
        target_handle: object | None,
    ):
        super().__init__()
        self.wav = wav
        self.paste_after = paste_after
        self.model_name = model_name
        self.device_pref = device_pref
        self.target_handle = target_handle

    def run(self) -> None:
        try:
            text, meta = transcribe(self.wav, self.model_name, self.device_pref)
            meta["paste_after"] = self.paste_after
            meta["target_handle"] = self.target_handle
            self.finished_text.emit(text, meta)
        except Exception as exc:
            self.failed.emit(str(exc))


# ---------- Global hotkey bridge ----------

#
# macOS: uses Carbon RegisterEventHotKey via ctypes - no Accessibility
#        permission needed, survives ad-hoc signing, dispatched via the
#        application's CFRunLoop which Qt already owns.
# Other: uses pynput's keyboard.GlobalHotKeys.
#
# Why not pynput on macOS:
#   1) Quartz event taps require Accessibility permission. macOS keys that
#      permission to the code signature, and ad-hoc-signed PyInstaller
#      bundles produce a new signature every build, so the grant doesn't
#      stick reliably.
#   2) pynput's macOS listener calls TSMGetInputSourceProperty from a
#      background thread; macOS 26 asserts that must be the main dispatch
#      queue and SIGTRAPs the process. So pynput is broken on macOS 26
#      regardless of permissions.
#

_CARBON_CMD_KEY = 1 << 8
_CARBON_SHIFT_KEY = 1 << 9
_CARBON_OPTION_KEY = 1 << 11
_CARBON_CONTROL_KEY = 1 << 12

_CARBON_MODIFIER_MAP = {
    "cmd": _CARBON_CMD_KEY,
    "command": _CARBON_CMD_KEY,
    "cmd_l": _CARBON_CMD_KEY,
    "cmd_r": _CARBON_CMD_KEY,
    "shift": _CARBON_SHIFT_KEY,
    "shift_l": _CARBON_SHIFT_KEY,
    "shift_r": _CARBON_SHIFT_KEY,
    "alt": _CARBON_OPTION_KEY,
    "option": _CARBON_OPTION_KEY,
    "opt": _CARBON_OPTION_KEY,
    "alt_l": _CARBON_OPTION_KEY,
    "alt_r": _CARBON_OPTION_KEY,
    "alt_gr": _CARBON_OPTION_KEY,
    "ctrl": _CARBON_CONTROL_KEY,
    "control": _CARBON_CONTROL_KEY,
    "ctrl_l": _CARBON_CONTROL_KEY,
    "ctrl_r": _CARBON_CONTROL_KEY,
}

# Mac virtual keycodes (HIToolbox/Events.h, "kVK_ANSI_*").
_CARBON_VK = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05,
    "z": 0x06, "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C,
    "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "1": 0x12,
    "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "9": 0x19,
    "7": 0x1A, "8": 0x1C, "0": 0x1D, "o": 0x1F, "u": 0x20, "i": 0x22,
    "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D, "m": 0x2E,
    "space": 0x31, "return": 0x24, "tab": 0x30, "escape": 0x35,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60,
    "f6": 0x61, "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D,
    "f11": 0x67, "f12": 0x6F,
}

_EVENT_CLASS_KEYBOARD = 0x6B657962  # 'keyb' FourCharCode
_EVENT_HOTKEY_PRESSED = 5


def _parse_carbon_combo(combo: str):
    tokens = [t.strip("<>").lower() for t in combo.replace(" ", "").split("+") if t]
    modifiers = 0
    key_token = None
    for tok in tokens:
        if tok in _CARBON_MODIFIER_MAP:
            modifiers |= _CARBON_MODIFIER_MAP[tok]
        else:
            key_token = tok
    if not key_token or key_token not in _CARBON_VK:
        return None
    return modifiers, _CARBON_VK[key_token]


class _CarbonHotkey:
    """Inline Carbon RegisterEventHotKey wrapper. See HotkeyBridge."""

    def __init__(self, combo, callback):
        self.combo = combo
        self.callback = callback
        self._carbon = None
        self._handler_ref = None
        self._hotkey_ref = None
        self._handler_proc = None  # keep CFUNCTYPE wrapper alive

    def register(self):
        import ctypes
        import ctypes.util
        from ctypes import CFUNCTYPE, POINTER, Structure, c_int32, c_uint32, c_void_p

        parsed = _parse_carbon_combo(self.combo)
        if not parsed:
            print(f"[hotkey] combo not parseable: {self.combo!r}", flush=True)
            return False
        modifiers, vk = parsed

        carbon_path = ctypes.util.find_library("Carbon") or (
            "/System/Library/Frameworks/Carbon.framework/Carbon"
        )
        try:
            carbon = ctypes.cdll.LoadLibrary(carbon_path)
        except OSError as exc:
            print(f"[hotkey] failed to load Carbon: {exc}", flush=True)
            return False

        class _EventTypeSpec(Structure):
            _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]

        class _EventHotKeyID(Structure):
            _fields_ = [("signature", c_uint32), ("id", c_uint32)]

        handler_proc_t = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)

        carbon.GetApplicationEventTarget.restype = c_void_p
        carbon.InstallEventHandler.argtypes = [
            c_void_p,
            handler_proc_t,
            c_uint32,
            POINTER(_EventTypeSpec),
            c_void_p,
            POINTER(c_void_p),
        ]
        carbon.InstallEventHandler.restype = c_int32
        carbon.RegisterEventHotKey.argtypes = [
            c_uint32,
            c_uint32,
            _EventHotKeyID,
            c_void_p,
            c_uint32,
            POINTER(c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = c_int32
        carbon.UnregisterEventHotKey.argtypes = [c_void_p]
        carbon.UnregisterEventHotKey.restype = c_int32
        carbon.RemoveEventHandler.argtypes = [c_void_p]
        carbon.RemoveEventHandler.restype = c_int32

        cb = self.callback

        def _handler(_call_ref, _event, _user_data):
            try:
                cb()
            except Exception:
                pass
            return 0

        self._handler_proc = handler_proc_t(_handler)
        spec = _EventTypeSpec(_EVENT_CLASS_KEYBOARD, _EVENT_HOTKEY_PRESSED)
        handler_ref = c_void_p()
        err = carbon.InstallEventHandler(
            carbon.GetApplicationEventTarget(),
            self._handler_proc,
            1,
            ctypes.byref(spec),
            None,
            ctypes.byref(handler_ref),
        )
        if err != 0:
            print(f"[hotkey] InstallEventHandler failed: err={err}", flush=True)
            return False

        hotkey_ref = c_void_p()
        hk_id = _EventHotKeyID(0x414E4C48, 1)  # 'ANLH'
        err = carbon.RegisterEventHotKey(
            vk,
            modifiers,
            hk_id,
            carbon.GetApplicationEventTarget(),
            0,
            ctypes.byref(hotkey_ref),
        )
        if err != 0:
            print(f"[hotkey] RegisterEventHotKey failed: err={err}", flush=True)
            carbon.RemoveEventHandler(handler_ref)
            return False

        self._carbon = carbon
        self._handler_ref = handler_ref
        self._hotkey_ref = hotkey_ref
        print(
            f"[hotkey] registered {self.combo} (mods=0x{modifiers:x}, vk=0x{vk:x})",
            flush=True,
        )
        return True

    def unregister(self):
        if not self._carbon:
            return
        if self._hotkey_ref and self._hotkey_ref.value:
            try:
                self._carbon.UnregisterEventHotKey(self._hotkey_ref)
            except Exception:
                pass
        if self._handler_ref and self._handler_ref.value:
            try:
                self._carbon.RemoveEventHandler(self._handler_ref)
            except Exception:
                pass
        self._hotkey_ref = None
        self._handler_ref = None


class HotkeyBridge(QObject):
    triggered = Signal()

    def __init__(self, combo: str):
        super().__init__()
        self.combo = combo
        self._listener = None
        self._carbon = None

    def start(self) -> bool:
        if sys.platform == "darwin":
            # On macOS we only use Carbon. We do NOT fall back to pynput
            # because pynput's macOS listener crashes the process on
            # macOS 26 from a background-thread TSMGetInputSourceProperty
            # call. A failed-to-bind hotkey is recoverable; a SIGTRAP isn't.
            hk = _CarbonHotkey(self.combo, self._fire)
            if hk.register():
                self._carbon = hk
                return True
            return False
        # Non-mac: pynput is fine.
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
        if self._carbon:
            try:
                self._carbon.unregister()
            except Exception:
                pass
            self._carbon = None
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


# ---------- Optional mouse-button hotkey ----------

def _resolve_mouse_button(name: str):
    """Map a friendly button name to a pynput Button, or None if unsupported.

    Extra mouse buttons are platform-named in pynput (x1/x2 on Windows,
    button8/button9 on X11/macOS), so we try several attribute names and take
    the first that exists on this platform's Button enum.
    """
    if not name:
        return None
    if sys.platform == "darwin":
        # macOS uses a native CGEventTap path. Keep this resolver permissive
        # so x1/x2/back/forward do not fail early.
        return name if _mouse_button_number(name) is not None else None
    try:
        from pynput.mouse import Button
    except Exception:
        return None
    aliases = {
        "left": ["left"],
        "right": ["right"],
        "middle": ["middle"],
        "x1": ["x1", "button8", "back"],
        "back": ["x1", "button8", "back"],
        "x2": ["x2", "button9", "forward"],
        "forward": ["x2", "button9", "forward"],
    }
    for attr in aliases.get(name, [name]):
        btn = getattr(Button, attr, None)
        if btn is not None:
            return btn
    return None


# Stable Win32 mouse-message constants, for selective suppression.
_WM_LBUTTONDOWN, _WM_LBUTTONUP = 0x0201, 0x0202
_WM_RBUTTONDOWN, _WM_RBUTTONUP = 0x0204, 0x0205
_WM_MBUTTONDOWN, _WM_MBUTTONUP = 0x0207, 0x0208
_WM_XBUTTONDOWN, _WM_XBUTTONUP = 0x020B, 0x020C

# button name -> (down_msg, up_msg, xbutton_id or None)
_WIN32_BUTTON_MSGS = {
    "left":    (_WM_LBUTTONDOWN, _WM_LBUTTONUP, None),
    "right":   (_WM_RBUTTONDOWN, _WM_RBUTTONUP, None),
    "middle":  (_WM_MBUTTONDOWN, _WM_MBUTTONUP, None),
    "x1":      (_WM_XBUTTONDOWN, _WM_XBUTTONUP, 1),
    "back":    (_WM_XBUTTONDOWN, _WM_XBUTTONUP, 1),
    "x2":      (_WM_XBUTTONDOWN, _WM_XBUTTONUP, 2),
    "forward": (_WM_XBUTTONDOWN, _WM_XBUTTONUP, 2),
}

_MAC_MOUSE_BUTTON_NUMBERS = {
    "left": 0,
    "right": 1,
    "middle": 2,
    "x1": 3,
    "back": 3,
    "x2": 4,
    "forward": 4,
}


def _mouse_button_number(name: str | None) -> int | None:
    if not name:
        return None
    return _MAC_MOUSE_BUTTON_NUMBERS.get(name.strip().lower())


class _MacMouseTap:
    """macOS global mouse listener using CGEventTap."""

    def __init__(self, button_name: str, on_press, on_release, suppress: bool):
        self.button_name = (button_name or "").strip().lower()
        self.button_number = _mouse_button_number(self.button_name)
        self.on_press = on_press
        self.on_release = on_release
        self.suppress = suppress

        self._cg = None
        self._cf = None
        self._tap_ref = None
        self._source_ref = None
        self._run_loop = None
        self._common_modes = None
        self._handler_proc = None  # Keep callback wrapper alive.

    def register(self) -> bool:
        if self.button_number is None:
            return False

        import ctypes
        import ctypes.util
        from ctypes import CFUNCTYPE, c_bool, c_int64, c_long, c_uint32, c_uint64, c_void_p

        cg_path = ctypes.util.find_library("CoreGraphics") or (
            "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
        )
        cf_path = ctypes.util.find_library("CoreFoundation") or (
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        try:
            cg = ctypes.cdll.LoadLibrary(cg_path)
            cf = ctypes.cdll.LoadLibrary(cf_path)
        except OSError:
            return False

        kCGHIDEventTap = 0
        kCGHeadInsertEventTap = 0
        kCGEventTapOptionDefault = 0
        kCGEventLeftMouseDown = 1
        kCGEventLeftMouseUp = 2
        kCGEventRightMouseDown = 3
        kCGEventRightMouseUp = 4
        kCGEventOtherMouseDown = 25
        kCGEventOtherMouseUp = 26
        kCGEventTapDisabledByTimeout = 0xFFFFFFFE
        kCGMouseEventButtonNumber = 3  # CGEventField; was wrongly 89 (read 0 for all buttons)

        callback_t = CFUNCTYPE(c_void_p, c_void_p, c_uint32, c_void_p, c_void_p)

        cg.CGEventTapCreate.argtypes = [
            c_uint32,
            c_uint32,
            c_uint32,
            c_uint64,
            callback_t,
            c_void_p,
        ]
        cg.CGEventTapCreate.restype = c_void_p
        cg.CGEventTapEnable.argtypes = [c_void_p, c_bool]
        cg.CGEventTapEnable.restype = None
        cg.CGEventGetIntegerValueField.argtypes = [c_void_p, c_uint32]
        cg.CGEventGetIntegerValueField.restype = c_int64

        cf.CFMachPortCreateRunLoopSource.argtypes = [c_void_p, c_void_p, c_long]
        cf.CFMachPortCreateRunLoopSource.restype = c_void_p
        cf.CFRunLoopGetMain.argtypes = []
        cf.CFRunLoopGetMain.restype = c_void_p
        cf.CFRunLoopAddSource.argtypes = [c_void_p, c_void_p, c_void_p]
        cf.CFRunLoopAddSource.restype = None
        cf.CFRunLoopRemoveSource.argtypes = [c_void_p, c_void_p, c_void_p]
        cf.CFRunLoopRemoveSource.restype = None
        cf.CFRunLoopWakeUp.argtypes = [c_void_p]
        cf.CFRunLoopWakeUp.restype = None
        cf.CFRelease.argtypes = [c_void_p]
        cf.CFRelease.restype = None

        try:
            common_modes = c_void_p.in_dll(cf, "kCFRunLoopCommonModes")
        except Exception:
            return False

        watched = set()
        if self.button_number == 0:
            watched.update((kCGEventLeftMouseDown, kCGEventLeftMouseUp))
        elif self.button_number == 1:
            watched.update((kCGEventRightMouseDown, kCGEventRightMouseUp))
        else:
            watched.update((kCGEventOtherMouseDown, kCGEventOtherMouseUp))
        mask = 0
        for event_type in watched:
            mask |= (1 << event_type)

        press_cb = self.on_press
        release_cb = self.on_release

        def _callback(_proxy, event_type, event_ref, _user_info):
            if event_type == kCGEventTapDisabledByTimeout:
                try:
                    if self._tap_ref and self._tap_ref.value:
                        cg.CGEventTapEnable(self._tap_ref, True)
                except Exception:
                    pass
                return event_ref

            is_down = False
            is_up = False
            button_num = None

            if event_type == kCGEventOtherMouseDown:
                is_down = True
                try:
                    button_num = int(cg.CGEventGetIntegerValueField(event_ref, kCGMouseEventButtonNumber))
                except Exception:
                    return event_ref
            elif event_type == kCGEventOtherMouseUp:
                is_up = True
                try:
                    button_num = int(cg.CGEventGetIntegerValueField(event_ref, kCGMouseEventButtonNumber))
                except Exception:
                    return event_ref
            elif event_type == kCGEventLeftMouseDown:
                is_down = True
                button_num = 0
            elif event_type == kCGEventLeftMouseUp:
                is_up = True
                button_num = 0
            elif event_type == kCGEventRightMouseDown:
                is_down = True
                button_num = 1
            elif event_type == kCGEventRightMouseUp:
                is_up = True
                button_num = 1
            else:
                return event_ref

            if button_num != self.button_number:
                return event_ref

            try:
                if is_down:
                    press_cb()
                elif is_up:
                    release_cb()
            except Exception:
                pass

            if self.suppress:
                return None
            return event_ref

        self._handler_proc = callback_t(_callback)
        tap = cg.CGEventTapCreate(
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            mask,
            self._handler_proc,
            None,
        )
        if not tap:
            return False

        source = cf.CFMachPortCreateRunLoopSource(None, tap, 0)
        if not source:
            cf.CFRelease(tap)
            return False

        run_loop = cf.CFRunLoopGetMain()
        if not run_loop:
            cf.CFRelease(source)
            cf.CFRelease(tap)
            return False

        cf.CFRunLoopAddSource(run_loop, source, common_modes)
        cg.CGEventTapEnable(tap, True)
        cf.CFRunLoopWakeUp(run_loop)

        self._cg = cg
        self._cf = cf
        self._tap_ref = c_void_p(tap)
        self._source_ref = c_void_p(source)
        self._run_loop = c_void_p(run_loop)
        self._common_modes = common_modes
        return True

    def unregister(self) -> None:
        if not self._cg or not self._cf:
            return
        try:
            if self._tap_ref and self._tap_ref.value:
                self._cg.CGEventTapEnable(self._tap_ref, False)
        except Exception:
            pass
        try:
            if (
                self._run_loop
                and self._run_loop.value
                and self._source_ref
                and self._source_ref.value
                and self._common_modes
                and self._common_modes.value
            ):
                self._cf.CFRunLoopRemoveSource(
                    self._run_loop,
                    self._source_ref,
                    self._common_modes,
                )
                self._cf.CFRunLoopWakeUp(self._run_loop)
        except Exception:
            pass
        try:
            if self._source_ref and self._source_ref.value:
                self._cf.CFRelease(self._source_ref)
        except Exception:
            pass
        try:
            if self._tap_ref and self._tap_ref.value:
                self._cf.CFRelease(self._tap_ref)
        except Exception:
            pass
        self._tap_ref = None
        self._source_ref = None
        self._run_loop = None
        self._common_modes = None
        self._handler_proc = None


class MouseHotkeyBridge(QObject):
    """Global mouse-button listener. `pressed` fires on button down, `released`
    on button up; the window decides toggle vs push-to-talk from those.

    When `suppress` is set, the bound button's normal action (e.g. the browser
    back/forward that x1/x2 map to) is blocked while we use it as the trigger.
    Suppression is selective and platform-specific: only the one bound button is
    consumed, every other click passes through untouched.
    """

    pressed = Signal()
    released = Signal()

    def __init__(self, button_name: str, suppress: bool = False):
        super().__init__()
        self.button_name = button_name
        self._button = _resolve_mouse_button(button_name)
        self.suppress = suppress
        # Whether suppression is actually active (only on supported platforms).
        self.suppress_active = False
        self._win32_target = _WIN32_BUTTON_MSGS.get(button_name)
        self._listener = None
        self._mac_tap = None

    def start(self) -> bool:
        if sys.platform == "darwin":
            self._mac_tap = _MacMouseTap(
                self.button_name,
                self.pressed.emit,
                self.released.emit,
                suppress=self.suppress,
            )
            ok = self._mac_tap.register()
            self.suppress_active = bool(ok and self.suppress)
            return ok
        if self._button is None:
            return False
        try:
            from pynput import mouse
        except Exception:
            return False
        kwargs = {"on_click": self._on_click}
        # Selective suppression: pynput's event filter, when it returns False,
        # blocks the event system-wide AND skips on_click — so for the bound
        # button we emit from inside the filter instead.
        if self.suppress and sys.platform == "win32" and self._win32_target:
            kwargs["win32_event_filter"] = self._win32_filter
            self.suppress_active = True
        elif self.suppress and sys.platform == "darwin":
            kwargs["darwin_intercept"] = self._darwin_intercept
            self.suppress_active = True
        try:
            self._listener = mouse.Listener(**kwargs)
            self._listener.start()
            return True
        except Exception:
            self.suppress_active = False
            return False

    def _on_click(self, _x, _y, button, pressed, *_extra) -> None:
        # Active only on the non-suppressed path (suppressed events never reach
        # here). *_extra absorbs the `injected` arg newer pynput passes.
        if button != self._button:
            return
        if pressed:
            self.pressed.emit()
        else:
            self.released.emit()

    def _win32_filter(self, msg, data) -> bool:
        """Return False to consume the bound button, True to pass everything
        else through. Runs on the hook thread; emits are queued to the UI."""
        target = self._win32_target
        if target is None:
            return True
        down, up, xbutton = target
        if msg != down and msg != up:
            return True
        if xbutton is not None and (data.mouseData >> 16) != xbutton:
            return True  # a different X button (e.g. back when we want forward)
        if msg == down:
            self.pressed.emit()
        else:
            self.released.emit()
        # Returning False only stops pynput's own on_click dispatch; it does NOT
        # block the event system-wide, so the browser back/forward still fired.
        # suppress_event() raises SuppressException, which pynput's hook proc
        # turns into a non-zero return so Windows drops the click. It raises, so
        # it must be last (the emits above have already run).
        if self._listener is not None:
            self._listener.suppress_event()
        return False  # fallback if the listener isn't available to suppress

    def _darwin_intercept(self, event_type, event):
        """macOS selective suppression. Return the event to pass it on, or None
        to consume it. Best-effort; validate on Mac hardware."""
        try:
            import Quartz
        except Exception:
            return event
        down_types = {
            Quartz.kCGEventLeftMouseDown,
            Quartz.kCGEventRightMouseDown,
            Quartz.kCGEventOtherMouseDown,
        }
        up_types = {
            Quartz.kCGEventLeftMouseUp,
            Quartz.kCGEventRightMouseUp,
            Quartz.kCGEventOtherMouseUp,
        }
        if event_type not in down_types and event_type not in up_types:
            return event
        try:
            btn = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGMouseEventButtonNumber
            )
        except Exception:
            return event
        # macOS button numbers: 0 left, 1 right, 2 middle, 3 back, 4 forward.
        wanted = {
            "left": 0, "right": 1, "middle": 2,
            "x1": 3, "back": 3, "x2": 4, "forward": 4,
        }.get(self.button_name)
        if wanted is None or btn != wanted:
            return event
        if event_type in down_types:
            self.pressed.emit()
        else:
            self.released.emit()
        return None  # consume

    def stop(self) -> None:
        if self._mac_tap:
            try:
                self._mac_tap.unregister()
            except Exception:
                pass
            self._mac_tap = None
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


# ---------- Settings: capture bridges + dialog ----------

def _humanize_combo(combo: str) -> str:
    """Turn a pynput hotkey string into something readable for the UI."""
    if not combo:
        return "(none)"
    return (
        combo.replace("<ctrl>", "Ctrl")
        .replace("<alt>", "Alt")
        .replace("<shift>", "Shift")
        .replace("<cmd>", "Cmd")
        .replace("<space>", "Space")
        .replace("+", " + ")
    )


_MODIFIER_TOKENS = {
    "ctrl": "<ctrl>", "ctrl_l": "<ctrl>", "ctrl_r": "<ctrl>",
    "alt": "<alt>", "alt_l": "<alt>", "alt_r": "<alt>", "alt_gr": "<alt>",
    "shift": "<shift>", "shift_l": "<shift>", "shift_r": "<shift>",
    "cmd": "<cmd>", "cmd_l": "<cmd>", "cmd_r": "<cmd>",
}
_MODIFIER_ORDER = ["<ctrl>", "<alt>", "<shift>", "<cmd>"]


class _KeyCaptureBridge(QObject):
    """One-shot global keyboard capture. Emits a pynput hotkey string (e.g.
    "<ctrl>+<alt>+m") once the user presses a non-modifier key."""

    captured = Signal(str)

    def __init__(self):
        super().__init__()
        self._listener = None
        self._mods: list[str] = []

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except Exception:
            return False
        self._mods = []
        try:
            self._listener = keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            self._listener.start()
            return True
        except Exception:
            return False

    def _token_for(self, key) -> str | None:
        name = getattr(key, "name", None)
        if name and name in _MODIFIER_TOKENS:
            return _MODIFIER_TOKENS[name]
        return None

    def _on_press(self, key) -> None:
        from pynput import keyboard
        mod = self._token_for(key)
        if mod is not None:
            if mod not in self._mods:
                self._mods.append(mod)
            return  # wait for the main key
        main = None
        if isinstance(key, keyboard.KeyCode) and key.char:
            main = key.char.lower()
        elif isinstance(key, keyboard.Key):
            main = f"<{key.name}>"
        if not main:
            return
        mods = [m for m in _MODIFIER_ORDER if m in self._mods]
        self.captured.emit("+".join(mods + [main]))

    def _on_release(self, key) -> None:
        mod = self._token_for(key)
        if mod is not None and mod in self._mods:
            self._mods.remove(mod)

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


class _MouseCaptureBridge(QObject):
    """One-shot global mouse-button capture. Emits the friendly button name
    (left/right/middle/x1/x2) of the next button pressed."""

    captured = Signal(str)

    def __init__(self):
        super().__init__()
        self._listener = None

    def start(self) -> bool:
        try:
            from pynput import mouse
        except Exception:
            return False
        try:
            self._listener = mouse.Listener(on_click=self._on_click)
            self._listener.start()
            return True
        except Exception:
            return False

    def _on_click(self, _x, _y, button, pressed) -> None:
        if not pressed:
            return
        name = getattr(button, "name", "") or ""
        if name and name != "unknown":
            self.captured.emit(name)

    def stop(self) -> None:
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


_MODEL_CHOICES = [
    "tiny.en", "base.en", "small.en", "medium.en", "large-v3",
]
_DEVICE_CHOICES = ["cpu", "cuda", "auto"]


def _qt_keyevent_to_combo(event) -> str | None:
    """Convert a Qt key press into a hotkey combo string (e.g. "<ctrl>+<alt>+m"),
    matching the format _KeyCaptureBridge emits. Returns None for a pure modifier
    press or an unsupported key, so the caller keeps waiting.

    Used for capture-by-press on macOS, where a global pynput listener would need
    Accessibility and SIGTRAP on macOS 26. The dialog has focus, so Qt delivers
    the key events directly with no OS permission. Note macOS Qt swaps Ctrl/Meta:
    Qt.ControlModifier is the physical Command key, Qt.MetaModifier is Control.
    """
    key = event.key()
    if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta,
               Qt.Key_CapsLock, Qt.Key_AltGr, 0):
        return None

    mods = event.modifiers()
    tokens: list[str] = []
    if sys.platform == "darwin":
        if mods & Qt.MetaModifier:
            tokens.append("<ctrl>")   # physical Control (Qt swaps it to Meta)
        if mods & Qt.AltModifier:
            tokens.append("<alt>")
        if mods & Qt.ShiftModifier:
            tokens.append("<shift>")
        if mods & Qt.ControlModifier:
            tokens.append("<cmd>")    # physical Command (Qt swaps it to Control)
    else:
        if mods & Qt.ControlModifier:
            tokens.append("<ctrl>")
        if mods & Qt.AltModifier:
            tokens.append("<alt>")
        if mods & Qt.ShiftModifier:
            tokens.append("<shift>")
        if mods & Qt.MetaModifier:
            tokens.append("<cmd>")
    # Keep modifier order stable to match the pynput path.
    tokens = [m for m in _MODIFIER_ORDER if m in tokens]

    main = None
    if Qt.Key_A <= key <= Qt.Key_Z:
        main = chr(key).lower()
    elif Qt.Key_0 <= key <= Qt.Key_9:
        main = chr(key)
    elif Qt.Key_F1 <= key <= Qt.Key_F12:
        main = f"<f{key - Qt.Key_F1 + 1}>"
    else:
        specials = {
            Qt.Key_Space: "<space>", Qt.Key_Return: "<return>",
            Qt.Key_Enter: "<return>", Qt.Key_Tab: "<tab>",
            Qt.Key_Escape: "<escape>",
        }
        main = specials.get(key)
    if not main:
        return None
    return "+".join(tokens + [main])


# Canonical button name -> friendly label shown in the UI. The canonical name
# (x1/x2/...) stays the stored/resolved value; only the display changes.
_MOUSE_DISPLAY_LABELS = {
    "left": "Left",
    "right": "Right",
    "middle": "Middle",
    "x1": "Back",
    "x2": "Forward",
}


def _mouse_display_label(name: str) -> str:
    """Friendly label for a canonical mouse-button name, e.g. x1 -> 'Back'."""
    if not name:
        return "(none)"
    return _MOUSE_DISPLAY_LABELS.get(name, name)


def _qt_mousebutton_to_name(button) -> str | None:
    if button == Qt.MiddleButton:
        return "middle"
    if button == Qt.BackButton or button == getattr(Qt, "ExtraButton1", None):
        return "x1"
    if button == Qt.ForwardButton or button == getattr(Qt, "ExtraButton2", None):
        return "x2"
    if button == Qt.LeftButton:
        return "left"
    if button == Qt.RightButton:
        return "right"
    return None


class SettingsDialog(QDialog):
    """Program the keyboard shortcut and mouse button by pressing them, plus
    pick mouse mode / model / device. Returns a settings dict via .result_settings."""

    def __init__(self, parent, current: dict):
        super().__init__(parent)
        self.setWindowTitle("AgeniusNote Lite Settings")
        self.setObjectName("root")
        self.setMinimumWidth(420)

        self._hotkey = current.get("hotkey", "") or ""
        self._mouse_button = current.get("mouse_button", "") or ""
        self.result_settings: dict | None = None

        self._key_cap: _KeyCaptureBridge | None = None
        self._mouse_cap: _MouseCaptureBridge | None = None
        self._capturing_key = False  # macOS: Qt-native key capture in progress
        self._capturing_mouse = False

        grid = QGridLayout()
        grid.setContentsMargins(18, 16, 18, 12)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(12)

        # Keyboard shortcut
        grid.addWidget(QLabel("Keyboard shortcut"), 0, 0)
        self.kbd_label = QLabel(_humanize_combo(self._hotkey))
        self.kbd_label.setObjectName("titleAccent")
        grid.addWidget(self.kbd_label, 0, 1)
        self.kbd_btn = QPushButton("Change…")
        self.kbd_btn.clicked.connect(self._capture_key)
        grid.addWidget(self.kbd_btn, 0, 2)

        # Mouse button
        grid.addWidget(QLabel("Mouse button"), 1, 0)
        self.mouse_label = QLabel(_mouse_display_label(self._mouse_button))
        self.mouse_label.setObjectName("titleAccent")
        grid.addWidget(self.mouse_label, 1, 1)
        mouse_btns = QHBoxLayout()
        self.mouse_btn = QPushButton("Change…")
        self.mouse_btn.clicked.connect(self._capture_mouse)
        self.mouse_clear = QPushButton("None")
        self.mouse_clear.clicked.connect(self._clear_mouse)
        mouse_btns.addWidget(self.mouse_btn)
        mouse_btns.addWidget(self.mouse_clear)
        mouse_wrap = QWidget()
        mouse_wrap.setLayout(mouse_btns)
        grid.addWidget(mouse_wrap, 1, 2)

        # Mouse mode
        grid.addWidget(QLabel("Mouse mode"), 2, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["toggle", "hold"])
        self.mode_combo.setCurrentText(current.get("mouse_mode", "toggle") or "toggle")
        grid.addWidget(self.mode_combo, 2, 1, 1, 2)

        # Suppress the button's normal action (block browser back/forward, etc.)
        self.suppress_check = QCheckBox("Block the button's normal action (e.g. browser back/forward)")
        self.suppress_check.setChecked(bool(current.get("mouse_suppress", True)))
        grid.addWidget(self.suppress_check, 3, 0, 1, 3)

        # Model
        grid.addWidget(QLabel("Model"), 4, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(_MODEL_CHOICES)
        self.model_combo.setCurrentText(current.get("model", DEFAULT_MODEL) or DEFAULT_MODEL)
        grid.addWidget(self.model_combo, 4, 1, 1, 2)

        # Device
        grid.addWidget(QLabel("Device"), 5, 0)
        self.device_combo = QComboBox()
        self.device_combo.addItems(_DEVICE_CHOICES)
        self.device_combo.setCurrentText(current.get("device", DEFAULT_DEVICE) or DEFAULT_DEVICE)
        grid.addWidget(self.device_combo, 5, 1, 1, 2)

        self.hint = QLabel(
            "Tip: the OS only exposes left/right/middle and the two side buttons "
            "(x1/x2). Extra buttons on a gaming mouse aren't visible here; map one "
            "to a keyboard shortcut in your mouse software, then capture that key "
            "above. Model/device changes take effect on the next launch."
        )
        self.hint.setObjectName("version")
        self.hint.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(grid)
        body = QVBoxLayout()
        body.setContentsMargins(18, 0, 18, 14)
        body.addWidget(self.hint)
        body.addWidget(buttons)
        layout.addLayout(body)

        self.setStyleSheet(parent.styleSheet() if parent else QSS)

    # ---- capture flows ----

    def _capture_key(self) -> None:
        self._end_captures()
        self.kbd_btn.setText("Press keys… (Esc to cancel)")
        self.kbd_btn.setEnabled(False)
        self.kbd_label.setText("…")
        if sys.platform == "darwin":
            # Capture via Qt's own key events (the dialog has focus) instead of a
            # global pynput listener: pynput's macOS keyboard listener needs
            # Accessibility and SIGTRAPs on macOS 26, the same reason the runtime
            # hotkey uses Carbon. See keyPressEvent.
            self._capturing_key = True
            self.grabKeyboard()
            return
        self._key_cap = _KeyCaptureBridge()
        self._key_cap.captured.connect(self._on_key_captured)
        if not self._key_cap.start():
            self.kbd_btn.setText("Change…")
            self.kbd_btn.setEnabled(True)
            self.kbd_label.setText("capture unavailable")

    def keyPressEvent(self, event) -> None:
        if self._capturing_mouse:
            if event.key() == Qt.Key_Escape:
                self._capturing_mouse = False
                self.releaseMouse()
                self.mouse_btn.setText("Change…")
                self.mouse_btn.setEnabled(True)
                self.mouse_label.setText(_mouse_display_label(self._mouse_button))
            event.accept()
            return
        if self._capturing_key:
            if event.key() == Qt.Key_Escape:
                self._capturing_key = False
                self.releaseKeyboard()
                self.kbd_btn.setText("Change…")
                self.kbd_btn.setEnabled(True)
                self.kbd_label.setText(_humanize_combo(self._hotkey))
                event.accept()
                return
            combo = _qt_keyevent_to_combo(event)
            if combo:
                self._capturing_key = False
                self.releaseKeyboard()
                self._on_key_captured(combo)
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_key_captured(self, combo: str) -> None:
        self._hotkey = combo
        self.kbd_label.setText(_humanize_combo(combo))
        self.kbd_btn.setText("Change…")
        self.kbd_btn.setEnabled(True)
        self._end_captures()

    def _capture_mouse(self) -> None:
        self._end_captures()
        self.mouse_btn.setText("Click a button… (Esc to cancel)")
        self.mouse_btn.setEnabled(False)
        self.mouse_label.setText("…")
        if sys.platform == "darwin":
            self._capturing_mouse = True
            self.grabMouse()
            return
        self._mouse_cap = _MouseCaptureBridge()
        self._mouse_cap.captured.connect(self._on_mouse_captured)
        if not self._mouse_cap.start():
            self.mouse_btn.setText("Change…")
            self.mouse_btn.setEnabled(True)
            self.mouse_label.setText("capture unavailable")

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._capturing_mouse:
            name = _qt_mousebutton_to_name(event.button())
            if name:
                self._capturing_mouse = False
                self.releaseMouse()
                self._on_mouse_captured(name)
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_mouse_captured(self, name: str) -> None:
        self._mouse_button = name
        self.mouse_label.setText(_mouse_display_label(name))
        self.mouse_btn.setText("Change…")
        self.mouse_btn.setEnabled(True)
        self._end_captures()

    def _clear_mouse(self) -> None:
        self._end_captures()
        self._mouse_button = ""
        self.mouse_label.setText("(none)")
        self.mouse_btn.setText("Change…")
        self.mouse_btn.setEnabled(True)

    def _end_captures(self) -> None:
        if self._capturing_key:
            self._capturing_key = False
            self.releaseKeyboard()
            self.kbd_btn.setText("Change…")
            self.kbd_btn.setEnabled(True)
        if self._capturing_mouse:
            self._capturing_mouse = False
            self.releaseMouse()
            self.mouse_btn.setText("Change…")
            self.mouse_btn.setEnabled(True)
        if self._key_cap:
            self._key_cap.stop()
            self._key_cap = None
            self.kbd_btn.setText("Change…")
            self.kbd_btn.setEnabled(True)
        if self._mouse_cap:
            self._mouse_cap.stop()
            self._mouse_cap = None
            self.mouse_btn.setText("Change…")
            self.mouse_btn.setEnabled(True)

    def _on_save(self) -> None:
        self.result_settings = {
            "hotkey": self._hotkey or DEFAULT_HOTKEY,
            "mouse_button": self._mouse_button,
            "mouse_mode": self.mode_combo.currentText(),
            "mouse_suppress": self.suppress_check.isChecked(),
            "model": self.model_combo.currentText().strip() or DEFAULT_MODEL,
            "device": self.device_combo.currentText(),
        }
        self.accept()

    def reject(self) -> None:  # noqa: D102
        self._end_captures()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._end_captures()
        super().closeEvent(event)


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
    """Bundle id of the frontmost app, via LaunchServices (`lsappinfo`).

    We deliberately avoid the AppleScript route ("System Events ... whose
    frontmost is true") because sending that Apple Event requires the
    Automation/Apple Events TCC permission, which is a *separate* prompt from
    Accessibility and was silently failing auto-paste when not granted.
    `lsappinfo` reads the same information with no Automation permission.
    """
    import subprocess
    try:
        asn = subprocess.run(
            ["lsappinfo", "front"], capture_output=True, text=True, timeout=2
        ).stdout.strip()
        if not asn:
            return None
        out = subprocess.run(
            ["lsappinfo", "info", "-only", "bundleid", asn],
            capture_output=True, text=True, timeout=2,
        ).stdout.strip()
        # out looks like: "CFBundleIdentifier"="com.apple.Safari"
        if "=" in out:
            return out.rsplit("=", 1)[-1].strip().strip('"') or None
        return None
    except Exception:
        return None


def _macos_activate_bundle(bundle_id: str) -> None:
    """Bring an app to the foreground by bundle id, via LaunchServices (`open
    -b`). Like _macos_frontmost_bundle, this avoids the Apple Events/Automation
    permission that `tell application ... to activate` would require."""
    import subprocess
    try:
        subprocess.run(["open", "-b", bundle_id], timeout=2)
    except Exception:
        pass


def _macos_accessibility_trusted() -> bool:
    """Return True if this process is trusted for Accessibility (Privacy &
    Security > Accessibility).

    macOS silently drops cross-app synthetic keystrokes (the auto-paste Cmd+V)
    unless the app holds the Accessibility right. We probe AXIsProcessTrusted
    via ctypes so we can warn instead of failing silently.
    """
    if sys.platform != "darwin":
        return True
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("ApplicationServices")
        if not path:
            return True  # can't probe; assume OK rather than nag
        appservices = ctypes.cdll.LoadLibrary(path)
        appservices.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(appservices.AXIsProcessTrusted())
    except Exception:
        return True  # probe failed; don't block the user on a false negative


def _macos_open_accessibility_settings() -> None:
    import subprocess
    url = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
    try:
        subprocess.run(["open", url], timeout=2)
    except Exception:
        pass


def _macos_send_cmd_v() -> bool:
    """Post Cmd+V via CoreGraphics. Returns True on apparent success.

    pynput.keyboard.Controller cannot be used on macOS 26 - it calls
    TSMGetInputSourceProperty during __init__, which on macOS 26 asserts
    it must be on the main dispatch queue and SIGTRAPs the process from
    background threads. CGEventPost via ctypes has no such restriction.
    """
    import ctypes
    import ctypes.util
    from ctypes import c_bool, c_uint32, c_uint64, c_void_p

    cg_path = ctypes.util.find_library("CoreGraphics") or (
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    )
    try:
        cg = ctypes.cdll.LoadLibrary(cg_path)
    except OSError as exc:
        print(f"[paste] CoreGraphics load failed: {exc}", flush=True)
        return False

    cg.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint32, c_bool]
    cg.CGEventCreateKeyboardEvent.restype = c_void_p
    cg.CGEventSetFlags.argtypes = [c_void_p, c_uint64]
    cg.CGEventSetFlags.restype = None
    cg.CGEventPost.argtypes = [c_uint32, c_void_p]
    cg.CGEventPost.restype = None

    cf_path = ctypes.util.find_library("CoreFoundation") or (
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    cf = ctypes.cdll.LoadLibrary(cf_path)
    cf.CFRelease.argtypes = [c_void_p]
    cf.CFRelease.restype = None

    VK_V = 0x09
    K_CG_HID_EVENT_TAP = 0
    K_CG_EVENT_FLAG_MASK_COMMAND = 1 << 20

    down = cg.CGEventCreateKeyboardEvent(None, VK_V, True)
    if not down:
        return False
    cg.CGEventSetFlags(down, K_CG_EVENT_FLAG_MASK_COMMAND)
    cg.CGEventPost(K_CG_HID_EVENT_TAP, down)
    cf.CFRelease(down)

    up = cg.CGEventCreateKeyboardEvent(None, VK_V, False)
    if up:
        cg.CGEventSetFlags(up, K_CG_EVENT_FLAG_MASK_COMMAND)
        cg.CGEventPost(K_CG_HID_EVENT_TAP, up)
        cf.CFRelease(up)
    return True


def _send_paste_async(handle: object | None, delay_ms: int = 120) -> None:
    """Restore the captured foreground app, then simulate the platform's paste
    shortcut. Runs in a background thread so the Qt event loop can finish
    updating the clipboard before the keypress goes out.

    macOS: uses CGEventPost via ctypes (pynput.keyboard.Controller crashes
    on macOS 26 from a background thread).
    Other: uses pynput.keyboard.Controller.
    """
    def _go():
        time.sleep(delay_ms / 1000.0)
        _restore_foreground(handle)
        # Settle delay so the target window is truly focused before paste.
        # macOS reactivates via `open -b` (LaunchServices), which has more
        # latency than an in-process focus call, so give it longer.
        time.sleep(0.25 if sys.platform == "darwin" else 0.06)
        if sys.platform == "darwin":
            _macos_send_cmd_v()
            return
        try:
            from pynput.keyboard import Controller, Key
        except Exception:
            return
        kb = Controller()
        modifier = Key.ctrl
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

QPushButton#collapse {
    padding: 4px 0;
    font-size: 14px;
    font-weight: 600;
    color: #7ea8cc;
    border-color: rgba(255, 255, 255, 0.08);
}
QPushButton#collapse:hover {
    color: #d4e3f5;
    border-color: rgba(56, 189, 248, 0.50);
}
"""


class LiteWindow(QWidget):
    _preload_done = Signal(bool, str)  # (ok, message)

    def __init__(
        self,
        hotkey_combo: str,
        model_name: str,
        device_pref: str,
        mouse_button: str = "",
        mouse_mode: str = "toggle",
        mouse_suppress: bool = True,
    ):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("AgeniusNote Lite")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.resize(460, 320)

        icon_path = _resource_path("assets/icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.model_name = model_name
        self.device_pref = (device_pref or "cpu").lower()
        self.hotkey_combo = hotkey_combo
        self.mouse_button = mouse_button
        self.mouse_mode = mouse_mode
        self.mouse_suppress = mouse_suppress
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

        # Collapse / expand toggle. Collapsed shows just the status line + this
        # button row, so the always-on-top window is minimally invasive.
        self.btn_settings = QPushButton("⚙")
        self.btn_settings.setObjectName("collapse")
        self.btn_settings.setToolTip("Settings: program the hotkey and mouse button")
        self.btn_settings.setFixedWidth(28)
        self.btn_settings.clicked.connect(self._open_settings)

        self.btn_collapse = QPushButton("–")
        self.btn_collapse.setObjectName("collapse")
        self.btn_collapse.setToolTip("Collapse to mini bar")
        self.btn_collapse.setFixedWidth(28)
        self.btn_collapse.clicked.connect(self._toggle_collapse)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_record)
        btn_row.addWidget(self.btn_copy)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_paste_toggle)
        btn_row.addWidget(self.btn_settings)
        btn_row.addWidget(self.btn_collapse)

        # Layout — header strip stretches edge-to-edge, body has padding
        self.header = header
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.header)
        body = QVBoxLayout()
        body.setContentsMargins(12, 10, 12, 12)
        body.setSpacing(8)
        body.addWidget(self.status)
        body.addWidget(self.transcript, 1)
        body.addLayout(btn_row)
        self.body_widget = QWidget()
        self.body_widget.setLayout(body)
        root.addWidget(self.body_widget, 1)

        self._collapsed = False
        self._expanded_size = (460, 320)

        self.setStyleSheet(QSS)

        # In-window shortcut: Ctrl+Shift+R to toggle manual record while focused
        QShortcut(QKeySequence("Ctrl+Shift+R"), self, self._toggle_manual)

        # Global hotkey
        self.hotkey = HotkeyBridge(hotkey_combo)
        self.hotkey.triggered.connect(self._toggle_hotkey)
        self._hotkey_ok = self.hotkey.start()
        if not self._hotkey_ok:
            self.status.setText(
                f"Global hotkey unavailable. Click Record. Model: {model_name}"
            )

        # Optional global mouse-button hotkey (off unless a button is set).
        self.mouse_hotkey: MouseHotkeyBridge | None = None
        self._mouse_ok = False
        self._start_mouse_bridge()

        # macOS auto-paste can use Accessibility, but it is optional.
        # Keep a one-time prompt only when the user actually tries auto-paste.
        self._accessibility_warned = False

        # Tracks whether the current recording was started via the global hotkey.
        # Only hotkey-driven sessions auto-paste into the focused window.
        self._session_via_hotkey = False

        # Warm the Whisper model in the background so the first hotkey press
        # doesn't pay for cold-start (~5-15s on CPU int8 for base.en).
        self._preload_done.connect(self._on_preload_done)
        self.status.setText(f"Warming up {self.model_name} model...")
        threading.Thread(target=self._preload_model, daemon=True).start()

    def _preload_model(self) -> None:
        try:
            _get_model(self.model_name, self.device_pref)
            self._preload_done.emit(True, "")
        except Exception as e:
            self._preload_done.emit(False, str(e))

    def _on_preload_done(self, ok: bool, msg: str) -> None:
        if ok:
            self.status.setText(self._status_idle())
            return
        self.status.setText(f"Model preload failed: {msg}")
        box = QMessageBox(self)
        box.setWindowTitle("AgeniusNote Lite: model download failed")
        box.setIcon(QMessageBox.Critical)
        box.setText(
            "AgeniusNote Lite couldn't download the Whisper model on first launch."
        )
        box.setInformativeText(
            f"Model: {self.model_name}\n"
            f"Cache: {_user_models_dir()}\n\n"
            "Most common cause is no internet access on first run. The model is "
            "fetched once from Hugging Face and cached locally; subsequent "
            "launches work offline.\n\nError:\n" + msg
        )
        box.exec()

    # ---- helpers ----

    def _human_combo(self) -> str:
        return _humanize_combo(self.hotkey_combo)

    def _status_idle(self) -> str:
        dev = ""
        if _WHISPER_CACHE["device"]:
            dev = f"  ·  {_WHISPER_CACHE['device']}/{_WHISPER_CACHE['compute']}"
        mouse = ""
        if getattr(self, "_mouse_ok", False):
            mouse = f"  ·  {_mouse_display_label(self.mouse_button)} ({self.mouse_mode})"
        return f"Ready  ·  {self.model_name}{dev}  ·  hotkey {self._human_combo()}{mouse}"

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

    def _mouse_hold_start(self) -> None:
        # Push-to-talk down: begin a hotkey-style (auto-paste) session.
        if not self.recording:
            self._toggle(via_hotkey=True)

    def _mouse_hold_stop(self) -> None:
        # Push-to-talk up: stop and transcribe.
        if self.recording:
            self._toggle(via_hotkey=True)

    def _prompt_macos_accessibility(self) -> None:
        """One-time nudge so macOS auto-paste actually works. Without the
        Accessibility right, the synthetic Cmd+V is silently dropped."""
        if self._accessibility_warned:
            return
        self._accessibility_warned = True
        box = QMessageBox(self)
        box.setWindowTitle("AgeniusNote Lite: enable auto-paste")
        box.setIcon(QMessageBox.Information)
        box.setText("AgeniusNote Lite needs Accessibility access to auto-paste.")
        box.setInformativeText(
            "Recording, transcription, hotkeys, and clipboard copy work "
            "without Accessibility.\n\n"
            "macOS blocks simulated keystrokes (the Cmd+V that drops your "
            "dictation into the focused app) until you grant Accessibility "
            "access for auto-paste.\n\n"
            "Open Settings, then enable \"AgeniusNote Lite\" under "
            "Privacy & Security > Accessibility. You only do this once.\n\n"
            "Until then, dictation still works; the text is copied to your "
            "clipboard and you can paste it manually."
        )
        open_btn = box.addButton("Open Accessibility Settings", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            _macos_open_accessibility_settings()

    # ---- hotkey / mouse binding ----

    def _start_mouse_bridge(self) -> None:
        """(Re)start the global mouse listener from self.mouse_button / mode.
        No-op when no button is configured."""
        if self.mouse_hotkey:
            self.mouse_hotkey.stop()
            self.mouse_hotkey = None
        self._mouse_ok = False
        if not self.mouse_button:
            return
        self.mouse_hotkey = MouseHotkeyBridge(self.mouse_button, suppress=self.mouse_suppress)
        if self.mouse_mode == "hold":
            # Push-to-talk: down starts a hotkey-style session, up stops it.
            self.mouse_hotkey.pressed.connect(self._mouse_hold_start)
            self.mouse_hotkey.released.connect(self._mouse_hold_stop)
        else:
            # Toggle: down flips record state, ignore the matching release.
            self.mouse_hotkey.pressed.connect(self._toggle_hotkey)
        self._mouse_ok = self.mouse_hotkey.start()

    def _open_settings(self) -> None:
        """Open the settings dialog. Live global listeners are paused while it's
        open so capturing a shortcut doesn't also trigger recording."""
        current = {
            "hotkey": self.hotkey_combo,
            "mouse_button": self.mouse_button,
            "mouse_mode": self.mouse_mode,
            "mouse_suppress": self.mouse_suppress,
            "model": self.model_name,
            "device": self.device_pref,
        }
        self.hotkey.stop()
        if self.mouse_hotkey:
            self.mouse_hotkey.stop()

        dlg = SettingsDialog(self, current)
        accepted = dlg.exec() == QDialog.Accepted

        if accepted and dlg.result_settings:
            self._apply_settings(dlg.result_settings)
        else:
            # Cancelled: re-arm the listeners we paused, unchanged.
            self._hotkey_ok = self.hotkey.start()
            self._start_mouse_bridge()

    def _apply_settings(self, s: dict) -> None:
        try:
            save_settings(s)
        except Exception as exc:
            self.status.setText(f"Couldn't save settings: {exc}")

        rebind_model = s.get("model", self.model_name) != self.model_name
        self.hotkey_combo = s.get("hotkey", self.hotkey_combo) or self.hotkey_combo
        self.mouse_button = s.get("mouse_button", "") or ""
        self.mouse_mode = s.get("mouse_mode", "toggle") or "toggle"
        self.mouse_suppress = bool(s.get("mouse_suppress", self.mouse_suppress))
        self.model_name = s.get("model", self.model_name) or self.model_name
        self.device_pref = (s.get("device", self.device_pref) or "cpu").lower()

        # Rebind keyboard hotkey to the new combo.
        self.hotkey.stop()
        self.hotkey = HotkeyBridge(self.hotkey_combo)
        self.hotkey.triggered.connect(self._toggle_hotkey)
        self._hotkey_ok = self.hotkey.start()

        # Rebind mouse listener.
        self._start_mouse_bridge()

        self.transcript.setPlaceholderText(
            f"Press {self._human_combo()} anywhere to dictate into the focused window.\n"
            "Or click Record to dictate into this box."
        )
        if rebind_model:
            self.status.setText(
                f"Saved. Model change to {self.model_name} takes effect next launch."
            )
        else:
            self.status.setText(self._status_idle())

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
        self.worker = TranscribeWorker(
            wav,
            paste_after,
            self.model_name,
            self.device_pref,
            self._target_handle,
        )
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
        # A CUDA->CPU fallback still produced a valid transcript, so it must not
        # short-circuit auto-paste (doing so silently disabled paste whenever
        # device was "auto"/"cuda" on a CPU-only build). Note it in the suffix
        # instead and fall through to the normal paste/copy handling.
        fallback_note = "  ·  CPU fallback" if meta.get("fallback_reason") else ""
        if meta.get("paste_after"):
            if sys.platform == "darwin" and not _macos_accessibility_trusted():
                # Don't pretend we pasted; the keystroke would be dropped.
                self.status.setText(
                    f"Copied, grant Accessibility to auto-paste ({elapsed} ms, {dev})"
                )
                self._prompt_macos_accessibility()
                return
            _send_paste_async(meta.get("target_handle"))
            self.status.setText(f"Pasted ({elapsed} ms, {dev}){fallback_note}")
        else:
            self.status.setText(f"Copied ({elapsed} ms, {dev}){fallback_note}")

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

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._expanded_size = (self.width(), self.height())
            self.header.setVisible(False)
            self.transcript.setVisible(False)
            self.btn_collapse.setText("+")
            self.btn_collapse.setToolTip("Expand")
            # Let the layout shrink to status + btn row only.
            self.setMinimumHeight(0)
            self.resize(self._expanded_size[0], 1)
            self.adjustSize()
        else:
            self.header.setVisible(True)
            self.transcript.setVisible(True)
            self.btn_collapse.setText("–")
            self.btn_collapse.setToolTip("Collapse to mini bar")
            self.resize(*self._expanded_size)

    def closeEvent(self, event) -> None:  # noqa: N802
        self.hotkey.stop()
        if self.mouse_hotkey:
            self.mouse_hotkey.stop()
        super().closeEvent(event)


def main() -> int:
    # CRITICAL for frozen macOS / Windows builds: without this, every
    # multiprocessing child (sounddevice, ctranslate2 thread helpers, etc.)
    # re-execs the .app bundle and spawns a fresh Qt window. v1.0.2 shipped
    # without it and produced a window-spawning loop on first launch.
    multiprocessing.freeze_support()
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("AgeniusNote Lite")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Agenius AI Labs")
    icon_path = _resource_path("assets/icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    # Effective config: saved settings win, then env vars, then hardcoded
    # defaults. Env still seeds first run and works for power users / CI.
    settings = load_settings()
    win = LiteWindow(
        settings.get("hotkey") or DEFAULT_HOTKEY,
        settings.get("model") or DEFAULT_MODEL,
        settings.get("device") or DEFAULT_DEVICE,
        mouse_button=settings.get("mouse_button", DEFAULT_MOUSE_BUTTON),
        mouse_mode=settings.get("mouse_mode") or DEFAULT_MOUSE_MODE,
        mouse_suppress=bool(settings.get("mouse_suppress", DEFAULT_MOUSE_SUPPRESS)),
    )
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
