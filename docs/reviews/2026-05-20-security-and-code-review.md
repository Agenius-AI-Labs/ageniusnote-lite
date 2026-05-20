# AgeniusNote Lite Security and Code Review

Date: 2026-05-20  
Repository: `Agenius-AI-Labs/ageniusnote-lite`  
Commit reviewed: `9f25d67`

## Review scope

Full repository review of tracked source and packaging files, including:

- `voice_notes_lite.py`
- `packaging/ageniusnote_lite.spec`
- `packaging/installer.iss`
- `packaging/build.ps1`
- `packaging/build.sh`
- `.github/workflows/release.yml`
- `scripts/build_icon.py`
- `requirements.txt`
- `README.md`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`, `ROADMAP.md`
- `.gitignore`, `.gitattributes`, `packaging/VERSION`

## Executive summary

The prior security review was validated and is substantially correct for commit `9f25d67`.  
No additional high-confidence security vulnerabilities were identified in this pass.

Code review found several non-security correctness and maintenance issues, primarily around version consistency and packaging script behavior.

## Security verification result

Verified claims:

- No use of `eval`, `exec`, `pickle`, or equivalent dynamic code execution in application code.
- `subprocess` usage is limited to macOS `osascript` invocations in focused-window handling.
- Temporary WAV handling uses `NamedTemporaryFile(..., delete=False, suffix=".wav")` and cleanup in `finally`.
- Workflow triggers are restricted to tag pushes (`v*`) and `workflow_dispatch`; no `pull_request` or `pull_request_target` trigger.
- Installer is configured with low privilege requirement and no custom Inno `[Code]` block.

Hardening observations retained:

- Third-party GitHub Actions are tag-pinned, not SHA-pinned.
- Several dependencies are unpinned in `requirements.txt`.

## Findings (code review)

### 1) Medium: Runtime app version is stale

`APP_VERSION` is hardcoded to `1.0.0` while `packaging/VERSION` and changelog are at `1.0.1`.  
This can mislead support, troubleshooting, and vulnerability triage when users report versions.

- `voice_notes_lite.py:89`
- `packaging/VERSION:1`
- `CHANGELOG.md:7`

### 2) Medium: `build.ps1 -SkipBuild` does not reuse existing build artifacts

When `-SkipBuild` is used, the script still creates a fresh timestamped dist path and points Inno Setup at that new path, which breaks the stated "skip PyInstaller" flow unless artifacts happen to exist there.

- `packaging/build.ps1:66`
- `packaging/build.ps1:84`
- `packaging/build.ps1:109`

### 3) Medium: `build.sh` has the same skip-build path issue

With `SKIP_BUILD=1`, the script still computes a new timestamped output directory and then requires `.app` at that new path, so skip-build repackaging is effectively broken for normal reuse scenarios.

- `packaging/build.sh:26`
- `packaging/build.sh:60`
- `packaging/build.sh:71`

### 4) Low: Icon build script outputs to the wrong directory

`scripts/build_icon.py` writes assets under `voice_notes_v3/assets` instead of this repo's `assets`.  
This creates a maintenance trap where regenerated icons do not update shipped assets.

- `scripts/build_icon.py:151`

### 5) Low: Hotkey default mismatch in module docstring

The top docstring says the default global hotkey is Ctrl+Alt+Space, but code defaults to Ctrl+Alt+M.

- `voice_notes_lite.py:10`
- `voice_notes_lite.py:85`

## Residual risk and testing gaps

- No automated test suite is present in this repository.
- Installer outputs were reviewed statically from scripts/spec/workflow in this pass; full Windows/macOS build execution was not part of this document.

## Recommended next actions

1. Synchronize runtime version with `packaging/VERSION` (single source of truth).
2. Fix skip-build logic in both packaging scripts so repackaging reuses an explicit existing dist path.
3. Correct `scripts/build_icon.py` output target to this repository's `assets` directory.
4. Clean up docstring/config drift for default hotkey.
5. Optionally apply hardening: SHA-pin GitHub Actions and pin runtime dependencies for reproducible builds.

## Resolution (2026-05-20)

All five code-review findings were addressed the same day, in a single commit on the public repo, and shipped in the v1.0.2 release. Items 1 through 5 below mirror the numbered findings above.

| # | Finding | Status | Fix commit | Shipped in |
|---|---|---|---|---|
| 1 | Runtime app version stale (`APP_VERSION = "1.0.0"`) | Resolved | [`7dcb49a`](https://github.com/Agenius-AI-Labs/ageniusnote-lite/commit/7dcb49a) | [v1.0.2](https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.2) |
| 2 | `build.ps1 -SkipBuild` allocates a fresh dist path instead of reusing one | Resolved | [`7dcb49a`](https://github.com/Agenius-AI-Labs/ageniusnote-lite/commit/7dcb49a) | [v1.0.2](https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.2) |
| 3 | `build.sh SKIP_BUILD=1` has the same defect | Resolved | [`7dcb49a`](https://github.com/Agenius-AI-Labs/ageniusnote-lite/commit/7dcb49a) | [v1.0.2](https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.2) |
| 4 | `scripts/build_icon.py` writes to `voice_notes_v3/assets/` (monorepo-only path) | Resolved | [`7dcb49a`](https://github.com/Agenius-AI-Labs/ageniusnote-lite/commit/7dcb49a) | [v1.0.2](https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.2) |
| 5 | Top-of-file docstring claims default hotkey is `Ctrl+Alt+Space`; actual default is `Ctrl+Alt+M` | Resolved | [`7dcb49a`](https://github.com/Agenius-AI-Labs/ageniusnote-lite/commit/7dcb49a) | [v1.0.2](https://github.com/Agenius-AI-Labs/ageniusnote-lite/releases/tag/v1.0.2) |

Fix details for #1: `APP_VERSION` is now read at runtime from `packaging/VERSION` via a small helper that resolves both the dev path and the PyInstaller-frozen path (`sys._MEIPASS`). The packaging spec ships `packaging/VERSION` inside the bundle so the lookup succeeds in installed builds.

Open items (hardening, not vulnerabilities):

- SHA-pin third-party GitHub Actions in `release.yml` (`actions/checkout`, `actions/setup-python`, `actions/upload-artifact`, `softprops/action-gh-release`). Currently tag-pinned.
- Pin `numpy`, `pynput`, `sounddevice`, `soundfile` in `requirements.txt` for reproducible installer builds.
- No automated test suite. CONTRIBUTING.md flags this as a good first contribution.

These are noted as backlog, not blockers for the public OSS launch.

