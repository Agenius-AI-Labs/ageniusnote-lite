# Changelog

All notable changes to AgeniusNote Lite are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.7] - 2026-05-27

### Changed
- **Mouse buttons show friendly names in the UI.** The side buttons now display as "Back" and "Forward" (and "Middle"/"Left"/"Right") in Settings and the status bar, instead of the internal `x1`/`x2` tokens. Stored/configured values are unchanged.

## [1.0.6] - 2026-05-27

### Fixed
- **Windows mouse-button suppression didn't actually block the button.** Binding the back/forward side button as the trigger fired dictation but the browser still navigated, because the `win32_event_filter` only returned `False` (which just skips pynput's own click dispatch) without calling `suppress_event()`. The filter now calls `suppress_event()`, so pynput's hook proc returns non-zero and Windows drops the click. Bound button is consumed; every other click passes through.
- **Auto-paste silently stopped working when device was `auto`/`cuda` on a CPU-only build.** A CUDA transcribe that fell back to CPU still produced a valid transcript, but `_on_transcribed` returned early on the fallback and never reached the paste call, so the text only landed on the clipboard. The fallback now annotates the status line ("CPU fallback") and falls through to the normal paste/copy path. Most visible on Windows, where the shipped installer is CPU-only and a saved `device: "auto"` setting triggered the fallback every time.
- **Windows model download crashed on first run with "'NoneType' object has no attribute 'write'".** The PyInstaller windowed build runs with no console, so `sys.stdout`/`sys.stderr` are `None`. The first-run Whisper model download streams tqdm progress to `sys.stderr`, which then blew up before the model finished downloading, so dictation never worked on a fresh install. The missing streams are now redirected to the null device at startup. Only affected the first launch (before the model was cached).
- **macOS keyboard hotkey crashed and didn't survive ad-hoc signing.** The global keyboard hotkey now uses Carbon `RegisterEventHotKey` via ctypes on macOS instead of pynput. pynput's macOS listener uses a Quartz event tap (needs Accessibility, which macOS keys to the code signature, so ad-hoc rebuilds lose the grant) and calls `TSMGetInputSourceProperty` from a background thread, which macOS 26 asserts must be the main dispatch queue and SIGTRAPs the process. Carbon `RegisterEventHotKey` is the OS-level dispatcher used by Slack/iTerm: no Accessibility, no event tap, no background-thread crash. **Input Monitoring is no longer required for the keyboard hotkey.** Windows/Linux keep pynput.
- **macOS auto-paste did nothing.** After dictation the transcript was copied to the clipboard but the Cmd+V never landed in the focused app. Root cause: the synthetic paste keystroke is silently dropped by macOS unless the app holds the **Accessibility** right, and the prior ad-hoc-signed builds made that permission grant unstable across launches. The paste is now posted via `CGEventPost` (CoreGraphics); `pynput.keyboard.Controller` hits the same macOS-26 TSM SIGTRAP as the hotkey, so it had to go. AgeniusNote Lite now probes `AXIsProcessTrusted` and shows a one-time prompt when auto-paste is attempted without Accessibility, then reports "Copied, grant Accessibility to auto-paste" instead of falsely claiming it pasted. Accessibility is now **optional**: without it, recording, transcription, and clipboard still work; only the auto-paste keystroke needs it. Proper Developer ID signing + notarization (below) stabilizes the grant so it sticks.

