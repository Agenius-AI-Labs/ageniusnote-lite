# Contributing to AgeniusNote Lite

Thanks for the interest. AgeniusNote Lite is intentionally small, so contributions stay focused.

## What's in scope

- Bugs in dictation, hotkey handling, or installer behavior on Windows or macOS.
- Performance and startup-time improvements.
- Linux support (X11 paste path + AppImage).
- Improvements to the Cyberpunk theme.
- Better Wayland / Linux paste path.
- Better docs.

## What's out of scope

- Cloud sync, accounts, telemetry.
- Wake-word activation (lives in the larger `voice-notes-desktop` v3 product, not Lite).
- LLM post-processing of transcripts (also v3 territory).
- Anything that adds a network dependency to the dictation path.

If you have a feature in mind that crosses these lines, open an issue first to discuss before sinking time into a PR.

## Dev setup

```bash
git clone https://github.com/Agenius-AI-Labs/ageniusnote-lite.git
cd ageniusnote-lite
python3.11 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Dev run, no installer needed.
python voice_notes_lite.py
```

Python 3.11 is the supported floor. 3.12 and 3.13 should also work.

## Building installers locally

### Windows

Requires [Inno Setup 6](https://jrsoftware.org/isdl.php).

```powershell
./packaging/build.ps1
# Output: dist-build-<stamp>/installer/AgeniusNoteLite-Setup-<version>.exe
```

### macOS

Requires Xcode command-line tools and optionally `create-dmg` (`brew install create-dmg`).

```bash
./packaging/build.sh
# Output: dist-build-mac-<stamp>/AgeniusNoteLite-Setup-<version>-<arch>.dmg
```

Set `ARCH=arm64` or `ARCH=x86_64` to override the host arch label.

## Coding style

- Python 3.11+ syntax welcome.
- Match the existing module layout. `voice_notes_lite.py` is intentionally a single file to keep the bundle small and the diff against the v3 codebase legible.
- Prefer standard-library solutions over new dependencies. Every new dep adds to the installer size.
- No em-dashes in comments, docstrings, or commit messages. Use commas, periods, or rephrase.

## Pull request checklist

Before opening a PR:

- [ ] Code runs locally on at least one platform (Windows or macOS).
- [ ] `python -m py_compile voice_notes_lite.py` is clean.
- [ ] If you touched packaging, the installer builds and the resulting app launches.
- [ ] If you changed behavior or installer output, add a line to `CHANGELOG.md` under `[Unreleased]`.

Tests are TODO and a great first contribution. See the open issue tagged `good first issue` once we add one.

## Reporting bugs

Use the GitHub Issues tab. Include:

- OS and version (Windows 11 23H2, macOS 14.5, etc.)
- Mac arch if applicable (Apple Silicon vs Intel).
- Python version if running from source.
- The exact installer or release tag you're on (`v1.0.1` etc.).
- Steps to reproduce.
- Anything that comes out of the console / `build-lite.log`.

For security-shaped issues, see [SECURITY.md](SECURITY.md) instead of filing publicly.

## License

By contributing you agree your work is released under the MIT license (see [LICENSE](LICENSE)).
