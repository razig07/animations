#!/usr/bin/env python3
"""Vector artwork -> supersampled straight RGBA -> ProRes 4444 with alpha.

No reference-image pixels are used. All motion affects one complete SVG group.
"""
import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
from xml.sax.saxutils import escape

import cairosvg
from PIL import Image, ImageFont
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen

HERE = Path(__file__).resolve().parent


def text_outline(text, font, letter_spacing=0):
    """Outline Inter using font metrics, including kerning, without OS fonts."""
    glyphs = font.getGlyphSet()
    cmap = font.getBestCmap()
    units = font['head'].unitsPerEm
    shaper = ImageFont.truetype(str(HERE / 'fonts/Inter-Bold.ttf'), units)
    svg_pen = SVGPathPen(glyphs)
    bounds_pen = BoundsPen(glyphs)
    for i, char in enumerate(text):
        # Pair adjustment belongs to the following glyph, as in font shaping.
        x = shaper.getlength(text[:i + 1]) - shaper.getlength(char) + i * letter_spacing
        name = cmap[ord(char)]
        glyphs[name].draw(TransformPen(svg_pen, (1, 0, 0, 1, x, 0)))
        glyphs[name].draw(TransformPen(bounds_pen, (1, 0, 0, 1, x, 0)))
    return svg_pen.getCommands(), bounds_pen.bounds