### Added
- **Apple notarization.** Builds can now be Developer ID signed with a hardened runtime and notarized end to end. `packaging/build.sh` gained codesign + `notarytool` + `stapler` steps (gated on `VN_SIGN_IDENTITY` and notary credentials), with a matching `packaging/entitlements.plist`. The GitHub Actions release workflow imports a Developer ID cert from secrets and signs + notarizes tagged releases. Notarized DMGs install without the Gatekeeper "unidentified developer" block, so the `xattr -dr com.apple.quarantine` workaround is no longer needed.
- **In-app Settings menu.** A gear button opens a settings dialog where you program the keyboard shortcut and the mouse button by pressing them (capture-by-press, so any key combo or mouse button works), plus pick the mouse mode, model, and device. Settings persist to `settings.json` in the per-user app folder and rebind live without a restart, so the installer/.app builds are configurable without setting environment variables. Saved settings take precedence over the `VN_LITE_*` env vars; env still seeds first run.
- **Optional mouse-button hotkey.** Set `VN_LITE_MOUSE_BUTTON` (`x1`/`back`, `x2`/`forward`, or `middle`) to trigger dictation from a mouse button. `VN_LITE_MOUSE_MODE` picks `toggle` (click to start, click to stop, matching the keyboard hotkey) or `hold` (push-to-talk: press to record, release to transcribe). Off by default, so normal clicks are never intercepted.
- **Mouse-button suppression.** When a mouse button is bound, its normal action is blocked while in use, so binding the forward/back side buttons no longer also navigates the browser. Selective and per-button: only the bound button is consumed, every other click passes through. Toggle it with the "Block the button's normal action" checkbox in Settings or `VN_LITE_MOUSE_SUPPRESS` (default on). Implemented via pynput's `win32_event_filter` on Windows and `darwin_intercept` on macOS.

## [1.0.3] - 2026-05-25

### Fixed
- **macOS launch loop / runaway window spawning on first run.** The frozen `.app` was missing `multiprocessing.freeze_support()`, so any multiprocessing child (sounddevice / ctranslate2 thread helpers) re-execed the entire bundle and spawned a fresh Qt window. Each child then hit bug #2 below, crashed, and macOS LaunchServices re-ran the bundle, producing what looked like a Dock-icon-bouncing loop with dozens of windows. Calling `freeze_support()` + `set_start_method("spawn", force=True)` at the top of `main()` kills the loop.
- **Whisper model download segfault on first run.** Passing a HF Hub model id directly to `WhisperModel(...)` makes libctranslate2 download from inside native code, and any failure (no internet, partial cache, sandbox permission) crashes inside spdlog's error path with `EXC_BAD_ACCESS` instead of raising a clean Python exception. Now we pre-download with `huggingface_hub.snapshot_download` to `~/Library/Application Support/AgeniusNote Lite/models/` (mac) or `%LOCALAPPDATA%\AgeniusNote Lite\models\` (win), validate `model.bin` exists and is non-trivial, then pass the local path to `WhisperModel`. Failures now surface as a clear Qt error dialog.

### Added
- Explicit `huggingface_hub` dependency in `requirements.txt` (was already a transitive dep of faster-whisper; pinning it removes the implicit chain).

### Known limitations
- The `.app` is still ad-hoc signed, not Apple-notarized. Users downloading via Chrome will see the quarantine flag; until notarization lands, the workaround is `xattr -dr com.apple.quarantine "/Applications/AgeniusNote Lite.app"` after install.

## [1.0.2] - 2026-05-20

### Fixed
- **In-app version label drift.** The window title was hardcoded to "1.0.0" even after the package bumped to 1.0.1, which made support triage confusing. The version is now read at runtime from `packaging/VERSION`, bundled into the PyInstaller `.app` / installer payload so frozen mode resolves correctly.
- **`build.ps1 -SkipBuild` and `build.sh SKIP_BUILD=1` are no longer broken.** Both scripts used to allocate a fresh timestamped output directory even when re-running just the installer/DMG step, so the packaging tools would point at an empty path. They now reuse the most recent `dist-build-*` (Win) or `dist-build-mac-*` (macOS) directory that contains a built bundle.
- **`scripts/build_icon.py`** now writes generated icons to `<repo_root>/assets/`. Previously it wrote to `voice_notes_v3/assets/`, a monorepo-only path that does not exist in this OSS repo, so re-running the script silently produced files that nobody saw.

### Changed
- Top-of-file docstring in `voice_notes_lite.py` corrected: the default hotkey is `Ctrl+Alt+M`, not `Ctrl+Alt+Space`.

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

[1.0.4]: https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.4
[1.0.3]: https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.3
[1.0.2]: https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.2
[1.0.1]: https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.1
[1.0.0]: https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.0
