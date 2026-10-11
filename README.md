# Compact animated badges — five seconds with sound

**[Download the latest badges as one ZIP](https://github.com/razig07/animations/archive/refs/heads/badge-downloads.zip)**

This download contains only the `downloads/` folder: eight ready-to-use ProRes 4444 MOVs, their matching transparent PNGs, and a lightweight MP4 preview. Unzip once and import the MOVs into Premiere Pro. No rendering, source installation, or extra badge ZIP extraction is required.

The pill widths now fit each word. All badges share the same font size and pill height, with consistent padding and centered content.

- Total duration: **5 seconds**, **60 fps**.
- Entrance: **0.00–0.40 seconds**.
- Hold: **0.40–4.00 seconds**.
- Exit: **4.00–5.00 seconds**.
- BRILLIANT, GREAT, BEST and GOOD play **Single UI Beep 12** once at the start.
- INACCURACY, MISTAKE, MISS and BLUNDER play **Single UI Beep 06** once at the start.

[Individual files](https://github.com/razig07/animations/tree/badge-downloads/downloads) · [MP4 preview](downloads/preview.mp4)

Use **Straight — Unmatted** alpha interpretation if Premiere asks. Each PNG matches its corresponding MOV's held position and canvas size. The preview has a dark background and plays a quiet mix of the two cues; use the MOVs for compositing and the assigned individual sound.

The editable renderer remains available in [render_compact.py](badge_overlays/source/render_compact.py), with [compact_settings.json](badge_overlays/source/compact_settings.json). To rerender, install the dependencies in [requirements.txt](badge_overlays/source/requirements.txt) and run:

```sh
python3 badge_overlays/source/render_compact.py
```

The earlier 15-second version remains in Git history. The download link above always points to the compact five-second version.