class Renderer:
    def __init__(self, settings):
        self.c = settings
        self.font = TTFont(HERE / 'fonts/Inter-Bold.ttf')
        self.units = self.font['head'].unitsPerEm
        self.cap = self.font['OS/2'].sCapHeight
        self.w = round((settings['pill_width'] + 2 * settings['padding_x']) * settings['size_scale'])
        self.h = round((settings['pill_height'] + 2 * settings['padding_y']) * settings['size_scale'])
        self.n = round(settings['duration'] * settings['fps'])
        assert self.n >= 3 and abs(self.n / settings['fps'] - settings['duration']) < 1e-8
        assert self.w % 2 == 0 and self.h % 2 == 0, 'Use a scale giving even canvas dimensions.'
        assert settings['entrance_seconds'] + settings['exit_seconds'] < settings['duration']
        assert settings['padding_y'] > settings['pill_height'] * max(settings['entrance_distance'], settings['exit_distance']) + settings['outline_width']
        self.groups = {b['id']: self.artwork(b) for b in settings['badges']}
        self.held = {}
        self.transitions = {}

    def artwork(self, badge):
        c = self.c
        x, y = c['padding_x'], c['padding_y']
        pw, ph = c['pill_width'], c['pill_height']
        cy = y + ph / 2
        # Identical left ink edge and cap-height baseline for every label.
        label_size = c['label_font_size'] * ph / 200
        ls = label_size / self.units
        label_path, lb = text_outline(badge['label'], self.font)
        lx = x + 250 * ph / 200 - lb[0] * ls
        baseline = cy + self.cap * ls / 2
        assert lx + lb[2] * ls < x + pw - 50 * ph / 200, 'Label exceeds safe right padding.'
        label = f'<path fill="{c["text"]}" transform="translate({lx},{baseline}) scale({ls}, {-ls})" d="{label_path}"/>'
        icon_cx = x + 123 * ph / 200
        symbol = badge['symbol']
        col = badge['color']
        icon_scale = ph / 200
        if symbol == '★':
            pts = []
            for i in range(10):
                angle = -math.pi / 2 + i * math.pi / 5
                radius = 59 if i % 2 == 0 else 25.7
                pts.append((radius * math.cos(angle), radius * math.sin(angle)))
            # Center the tight geometric bounds, not the star's circumcircle.
            middle_y = (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2
            points = ' '.join(f'{px:.5f},{py - middle_y:.5f}' for px, py in pts)
            shape = f'<polygon points="{points}"/>'
        elif symbol == '✓':
            # Flat-ended continuous vector mark, free of font fallback.
            shape = '<polygon points="-54,1 -36,-15 -15,8 39,-49 58,-31 -15,49"/>'
        elif symbol == '✕':
            shape = '<path d="M -34,-53 L 0,-19 L 34,-53 L 53,-34 L 19,0 L 53,34 L 34,53 L 0,19 L -34,53 L -53,34 L -19,0 L -53,-34 Z"/>'
        else:
            ss = c['symbol_font_size'] / self.units
            extra = (12 if symbol == '!!' else 4) / ss if len(symbol) > 1 else 0
            symbol_path, sb = text_outline(symbol, self.font, extra)
            dx = -(sb[0] + sb[2]) * ss / 2
            dy = (sb[1] + sb[3]) * ss / 2
            shape = f'<path transform="translate({dx},{dy}) scale({ss},{-ss})" d="{symbol_path}"/>'
        icon = f'<g fill="{col}" transform="translate({icon_cx},{cy}) scale({icon_scale})">{shape}</g>'
        pill = f'<rect x="{x}" y="{y}" width="{pw}" height="{ph}" rx="{ph/2}" fill="{c["fill"]}" stroke="{c["outline"]}" stroke-width="{c["outline_width"]}"/>'
        return pill + icon + label

    def state(self, frame):
        c = self.c
        t = frame / c['fps']
        if frame == 0 or frame == self.n - 1:
            return 0.0, 0.0, 1.0
        if t < c['entrance_seconds']:
            u = t / c['entrance_seconds']
            e = 1 - (1 - u) ** 3  # cubic ease-out
            return e, c['pill_height'] * c['entrance_distance'] * (1 - e), c['initial_scale'] + (1 - c['initial_scale']) * e
        exit_start = c['duration'] - c['exit_seconds']
        if t > exit_start:
            # The final visible sample is 1/fps before duration. Reach zero there.
            u = (t - exit_start) / ((self.n - 1) / c['fps'] - exit_start)
            e = min(1.0, max(0.0, u)) ** 3  # cubic ease-in
            return 1 - e, -c['pill_height'] * c['exit_distance'] * e, 1.0
        return 1.0, 0.0, 1.0

    def svg(self, badge, state=(1.0, 0.0, 1.0)):
        opacity, shift_y, scale = state
        c = self.c
        cw = c['pill_width'] + 2 * c['padding_x']
        ch = c['pill_height'] + 2 * c['padding_y']
        cx, cy = cw / 2, ch / 2
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {cw} {ch}" role="img" aria-label="{escape(badge["symbol"] + " " + badge["label"])}">'
                f'<title>{escape(badge["label"])}</title>'
                f'<g opacity="{opacity:.10f}" transform="translate({cx},{cy+shift_y:.10f}) scale({scale:.10f}) translate({-cx},{-cy})">'
                + self.groups[badge['id']] + '</g></svg>')

    def raster(self, badge, state):
        if state[0] == 0:
            return Image.new('RGBA', (self.w, self.h))
        ss = self.c['supersampling']
        # Apply group opacity after reduction. Otherwise Lanczos ringing can
        # overshoot a partially faded alpha edge and momentarily saturate it.
        # RGB and glyph edges stay stable while one alpha multiplier fades all
        # components together; SVG geometry still transforms as one group.
        opacity, shift_y, scale = state
        geometry = (1.0, shift_y, scale)
        data = cairosvg.svg2png(bytestring=self.svg(badge, geometry).encode(), output_width=self.w * ss, output_height=self.h * ss)
        # PNG is straight RGBA. Pillow's RGBA resize handles associated-alpha
        # internally and unassociates on output, avoiding dark matte fringes.
        im = Image.open(io.BytesIO(data)).convert('RGBA')
        im = im.resize((self.w, self.h), Image.Resampling.LANCZOS)
        if opacity != 1.0:
            im.putalpha(im.getchannel('A').point([round(a * opacity) for a in range(256)]))
        return im

    def frame(self, badge, frame):
        state = self.state(frame)
        bid = badge['id']
        if state == (1.0, 0.0, 1.0):
            if bid not in self.held:
                self.held[bid] = self.raster(badge, state)
            return self.held[bid]
        key = (bid, frame)
        if key not in self.transitions:
            self.transitions[key] = self.raster(badge, state)
        return self.transitions[key]


