#!/usr/bin/env python3
"""Verify exported media and create review sheets from decoded ProRes frames.

Run after render.py completes. No source artwork is substituted for video in
the review sheets. A nonzero exit status means at least one check failed.
"""
import argparse
from datetime import datetime, timezone
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
EXPECTED_BADGES = [
    ('brilliant', 'BRILLIANT', '!!', '#A592DC'),
    ('great', 'GREAT', '!', '#3D7BFF'),
    ('best', 'BEST', '★', '#2FBF71'),
    ('good', 'GOOD', '✓', '#8FB573'),
    ('inaccuracy', 'INACCURACY', '?!', '#F2B233'),
    ('mistake', 'MISTAKE', '?', '#F07A2E'),
    ('miss', 'MISS', '✕', '#E2577F'),
    ('blunder', 'BLUNDER', '??', '#E0312F'),
]


def run(command):
    p = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors='replace').strip())
    return p.stdout


def probe(path):
    return json.loads(run([
        'ffprobe', '-v', 'error', '-count_frames', '-show_streams',
        '-show_format', '-of', 'json', str(path),
    ]))


def decode_selected(path, indices, width, height):
    """One decode pass per file, with only the requested frames in memory."""
    indices = sorted(set(indices))
    select = '+'.join(f'eq(n\\,{i})' for i in indices)
    filters = (f'select={select},'
               'scale=in_range=tv:out_range=pc:in_color_matrix=bt709,format=rgba')
    data = run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-threads', '2',
        '-i', str(path), '-vf', filters, '-fps_mode', 'passthrough',
        '-threads:v', '2', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1',
    ])
    stride = width * height * 4
    if len(data) != len(indices) * stride:
        raise RuntimeError(f'Decoded {len(data) // stride} selected frames; expected {len(indices)}.')
    frames = np.frombuffer(data, np.uint8).reshape(len(indices), height, width, 4)
    return dict(zip(indices, frames))


