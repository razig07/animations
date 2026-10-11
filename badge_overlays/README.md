# Animated badge overlays

Eight clean vector badges, rebuilt from shapes and Inter Bold outlines. Every badge uses the same 1,000 × 200 px pill, 1,080 × 264 px canvas, label size, baseline, symbol slot, padding and 2 px neutral outline. No raster reference pixels, backgrounds, effects, or audio are baked into the overlays.

## Delivered files

- `deliverables/mov/`: eight 15-second, 60 fps Apple ProRes 4444 MOVs with straight alpha. Import these into Premiere Pro and place them above your video.
- `deliverables/png/`: matching transparent held-state PNGs at the exact video canvas size and position.
- `deliverables/svg/`: resolution-independent outlined SVG masters.
- `deliverables/preview/all_badges_dark.mp4`: lightweight 15-second, 60 fps preview of all eight animations together. Its dark background is baked in; use the MOV files for compositing.
- `deliverables/preview/all_badges_dark.png` and `deliverables/quality/`: visual review images and export verification report.
- `source/`: Python renderer, editable JSON settings, bundled Inter font, font license, and dependency versions.

If Premiere asks for alpha interpretation, choose **Straight — Unmatted** under Modify → Interpret Footage. The transparent PNG and video can be swapped without repositioning. The pill itself is opaque during the hold; the canvas outside it is transparent.

## Run the renderer

Python 3.12 and FFmpeg 7.1.5 were used for these exports. FFmpeg must be installed and available on PATH, with the `prores_ks` and `libx264` encoders. CairoSVG also needs the Cairo system library. On Debian/Ubuntu install `ffmpeg libcairo2` using your normal package manager; on macOS `brew install ffmpeg cairo` provides them.

From the extracted package directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r source/requirements.txt
.venv/bin/python source/render.py
.venv/bin/python source/verify.py
```

On Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`. Rendering replaces matching files under `deliverables/`; use `--output /path/to/new-folder` to keep existing renders.

## Change size, duration or motion

Edit `source/settings.json`, then rerun the renderer:

- `fps`: frame rate, default **60**. Examples: 30, 60. Use a duration producing an integral number of frames.
- `duration`: total seconds, default **15.0**.
- `size_scale`: scales the entire canvas, pill, font, outline and motion together. `0.5` produces 540 × 132 px; `2.0` produces 2,160 × 528 px. Choose a value producing even canvas dimensions.
- `entrance_seconds`: entrance length, default **0.4**.
- `exit_seconds`: exit length, default **0.3**. Exit starts at `duration - exit_seconds`.
- `entrance_distance`: initial downward offset as a fraction of pill height, default **0.12**. The badge moves upward into its held position.
- `exit_distance`: upward exit movement as a fraction of pill height, default **0.06**.
- `initial_scale`: entrance scale, default **0.96**.
- `badges`: labels, symbol characters and symbol colours. Star, check and cross use explicit vector geometry. Punctuation and labels use the bundled font outlines. Shapes can be edited in `Renderer.artwork()`.

Do not reduce padding below the movement distance. The renderer checks padding and label fit. Keep all badges at the same settings to preserve consistent sizing and alignment. Use `--artwork-only` for fast SVG, PNG and contact-sheet updates.

The entrance uses cubic ease-out for opacity, translation and scale. The hold is an exact repeated frame, with no movement. The exit uses cubic ease-in for opacity and a small upward translation. Each transform applies to the whole badge. At 60 fps there are exactly 900 frames: frame 0 is fully transparent, frame 24 reaches the held state, frame 882 begins the exit, and frame 899 is fully transparent. Since a 15-second clip's final frame starts at 14.983333 seconds, exit interpolation ends at that last sample so no residual opacity remains. Clip duration remains exactly 15 seconds.

The raster stage uses 3× supersampling and alpha-aware Lanczos reduction. Group opacity is applied to the reduced alpha channel so edge ringing cannot overshoot the fade and glyph RGB remains stable. FFmpeg receives straight RGBA and writes ProRes profile 4444 with a 16-bit alpha payload. The vector-to-raster source is 8-bit per channel; ProRes's 10-bit colour and 16-bit alpha storage do not imply extra source precision. Colour conversion and MOV metadata use the Rec.709 matrix and limited YUV range.

## Font and reproducibility

Inter is bundled under SIL Open Font License 1.1 (`source/fonts/OFL.txt`). `Inter-Bold.ttf` is a static instance of the included variable font at weight 700 and optical size 32. Original font source: `https://github.com/google/fonts/tree/main/ofl/inter`. No font download is needed to render. Text is converted to SVG paths so output never depends on system fonts.

`source/verify.py` checks all MOV metadata, decoded first/last/held alpha, held-frame stability, canvas dimensions, no audio and padding through the transition. It also creates composites from decoded ProRes frames over dark and light backgrounds. See `deliverables/quality/verification.json` for the results from the delivered files.
