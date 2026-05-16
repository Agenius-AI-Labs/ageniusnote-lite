# PyInstaller spec for AgeniusNote Lite (Windows + macOS).
#
# Build:
#     cd apps/voice-notes-desktop
#     pyinstaller packaging/ageniusnote_lite.spec --noconfirm --clean
#
# Output:
#     Windows : dist/AgeniusNoteLite/AgeniusNoteLite.exe   (one-folder)
#     macOS   : dist/AgeniusNoteLite.app                   (.app bundle)
#
# We deliberately use --onedir (not --onefile) because faster-whisper +
# CTranslate2 + onnxruntime ship hundreds of MB of native binaries, and
# --onefile unpacks them to a temp dir on every launch (slow start, AV scans).
from __future__ import annotations

import sys
from pathlib import Path

block_cipher = None

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

here = Path.cwd()
app_dir = here  # caller should `cd apps/voice-notes-desktop` first
entry = str(app_dir / "voice_notes_lite.py")

icon_path = str(app_dir / "assets" / ("icon.icns" if IS_MAC else "icon.ico"))

datas = [
    (str(app_dir / "voice_notes_v3" / "assets"), "assets"),
]

hiddenimports = []
if IS_WIN:
    hiddenimports += [
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
    ]
if IS_MAC:
    hiddenimports += [
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
    ]

a = Analysis(
    [entry],
    pathex=[str(app_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Faster-whisper uses CTranslate2, not PyTorch. Drop ~4.2 GB of torch.
        "torch",
        "torchvision",
        "torchaudio",
        # Transformers pulls torch back in and isn't needed (faster-whisper
        # uses `tokenizers` directly with a bundled vocab).
        "transformers",
        # Numba/llvmlite (~100 MB) come in transitively but aren't used.
        "numba",
        "llvmlite",
        # We don't use openwakeword in lite mode -> drops sklearn + tflite.
        "openwakeword",
        "sklearn",
        "scikit_learn",
        # Note: PyAV (~74 MB) IS required by faster-whisper for audio decoding,
        # even when feeding it a WAV file path. Do not exclude.
        # General junk pulled in by transitive deps.
        "tkinter",
        "matplotlib",
        "pytest",
        "IPython",
        "jupyter",
        "notebook",
        "pandas",
        "lxml",
        "cryptography",  # only used for HF auth; not needed for local models
        "hf_xet",        # huggingface accelerated download backend
        "pyarrow",
        "scipy",         # sklearn / openwakeword dep; not used elsewhere
        "websockets",
        "yaml",
        "zstandard",
        "yarl",
        "aiohttp",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AgeniusNoteLite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AgeniusNoteLite",
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name="AgeniusNote Lite.app",
        icon=icon_path,
        bundle_identifier="com.ageniusailabs.ageniusnote-lite",
        info_plist={
            "CFBundleName": "AgeniusNote Lite",
            "CFBundleDisplayName": "AgeniusNote Lite",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            # Required permission prompts on first run:
            "NSMicrophoneUsageDescription":
                "AgeniusNote Lite uses the microphone to transcribe your voice "
                "into text locally on your machine.",
            "NSAppleEventsUsageDescription":
                "AgeniusNote Lite uses Apple Events to detect the focused app "
                "so it can paste your dictation into the right window.",
        },
    )
