# Security Policy

## Supported versions

AgeniusNote Lite gets security fixes on the latest released minor version. Older minor versions are not patched.

| Version | Supported |
|---|---|
| 1.0.x | yes |
| < 1.0 | no |

## Reporting a vulnerability

**Do not open a public GitHub issue for security reports.**

Use [GitHub Security Advisories](https://github.com/Agenius-AI-Labs/ageniusnote-lite/security/advisories/new) to file a private report. That sends the report directly to the maintainers, gives you a private thread to share details, and reserves a CVE if needed.

If you cannot use the GitHub form, email **security@ageniuslabs.com**.

Please include:

- A description of the issue and the affected version(s).
- Reproduction steps. A minimal proof-of-concept is appreciated.
- Your assessment of impact.
- Whether you would like credit in the advisory (we will not name you without permission).

## What to expect

- We aim to acknowledge new reports within **3 business days**.
- We aim to triage and confirm severity within **7 business days**.
- For confirmed issues we will agree a coordinated disclosure timeline. Patch release first, public advisory second.

## Scope

In scope:

- The installed `AgeniusNote Lite` desktop application.
- The published installers (`.exe`, `.dmg`).
- The source code in this repository.

Out of scope:

- Vulnerabilities in upstream dependencies (report those upstream; we will update once a fix lands).
- Issues that require an attacker to already have local code execution on the machine running AgeniusNote Lite.
- Issues that require disabling macOS Gatekeeper or Windows SmartScreen and then running an untrusted build.
- Reports about the absence of code-signing certificates. Code signing is a known gap on the v1.x line; see [ROADMAP.md](ROADMAP.md).
