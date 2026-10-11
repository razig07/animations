#!/usr/bin/env python3
"""Render compact 5-second ProRes 4444 overlays with the supplied UI beeps."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import cairosvg
from PIL import Image
from fontTools.ttLib import TTFont

from render import HERE, Renderer, text_outline

POSITIVE = {'brilliant', 'great', 'best', 'good'}


def compact_config(settings, badge):
    c = dict(settings)
    font = TTFont(HERE / 'fonts/Inter-Bold.ttf')
    units = font['head'].unitsPerEm
    label_size = c['label_font_size'] * c['pill_height'] / 200
    _, bounds = text_outline(badge['label'], font)
    label_width = (bounds[2] - bounds[0]) * label_size / units
    symbol = badge['symbol']
    if symbol == '★':
        min_x = -59 * math.sin(math.pi * 2 / 5)
        max_x = -min_x
    elif symbol == '✓':
        min_x, max_x = -54, 58
    elif symbol == '✕':
        min_x, max_x = -53, 53
    else:
        ss = c['symbol_font_size'] / units
        extra = (12 if symbol == '!!' else 4) / ss if len(symbol) > 1 else 0
        _, sb = text_outline(symbol, font, extra)
        max_x = (sb[2] - sb[0]) * ss / 2
        min_x = -max_x
    factor = c['pill_height'] / 200
    min_x, max_x = min_x * factor, max_x * factor
    side = c['content_padding'] * factor
    gap = c['symbol_label_gap'] * factor
    c['icon_offset'] = side - min_x
    c['label_offset'] = side + max_x - min_x + gap
    c['pill_width'] = math.ceil((c['label_offset'] + label_width + side) / 2) * 2
    # Share fractional rounding equally so the visible content stays centered.
    spare = c['pill_width'] - (c['label_offset'] + label_width + side)
    c['icon_offset'] += spare / 2
    c['label_offset'] += spare / 2
    c['badges'] = [badge]
    c['layout'] = {'label_width': label_width, 'symbol_width': max_x - min_x,
                   'content_padding': side + spare / 2, 'symbol_label_gap': gap}
    return c


class CompactRenderer(Renderer):
    """Rasterize vector art once at 3×; move the completed badge as one group."""
    def __init__(self, settings):
        super().__init__(settings)
        self.large = None

    def raster(self, badge, state):
        opacity, shift_y, scale = state
        if opacity == 0:
            return Image.new('RGBA', (self.w, self.h))
        ss = self.c['supersampling']
        if self.large is None:
            data = cairosvg.svg2png(bytestring=self.svg(badge).encode(),
                                   output_width=self.w * ss, output_height=self.h * ss)
            self.large = Image.open(io.BytesIO(data)).convert('RGBA')
        im = self.large
        if shift_y or scale != 1:
            cx, cy = im.width / 2, im.height / 2
            shift = shift_y * self.c['size_scale'] * ss
            # Pillow uses an inverse affine transform. RGBA bicubic resampling
            # uses associated-alpha internally to preserve clean transparent edges.
            im = im.transform(im.size, Image.Transform.AFFINE,
                              (1 / scale, 0, cx - cx / scale,
                               0, 1 / scale, cy - (cy + shift) / scale),
                              Image.Resampling.BICUBIC)
        im = im.resize((self.w, self.h), Image.Resampling.LANCZOS)
        if opacity != 1:
            im.putalpha(im.getchannel('A').point([round(a * opacity) for a in range(256)]))
        return im


def encode(path, width, height, fps, frames, prores=True):
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
           '-pix_fmt', 'rgba', '-s:v', f'{width}x{height}', '-r', str(fps),
           '-i', 'pipe:0', '-an', '-vf',
           'scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=' +
           ('yuva444p10le' if prores else 'yuv420p'),
           '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
           '-color_range', 'tv', '-threads', '2']
    if prores:
        cmd += ['-c:v', 'prores_ks', '-profile:v', '4', '-alpha_bits', '16', '-vendor', 'apl0']
    else:
        cmd += ['-c:v', 'libx264', '-crf', '20', '-preset', 'fast', '-movflags', '+faststart']
    proc = subprocess.Popen(cmd + [str(path)], stdin=subprocess.PIPE)
    try:
        for frame in frames:
            proc.stdin.write(frame)
        proc.stdin.close()
        if proc.wait():
            raise RuntimeError(f'FFmpeg failed: {path}')
    except BaseException:
        proc.kill()
        proc.wait()
        raise


def add_sound(video, sound, target, duration):
    # The source WAVs have cover-art video streams: select audio explicitly.
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                    '-i', str(video), '-i', str(sound), '-map', '0:v:0', '-map', '1:a:0',
                    '-map_metadata', '-1', '-c:v', 'copy', '-c:a', 'pcm_s24le',
                    '-ar', '48000', '-ac', '2', '-af', f'apad=whole_dur={duration}',
                    '-t', str(duration), str(target)], check=True)


def preview_frame(renderers, badges, i, background):
    sheet = Image.new('RGBA', (1600, 1000), background)
    for index, b in enumerate(badges):
        r = renderers[b['id']]
        im = r.frame(b, i)
        # Composite before reduction so preview opacity cannot overshoot.
        bg = Image.new('RGBA', im.size, background)
        bg.alpha_composite(im)
        im = bg.resize((round(r.w * .7), round(r.h * .7)), Image.Resampling.LANCZOS)
        center_x = 400 + (index % 2) * 800
        center_y = 150 + (index // 2) * 232
        sheet.alpha_composite(im, (center_x - im.width // 2, center_y - im.height // 2))
    return sheet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, default=HERE / 'compact_settings.json')
    parser.add_argument('--output', type=Path, default=HERE.parent.parent / 'downloads')
    parser.add_argument('--artwork-only', action='store_true')
    args = parser.parse_args()
    cfg = json.loads(args.settings.read_text())
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    sounds = {'positive': HERE / 'audio/Single UI Beep 12.wav',
              'negative': HERE / 'audio/Single UI Beep 06.wav'}
    for sound in sounds.values():
        if not sound.is_file():
            raise SystemExit(f'Missing supplied sound: {sound}')
    badges = cfg['badges']
    rs = {b['id']: CompactRenderer(compact_config(cfg, b)) for b in badges}
    metadata = {}
    for b in badges:
        bid = b['id']
        r = rs[bid]
        im = r.frame(b, round(cfg['entrance_seconds'] * cfg['fps']))
        im.save(out / f'{bid}.png')
        metadata[bid] = {'label': b['label'], 'symbol': b['symbol'], 'symbol_color': b['color'],
                         'canvas': [r.w, r.h], 'pill': [r.c['pill_width'], r.c['pill_height']],
                         'layout': r.c['layout'], 'icon_offset': r.c['icon_offset'],
                         'label_offset': r.c['label_offset'],
                         'audio': sounds['positive' if bid in POSITIVE else 'negative'].name}
    manifest = {'duration': cfg['duration'], 'fps': cfg['fps'], 'frames': next(iter(rs.values())).n,
                'entrance_seconds': cfg['entrance_seconds'], 'exit_start': cfg['duration'] - cfg['exit_seconds'],
                'exit_seconds': cfg['exit_seconds'], 'badges': metadata}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    qa = out.parent / 'compact-qa'
    qa.mkdir(exist_ok=True)
    hold = round(cfg['entrance_seconds'] * cfg['fps'])
    preview_frame(rs, badges, hold, cfg['preview_background']).convert('RGB').save(qa / 'artwork_dark.png')
    preview_frame(rs, badges, hold, '#FFFFFF').convert('RGB').save(qa / 'artwork_light.png')
    if args.artwork_only:
        print('Compact artwork ready.', flush=True)
        return
    with tempfile.TemporaryDirectory(prefix='compact-render-', dir=str(out.parent)) as scratch:
        temp = Path(scratch)
        def render_one(b):
            begin = time.monotonic()
            bid = b['id']
            r = rs[bid]
            held = r.frame(b, hold).tobytes()
            frames = (held if r.state(i) == (1.0, 0.0, 1.0) else r.frame(b, i).tobytes() for i in range(r.n))
            silent = temp / f'{bid}.mov'
            encode(silent, r.w, r.h, cfg['fps'], frames)
            add_sound(silent, sounds['positive' if bid in POSITIVE else 'negative'], out / f'{bid}.mov', cfg['duration'])
            print(f'{b["label"]}: {r.w}x{r.h}, 5 seconds, sound added, {time.monotonic()-begin:.1f}s', flush=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(render_one, badges))
        r0 = next(iter(rs.values()))
        held = preview_frame(rs, badges, hold, cfg['preview_background']).tobytes()
        frames = (held if r0.state(i) == (1.0, 0.0, 1.0) else preview_frame(rs, badges, i, cfg['preview_background']).tobytes() for i in range(r0.n))
        silent_preview = temp / 'preview.mp4'
        encode(silent_preview, 1600, 1000, cfg['fps'], frames, prores=False)
        # Overview only: one copy of each cue at half volume; individual MOVs
        # contain their assigned original cue at its unchanged original level.
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                        '-i', str(silent_preview), '-i', str(sounds['positive']), '-i', str(sounds['negative']),
                        '-filter_complex', '[1:a:0]volume=0.5[a];[2:a:0]volume=0.5[b];[a][b]amix=inputs=2:normalize=0,apad=whole_dur=5[mix]',
                        '-map', '0:v:0', '-map', '[mix]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
                        '-t', str(cfg['duration']), '-movflags', '+faststart', str(out / 'preview.mp4')], check=True)
    (out / 'README.txt').write_text(
        'COMPACT BADGE OVERLAYS — 5 SECONDS\n\n'
        'Import the .mov files into Premiere Pro and place them above your video.\n'
        'Each MOV is ProRes 4444 with straight alpha, 60 fps, 5 seconds.\n'
        'Entrance: 0.00–0.40s. Hold: 0.40–4.00s. Exit: 4.00–5.00s.\n'
        'The sound plays once at the beginning, at its original level.\n'
        'BRILLIANT / GREAT / BEST / GOOD: Single UI Beep 12.\n'
        'INACCURACY / MISTAKE / MISS / BLUNDER: Single UI Beep 06.\n'
        'PNG files match each badge’s held position and transparent canvas.\n'
        'Pill widths fit each label; font size and pill height remain consistent.\n'
        'Use Straight — Unmatted if Premiere asks for alpha interpretation.\n'
        'preview.mp4 is a dark-background overview, with both cues mixed quietly.\n'
        'Use the MOVs for compositing and the correct individual sound.\n')
    print('All compact exports finished.', flush=True)


if __name__ == '__main__':
    main()