def encode_frames(path, width, height, fps, frames, codec):
    common = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s:v', f'{width}x{height}', '-r', str(fps), '-i', 'pipe:0', '-an']
    color = ['-vf', 'scale=in_range=pc:out_range=tv:out_color_matrix=bt709,format=' + ('yuva444p10le' if codec == 'prores' else 'yuv420p'), '-color_range', 'tv', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709']
    if codec == 'prores':
        output = ['-c:v', 'prores_ks', '-profile:v', '4', '-alpha_bits', '16', '-vendor', 'apl0', '-threads', '4']
    else:
        output = ['-c:v', 'libx264', '-crf', '20', '-preset', 'fast', '-threads', '4', '-movflags', '+faststart']
    proc = subprocess.Popen(common + color + output + [str(path)], stdin=subprocess.PIPE)
    try:
        for data in frames:
            proc.stdin.write(data)
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError(f'FFmpeg failed: {path}')
    except BaseException:
        proc.kill()
        proc.wait()
        raise


def preview_frame(r, frame):
    # Two columns, four rows, same reading order as the reference.
    canvas = Image.new('RGBA', (1600, 1000), r.c['preview_background'])
    pw = 756
    ph = round(pw * r.h / r.w)
    for i, b in enumerate(r.c['badges']):
        im = r.frame(b, frame).resize((pw, ph), Image.Resampling.LANCZOS)
        canvas.alpha_composite(im, (38 + (i % 2) * 768, 58 + (i // 2) * 232))
    return canvas


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--settings', type=Path, default=HERE / 'settings.json')
    p.add_argument('--output', type=Path, default=HERE.parent / 'deliverables')
    p.add_argument('--artwork-only', action='store_true')
    args = p.parse_args()
    for tool in ['ffmpeg', 'ffprobe']:
        if not shutil.which(tool):
            raise SystemExit(f'Missing {tool}; install FFmpeg with prores_ks support.')
    cfg = json.loads(args.settings.read_text())
    r = Renderer(cfg)
    out = args.output.resolve()
    for directory in ['mov', 'png', 'svg', 'preview', 'quality']:
        (out / directory).mkdir(parents=True, exist_ok=True)
    for b in cfg['badges']:
        (out / 'svg' / f'{b["id"]}.svg').write_text(r.svg(b))
        r.frame(b, round(cfg['entrance_seconds'] * cfg['fps'])).save(out / 'png' / f'{b["id"]}.png')
    preview_frame(r, round(cfg['entrance_seconds'] * cfg['fps'])).convert('RGB').save(out / 'preview' / 'all_badges_dark.png')
    # A light-background contact sheet is included for visual review.
    old_bg = cfg['preview_background']
    cfg['preview_background'] = '#FFFFFF'
    preview_frame(r, round(cfg['entrance_seconds'] * cfg['fps'])).convert('RGB').save(out / 'quality' / 'all_badges_light.png')
    cfg['preview_background'] = old_bg
    if args.artwork_only:
        print('Artwork exported.', flush=True)
        return
    for b in cfg['badges']:
        t0 = time.monotonic()
        held_bytes = r.frame(b, round(cfg['entrance_seconds'] * cfg['fps'])).tobytes()
        def frames():
            for i in range(r.n):
                yield held_bytes if r.state(i) == (1.0, 0.0, 1.0) else r.frame(b, i).tobytes()
        encode_frames(out / 'mov' / f'{b["id"]}.mov', r.w, r.h, cfg['fps'], frames(), 'prores')
        print(f'{b["label"]}: {r.n} frames, {r.w}x{r.h}, {time.monotonic()-t0:.1f}s', flush=True)
    held_preview = preview_frame(r, round(cfg['entrance_seconds'] * cfg['fps'])).tobytes()
    def preview_frames():
        for i in range(r.n):
            yield held_preview if r.state(i) == (1.0, 0.0, 1.0) else preview_frame(r, i).tobytes()
    encode_frames(out / 'preview' / 'all_badges_dark.mp4', 1600, 1000, cfg['fps'], preview_frames(), 'h264')
    manifest = {
        'canvas': [r.w, r.h], 'pill': [cfg['pill_width'], cfg['pill_height']],
        'fps': cfg['fps'], 'duration_seconds': cfg['duration'], 'frames': r.n,
        'alpha': 'straight/unassociated; ProRes 4444 alpha_bits=16',
        'font': 'Inter Bold, weight 700, optical size 32; SIL OFL 1.1',
        'audio': False, 'settings': cfg,
        'font_sha256': hashlib.sha256((HERE / 'fonts/Inter-Bold.ttf').read_bytes()).hexdigest(),
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('All exports complete.', flush=True)


if __name__ == '__main__':
    main()
