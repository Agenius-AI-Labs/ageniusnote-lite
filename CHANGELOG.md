# Changelog

All notable changes to AgeniusNote Lite are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-05-20

### Fixed
- Transcription crash on first run after installer launch. The PyInstaller bundle was missing `yaml` and a couple of CTranslate2 runtime data files, so the first hotkey press would raise `ModuleNotFoundError: No module named 'yaml'` before the model loaded. Spec now keeps `yaml` and pulls the required CTranslate2 assets explicitly.

### Added
- **Collapse toggle** on the Lite window. Click the chevron to shrink the window to a compact strip that still shows the recording indicator. Useful when you want the hotkey workflow without the full notepad pane on screen.
- **Background model preload.** `base.en` (or whatever `VN_LITE_MODEL` is set to) is loaded on a worker thread at app startup instead of on first hotkey press. First transcription is now snappy instead of paying the model-load cost mid-dictation.
- **Intel Mac build path** added to the GHA workflow (matrix job on macos-13).

### Changed
- Mac DMG naming now includes the architecture suffix. Old `AgeniusNoteLite-Setup-x.y.z.dmg` is replaced by `AgeniusNoteLite-Setup-x.y.z-arm64.dmg`.

### Known limitations
- **Intel Mac DMG not attached to this release.** GitHub Actions' macos-13 Intel runner pool failed to assign a runner within 65+ minutes after the tag push, so the Intel build was canceled. Apple Silicon Mac users are unaffected. The build path is wired and ready; we'll attach `AgeniusNoteLite-Setup-1.0.1-x86_64.dmg` to this release as soon as a runner becomes available. Track [the v1.0.1 release page](https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.1) or file an issue if you're blocked on Intel.

## [1.0.0] - 2026-05-16

### Added
- Initial public release.
- Push-to-talk dictation via global hotkey (default `Ctrl+Alt+M`), pastes transcription into the focused window.
- In-window record button (no auto-paste mode).
- Local speech-to-text with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), `base.en` by default. Configurable via `VN_LITE_MODEL`.
- CPU and CUDA device strategies via `VN_LITE_DEVICE`.
- Configurable hotkey via `VN_LITE_HOTKEY`.
- Windows installer (Inno Setup) with optional auto-start and desktop icon.
- macOS DMG with Gatekeeper-friendly first-launch instructions.
- Cyberpunk theme.

[1.0.1]: https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.1
[1.0.0]: https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.0
