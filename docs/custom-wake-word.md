# Training a custom wake word ("Hey Biggie")

Voice Notes uses [openWakeWord](https://github.com/dscripka/openWakeWord) for hotword detection. The built-in models cover `alexa`, `hey_jarvis`, `hey_mycroft`, and `hey_rhasspy`. For anything else you train your own model. openWakeWord's authors maintain a Colab notebook that does this end-to-end with synthetic training audio, so you don't need to record yourself thousands of times.

## What you'll end up with

A single `.onnx` file (e.g., `hey_biggie.onnx`). Drop it anywhere on disk, point Voice Notes at it in Settings, and it becomes your wake word.

## Steps

1. Open the automatic training notebook in Colab:
   https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb
   Click the "Open in Colab" badge at the top.

2. Sign in with a Google account. Switch the Colab runtime to GPU: `Runtime → Change runtime type → T4 GPU` (free tier is fine).

3. In the first config cell, set:
   - `target_phrase = ["hey biggie"]`
   - `model_name = "hey_biggie"`
   - Leave the other defaults; they're tuned for ~1–2 hours of total training time on a free T4.

4. Run all cells (`Runtime → Run all`). The notebook will:
   - Synthesize ~10k positive samples ("hey biggie" spoken by many TTS voices)
   - Pull ~10k negative samples from open speech corpora
   - Train two stages of the openWakeWord pipeline
   - Validate accuracy and produce ROC curves

5. When training finishes, the notebook saves `hey_biggie.onnx` to the Colab session. The last cell zips and offers it for download.

6. Download `hey_biggie.onnx` to a stable location on McNasty, e.g.:
   `M:\Code\Agenius-AI-Labs\apps\voice-notes-desktop\models\hey_biggie.onnx`

7. In Voice Notes:
   - Click the gear (bottom-left sidebar).
   - Active Listening → "Custom model file" → Browse → pick the `.onnx`.
   - Save. The listener restarts with the new model.

The status footer should read `Listening for 'hey_biggie'`.

## Tuning

If it false-triggers:
- Settings → Active Listening → Score threshold → bump from `0.50` to `0.60` or higher.
- Stricter threshold = fewer false positives, more missed triggers.

If it misses:
- Drop the threshold to `0.40`.
- Make sure your mic is the system default input. openWakeWord uses the default device.

If accuracy is bad regardless of threshold:
- The synthetic data didn't cover your voice well. Re-train with `n_positive_audio_samples_per_phrase` bumped to 20000 in the notebook, and add a few real recordings of yourself saying the phrase to the positive set (the notebook explains how).

## Privacy

Training runs in Colab on your Google account. The audio is synthetic + public-domain corpora; nothing of yours is uploaded. The resulting `.onnx` runs entirely locally; openWakeWord never phones home.