def bbox(alpha):
    ys, xs = np.nonzero(alpha)
    if not xs.size:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def rgb(hex_color):
    return np.array([int(hex_color[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.uint8)


def expected_alpha_max(frame, cfg, total):
    """Analytic group fade, independently checked against decoded alpha.

    The last encoded sample is at (N-1)/fps, so exit easing reaches zero at
    that sample rather than leaving a partially visible final video frame.
    """
    if frame == 0 or frame == total - 1:
        return 0
    t = frame / cfg['fps']
    if t < cfg['entrance_seconds']:
        u = t / cfg['entrance_seconds']
        opacity = 1 - (1 - u) ** 3
    elif t > cfg['duration'] - cfg['exit_seconds']:
        start = cfg['duration'] - cfg['exit_seconds']
        end = (total - 1) / cfg['fps']
        u = min(1.0, max(0.0, (t - start) / (end - start)))
        opacity = 1 - u ** 3
    else:
        opacity = 1.0
    return round(255 * opacity)


def composite(frame, background):
    foreground = Image.fromarray(frame, 'RGBA')
    image = Image.new('RGBA', foreground.size, background)
    image.alpha_composite(foreground)
    return image.convert('RGB')


def font(size):
    return ImageFont.truetype(str(HERE / 'fonts' / 'Inter-Bold.ttf'), size)


def contact_sheet(frames, cfg, background, target):
    sheet = Image.new('RGB', (1600, 1000), background)
    for i, b in enumerate(cfg['badges']):
        im = composite(frames[b['id']], background)
        h = round(756 * im.height / im.width)
        sheet.paste(im.resize((756, h), Image.Resampling.LANCZOS),
                    (38 + (i % 2) * 768, 58 + (i // 2) * 232))
    sheet.save(target)


def edge_sheet(frame, scale, target):
    # Three representative areas: pill edge, colored symbol, charcoal lettering.
    regions = [(24, 55, 130, 155), (108, 73, 210, 196), (280, 91, 385, 173)]
    names = ['Pill edge', 'Colored symbol', 'Lettering']
    sheet = Image.new('RGB', (1360, 1180), '#292930')
    draw = ImageDraw.Draw(sheet)
    draw.text((28, 16), 'Decoded ProRes 4444: 4× pixel detail', fill='white', font=font(26))
    for row, bg in enumerate(['#FFFFFF', '#17171C']):
        source = composite(frame, bg)
        y = 70 + row * 555
        for col, (region, name) in enumerate(zip(regions, names)):
            bounds = tuple(round(v * scale) for v in region)
            crop = source.crop(bounds)
            crop = crop.resize((crop.width * 4, crop.height * 4), Image.Resampling.NEAREST)
            x = 28 + col * 442
            draw.text((x, y), name + (' / light' if row == 0 else ' / dark'),
                      fill='white', font=font(20))
            sheet.paste(crop, (x + (424 - crop.width) // 2, y + 34 + (492 - crop.height) // 2))
    sheet.save(target)


def transition_sheet(frames, indices, fps, target):
    sheet = Image.new('RGB', (1680, 420), '#17171C')
    draw = ImageDraw.Draw(sheet)
    draw.text((24, 12), 'Decoded entrance and exit samples — BRILLIANT',
              fill='#F0F0F2', font=font(22))
    for i, index in enumerate(indices):
        frame = frames[index]
        x, y = 20 + (i % 4) * 415, 56 + (i // 4) * 177
        im = composite(frame, '#17171C')
        h = round(395 * im.height / im.width)
        sheet.paste(im.resize((395, h), Image.Resampling.LANCZOS), (x, y))
        draw.text((x, y + h + 8), f'{index / fps:.3f}s · alpha max {int(frame[:, :, 3].max())}/255',
                  fill='#D8D8DC', font=font(16))
    sheet.save(target)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--settings', type=Path, default=HERE / 'settings.json')
    p.add_argument('--output', type=Path, default=HERE.parent / 'deliverables')
    args = p.parse_args()
    cfg = json.loads(args.settings.read_text())
    out = args.output.resolve()
    quality = out / 'quality'
    quality.mkdir(parents=True, exist_ok=True)
    width = round((cfg['pill_width'] + 2 * cfg['padding_x']) * cfg['size_scale'])
    height = round((cfg['pill_height'] + 2 * cfg['padding_y']) * cfg['size_scale'])
    fps = cfg['fps']
    duration = cfg['duration']
    total = round(fps * duration)
    entrance_end = math.ceil(cfg['entrance_seconds'] * fps - 1e-9)
    exit_start = math.floor((duration - cfg['exit_seconds']) * fps + 1e-9)
    hold_a = entrance_end + (exit_start - entrance_end) // 4
    hold_b = entrance_end + 3 * (exit_start - entrance_end) // 4
    transition_indices = list(range(entrance_end + 1)) + list(range(exit_start, total))
    selected = sorted(set(transition_indices + [hold_a, hold_b]))
    report = {
        'verified_at_utc': datetime.now(timezone.utc).isoformat(),
        'expected': {'dimensions': [width, height], 'fps': fps, 'duration_seconds': duration,
                     'frames': total, 'codec': 'ProRes 4444', 'audio_streams': 0},
        'method': 'ffprobe full-frame counting; decoded RGBA samples; PNG and SVG metadata inspection',
        'checks': [], 'badges': {},
        'visual_review': {
            'automatic_checks_do_not_replace_visual_inspection': True,
            'sheets': ['decoded_all_badges_light.png', 'decoded_all_badges_dark.png',
                       'decoded_edges.png', 'decoded_transitions.png'],
        },
    }
    all_checks = []

    def check(items, name, passed, actual, expected=None):
        item = {'check': name, 'passed': bool(passed), 'actual': actual}
        if expected is not None:
            item['expected'] = expected
        items.append(item)
        all_checks.append(item)

    metadata = [(b['id'], b['label'], b['symbol'], b['color']) for b in cfg['badges']]
    check(report['checks'], 'badge_spellings_symbols_colors', metadata == EXPECTED_BADGES,
          metadata, EXPECTED_BADGES)
    for key, expected in [('fill', '#F0F0F2'), ('outline', '#D8D8DC'), ('text', '#303038')]:
        check(report['checks'], key + '_color', cfg[key] == expected, cfg[key], expected)
    check(report['checks'], 'pill_ratio', abs(cfg['pill_width'] / cfg['pill_height'] - 5) < 1e-9,
          cfg['pill_width'] / cfg['pill_height'], 5)
    held_frames, png_bboxes, label_bboxes, label_transforms = {}, {}, {}, {}
    brilliant_transitions = None
    for b in cfg['badges']:
        bid = b['id']
        result = {'label': b['label'], 'symbol': b['symbol'], 'symbol_color': b['color'], 'checks': []}
        report['badges'][bid] = result
        checks = result['checks']
        try:
            path = out / 'mov' / f'{bid}.mov'
            info = probe(path)
            streams = info.get('streams', [])
            videos = [s for s in streams if s['codec_type'] == 'video']
            audios = [s for s in streams if s['codec_type'] == 'audio']
            check(checks, 'one_video_no_audio', len(videos) == 1 and not audios,
                  {'video_streams': len(videos), 'audio_streams': len(audios)})
            if len(videos) != 1:
                raise RuntimeError('Expected one video stream.')
            stream = videos[0]
            actual_fps = float(Fraction(stream['avg_frame_rate']))
            actual_duration = float(stream.get('duration', info['format']['duration']))
            actual_frames = int(stream.get('nb_read_frames', -1))
            result['video'] = {k: stream.get(k) for k in ['codec_name', 'profile', 'codec_tag_string',
                                                        'pix_fmt', 'width', 'height', 'avg_frame_rate',
                                                        'duration', 'nb_frames', 'nb_read_frames',
                                                        'color_range', 'color_space']}
            check(checks, 'prores_4444_profile', stream.get('codec_name') == 'prores' and
                  stream.get('profile') == '4444' and stream.get('codec_tag_string') == 'ap4h',
                  {k: stream.get(k) for k in ['codec_name', 'profile', 'codec_tag_string']})
            check(checks, 'alpha_pixel_format', stream.get('pix_fmt', '').startswith('yuva444p'),
                  stream.get('pix_fmt'))
            check(checks, 'dimensions', [stream['width'], stream['height']] == [width, height],
                  [stream['width'], stream['height']], [width, height])
            check(checks, 'frame_rate', abs(actual_fps - fps) < 1e-9, actual_fps, fps)
            check(checks, 'decoded_frame_count', actual_frames == total, actual_frames, total)
            check(checks, 'duration', abs(actual_duration - duration) < 1e-6, actual_duration, duration)
            frames = decode_selected(path, selected, width, height)
            first, last = frames[0][:, :, 3], frames[total - 1][:, :, 3]
            held = frames[hold_a]
            check(checks, 'fully_transparent_start_end', not np.any(first) and not np.any(last),
                  {'first_alpha_max': int(first.max()), 'last_alpha_max': int(last.max())},
                  {'first_alpha_max': 0, 'last_alpha_max': 0})
            check(checks, 'held_alpha_contains_transparency_and_opacity',
                  held[:, :, 3].min() == 0 and held[:, :, 3].max() == 255,
                  {'min': int(held[:, :, 3].min()), 'max': int(held[:, :, 3].max())},
                  {'min': 0, 'max': 255})
            check(checks, 'held_frames_byte_identical', np.array_equal(held, frames[hold_b]),
                  {'frames_compared': [hold_a, hold_b], 'byte_identical': bool(np.array_equal(held, frames[hold_b]))})
            for endpoint in [entrance_end, exit_start]:
                check(checks, f'held_endpoint_frame_{endpoint}', np.array_equal(held, frames[endpoint]),
                      {'frame': endpoint, 'byte_identical': bool(np.array_equal(held, frames[endpoint]))})
            png_path = out / 'png' / f'{bid}.png'
            with Image.open(png_path) as image:
                png_mode = image.mode
                png_size = list(image.size)
                png = np.array(image.convert('RGBA'))
            check(checks, 'png_rgba_same_canvas', png_mode == 'RGBA' and png_size == [width, height],
                  {'mode': png_mode, 'dimensions': png_size})
            check(checks, 'png_alpha_contains_transparency_and_opacity',
                  png[:, :, 3].min() == 0 and png[:, :, 3].max() == 255,
                  {'min': int(png[:, :, 3].min()), 'max': int(png[:, :, 3].max())})
            png_bboxes[bid] = bbox(png[:, :, 3])
            png_alpha_diff = np.abs(held[:, :, 3].astype(np.int16) - png[:, :, 3].astype(np.int16))
            check(checks, 'png_video_alpha_match', png_alpha_diff.max() <= 1,
                  {'max_8bit_alpha_difference': int(png_alpha_diff.max())}, {'max_allowed': 1})
            check(checks, 'png_video_same_held_bbox', bbox(held[:, :, 3]) == png_bboxes[bid],
                  {'video': bbox(held[:, :, 3]), 'png': png_bboxes[bid]})
            color_results = {}
            for name, color in [('fill', cfg['fill']), ('outline', cfg['outline']),
                                ('text', cfg['text']), ('symbol', b['color'])]:
                mask = np.all(png[:, :, :3] == rgb(color), axis=2) & (png[:, :, 3] == 255)
                # Require a uniform 3×3 source neighborhood; keep edge DCT error
                # out of the interior-color comparison, while reporting its tolerance.
                safe = mask.copy()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        safe &= np.roll(np.roll(mask, dy, axis=0), dx, axis=1)
                diff = np.abs(held[:, :, :3].astype(np.int16) - png[:, :, :3].astype(np.int16))[safe]
                if diff.size:
                    color_results[name] = {'pixels': int(safe.sum()), 'mean_channel_error': float(diff.mean()),
                                           'p99_channel_error': float(np.percentile(diff, 99)),
                                           'max_channel_error': int(diff.max())}
                else:
                    color_results[name] = {'pixels': 0}
                # Thin 2px outline need not contain a complete 3×3 interior.
                required = name != 'outline'
                color_ok = bool(diff.size and diff.mean() <= 2 and np.percentile(diff, 99) <= 8)
                check(checks, name + '_interior_rgb', color_ok or not required,
                      color_results[name], {'mean_channel_error_max': 2, 'p99_channel_error_max': 8,
                                            'interior_required': required})
            result['interior_colors'] = color_results
            text_mask = np.all(png[:, :, :3] == rgb(cfg['text']), axis=2) & (png[:, :, 3] == 255)
            label_bboxes[bid] = bbox(text_mask)
            svg = ET.parse(out / 'svg' / f'{bid}.svg').getroot()
            title = svg.find('{http://www.w3.org/2000/svg}title')
            group = svg.find('{http://www.w3.org/2000/svg}g')
            label_path = group.find('{http://www.w3.org/2000/svg}path')
            transform = label_path.attrib['transform']
            # The x translation compensates each first glyph's sidebearing.
            # The baseline and scale must be identical for every label.
            label_transforms[bid] = transform.split(',', 1)[1]
            check(checks, 'editable_svg_label_symbol_metadata',
                  svg.attrib.get('aria-label') == b['symbol'] + ' ' + b['label'] and
                  title is not None and title.text == b['label'],
                  {'aria_label': svg.attrib.get('aria-label'), 'title': None if title is None else title.text})
            samples = []
            clipping = []
            alpha_curve_errors = []
            for index in transition_indices:
                alpha = frames[index][:, :, 3]
                bounds = bbox(alpha)
                expected_alpha = expected_alpha_max(index, cfg, total)
                alpha_error = abs(int(alpha.max()) - expected_alpha)
                alpha_curve_errors.append(alpha_error)
                if bounds and not (bounds[0] > 0 and bounds[1] > 0 and bounds[2] < width and bounds[3] < height):
                    clipping.append(index)
                samples.append({'frame': index, 'time_seconds': index / fps,
                                'alpha_max': int(alpha.max()), 'expected_alpha_max': expected_alpha,
                                'alpha_curve_error': alpha_error, 'bbox': bounds})
            result['transition_samples'] = samples
            check(checks, 'all_entrance_exit_samples_unclipped', not clipping,
                  {'sampled_frames': len(samples), 'frames_touching_canvas_edges': clipping})
            check(checks, 'fade_alpha_matches_cubic_curve', max(alpha_curve_errors) <= 1,
                  {'sampled_frames': len(samples), 'max_8bit_alpha_error': max(alpha_curve_errors),
                   'frames_exceeding_tolerance': [s['frame'] for s in samples if s['alpha_curve_error'] > 1]},
                  {'max_allowed_8bit_alpha_error': 1,
                   'entrance': '1-(1-t/entrance_seconds)^3',
                   'exit': '1-u^3, normalized to the last encoded frame',
                   'first_and_last_alpha': 0})
            entrance_alpha = [int(frames[i][:, :, 3].max()) for i in range(entrance_end + 1)]
            exit_alpha = [int(frames[i][:, :, 3].max()) for i in range(exit_start, total)]
            check(checks, 'monotonic_fade_in_out',
                  all(a <= z for a, z in zip(entrance_alpha, entrance_alpha[1:])) and
                  all(a >= z for a, z in zip(exit_alpha, exit_alpha[1:])),
                  {'entrance_alpha_maxima': entrance_alpha, 'exit_alpha_maxima': exit_alpha})
            held_frames[bid] = held.copy()
            if bid == 'brilliant':
                brilliant_transitions = {i: frames[i].copy() for i in selected}
            print(f'{b["label"]}: {sum(c["passed"] for c in checks)}/{len(checks)} checks passed', flush=True)
        except Exception as exc:
            check(checks, 'verification_completed', False, str(exc))
            print(f'{b["label"]}: ERROR: {exc}', flush=True)
    check(report['checks'], 'consistent_png_held_bboxes',
          len(png_bboxes) == len(cfg['badges']) and len({tuple(v) for v in png_bboxes.values()}) == 1,
          png_bboxes)
    label_alignments = {k: [v[0], v[1], v[3]] for k, v in label_bboxes.items() if v}
    spans = np.ptp(np.array(list(label_alignments.values())), axis=0).tolist() if label_alignments else None
    # Curved glyphs have optical overshoots; their ink bounds legitimately
    # differ by one or two pixels even with an identical baseline and font size.
    check(report['checks'], 'consistent_label_alignment_font_scale',
          len(label_alignments) == len(cfg['badges']) and len(set(label_transforms.values())) == 1
          and spans[0] <= math.ceil(cfg['size_scale'])
          and spans[1] <= math.ceil(2 * cfg['size_scale'])
          and spans[2] <= math.ceil(2 * cfg['size_scale']),
          {'ink_left_top_bottom': label_alignments, 'pixel_spans': spans,
           'baseline_scale_transforms': label_transforms},
          'Identical vector baseline/font scale; at most 1 px horizontal and 2 px optical overshoot at native size.')
    try:
        preview_info = probe(out / 'preview' / 'all_badges_dark.mp4')
        preview_streams = preview_info['streams']
        video = next(s for s in preview_streams if s['codec_type'] == 'video')
        preview_actual = {k: video.get(k) for k in ['codec_name', 'width', 'height', 'avg_frame_rate',
                                                  'duration', 'nb_read_frames']}
        preview_actual['audio_streams'] = sum(s['codec_type'] == 'audio' for s in preview_streams)
        preview_ok = (video['codec_name'] == 'h264' and [video['width'], video['height']] == [1600, 1000]
                      and abs(float(Fraction(video['avg_frame_rate'])) - fps) < 1e-9
                      and int(video.get('nb_read_frames', -1)) == total
                      and abs(float(video['duration']) - duration) < 1e-6
                      and preview_actual['audio_streams'] == 0)
        check(report['checks'], 'preview_video', preview_ok, preview_actual)
    except Exception as exc:
        check(report['checks'], 'preview_video', False, str(exc))
    if len(held_frames) == len(cfg['badges']):
        contact_sheet(held_frames, cfg, '#FFFFFF', quality / 'decoded_all_badges_light.png')
        contact_sheet(held_frames, cfg, cfg['preview_background'], quality / 'decoded_all_badges_dark.png')
        edge_sheet(held_frames['brilliant'], cfg['size_scale'], quality / 'decoded_edges.png')
    if brilliant_transitions is not None:
        exit_length = total - 1 - exit_start
        sample_indices = [0, max(1, entrance_end // 4), entrance_end // 2, entrance_end,
                          exit_start, exit_start + exit_length // 3,
                          exit_start + 2 * exit_length // 3, total - 1]
        transition_sheet(brilliant_transitions, sample_indices, fps, quality / 'decoded_transitions.png')
    report['passed_checks'] = sum(c['passed'] for c in all_checks)
    report['total_checks'] = len(all_checks)
    report['all_checks_passed'] = all(c['passed'] for c in all_checks)
    report['failed_checks'] = [c for c in all_checks if not c['passed']]
    (quality / 'verification.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(f'Verification: {report["passed_checks"]}/{len(all_checks)} checks passed. '
          f'Report: {quality / "verification.json"}', flush=True)
    return 0 if report['all_checks_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
