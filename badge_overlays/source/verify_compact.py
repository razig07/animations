#!/usr/bin/env python3
"""Independent checks of compact five-second overlays; writes QA outside delivery."""
import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def command(args):
    return subprocess.check_output(args)


def probe(path):
    return json.loads(command(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def rgba_selected(path, width, height, indices):
    select = '+'.join(f'eq(n\\,{i})' for i in indices)
    data = command(['ffmpeg', '-v', 'error', '-threads', '2', '-i', str(path), '-map', '0:v:0', '-vf', f'select={select}', '-vsync', '0', '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1'])
    frames = np.frombuffer(data, dtype=np.uint8).reshape((-1, height, width, 4))
    assert len(frames) == len(indices), (path, len(frames))
    return dict(zip(indices, frames))


def bbox(mask):
    y, x = np.where(mask)
    return [int(x.min()), int(y.min()), int(x.max()), int(y.max())] if len(x) else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('/workspace/compact-downloads'))
    parser.add_argument('--output', type=Path, default=Path('/workspace/compact-qa'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((args.input / 'manifest.json').read_text())
    audio_evidence = json.loads((args.output / 'audio_prefix_verification.json').read_text())
    expected = [('brilliant', 'BRILLIANT', '!!'), ('great', 'GREAT', '!'), ('best', 'BEST', '★'), ('good', 'GOOD', '✓'), ('inaccuracy', 'INACCURACY', '?!'), ('mistake', 'MISTAKE', '?'), ('miss', 'MISS', '✕'), ('blunder', 'BLUNDER', '??')]
    indices = [0, 1, 6, 12, 23, 24, 60, 120, 239, 240, 250, 270, 285, 298, 299]
    report = {'checks': [], 'badges': {}, 'sampling': indices, 'visual_review': 'Decoded MOV frames at full opacity on light and dark contact sheets. Spelling and symbol appearance require inspecting the sheets.'}
    def check(name, ok, actual=None):
        report['checks'].append({'name': name, 'passed': bool(ok), 'actual': actual})
        if not ok:
            print(f'FAIL: {name}: {actual}', flush=True)
    check('manifest duration, fps, frames, entrance, exit', [manifest[k] for k in ['duration', 'fps', 'frames', 'entrance_seconds', 'exit_start', 'exit_seconds']] == [5, 60, 300, .4, 4, 1])
    held_images = []
    for bid, label, symbol in expected:
        info = manifest['badges'][bid]
        width, height = info['canvas']
        media = probe(args.input / f'{bid}.mov')
        streams = media['streams']
        videos = [s for s in streams if s['codec_type'] == 'video']
        audios = [s for s in streams if s['codec_type'] == 'audio']
        check(f'{bid}: one video plus one audio, no cover art', len(streams) == 2 and len(videos) == 1 and len(audios) == 1, len(streams))
        v, a = videos[0], audios[0]
        check(f'{bid}: 5 sec / 300 frames / 60fps / matching dimensions', v['nb_frames'] == '300' and v['avg_frame_rate'] == '60/1' and abs(float(v['duration']) - 5) < 1e-6 and [v['width'], v['height']] == [width, height] and height == 264, [v['nb_frames'], v['avg_frame_rate'], v['duration'], v['width'], v['height']])
        check(f'{bid}: ProRes4444 with alpha', v['codec_name'] == 'prores' and v['profile'] == '4444' and v['pix_fmt'].startswith('yuva444'), [v['codec_name'], v['profile'], v['pix_fmt']])
        check(f'{bid}: PCM24 stereo48k 5sec', a['codec_name'] == 'pcm_s24le' and a['sample_rate'] == '48000' and a['channels'] == 2 and abs(float(a['duration']) - 5) < 1e-6, [a['codec_name'], a['sample_rate'], a['channels'], a['duration']])
        evidence = audio_evidence[bid]
        check(f'{bid}: assigned source audio at sample zero, exact PCM and trailing silence', evidence['sound'] == info['audio'] and evidence['source_matches_exactly'] and evidence['remaining_audio_is_silent'] and evidence['audio_seconds'] == 5, evidence)
        check(f'{bid}: correct label/symbol metadata', info['label'] == label and info['symbol'] == symbol, [info['label'], info['symbol']])
        png = Image.open(args.input / f'{bid}.png').convert('RGBA')
        p = np.asarray(png)
        check(f'{bid}: PNG same canvas, straight RGBA', png.size == (width, height) and Image.open(args.input / f'{bid}.png').mode == 'RGBA', png.size)
        pf = p[..., 3]
        silhouette = bbox(pf > 128)
        cx, cy = (silhouette[0] + silhouette[2]) / 2, (silhouette[1] + silhouette[3]) / 2
        check(f'{bid}: held pill centered with 40px side /32px vertical padding', abs(cx - (width - 1) / 2) <= 1 and abs(cy - (height - 1) / 2) <= 1 and 38 <= silhouette[0] <= 41 and 30 <= silhouette[1] <= 33, silhouette)
        col = np.array([int(info['symbol_color'][i:i+2], 16) for i in [1, 3, 5]])
        rgb = p[..., :3].astype(np.int16)
        icon_mask = (np.abs(rgb - col).max(axis=2) <= 2) & (pf == 255)
        text_mask = (np.abs(rgb - [48, 48, 56]).max(axis=2) <= 2) & (pf == 255)
        check(f'{bid}: symbol and label colors present', icon_mask.any() and text_mask.any())
        # Include antialiased colored tips and dark glyph edges when measuring
        # visible ink: exact solid-color masks omit narrow star/cross vertices.
        saturation = rgb.max(axis=2) - rgb.min(axis=2)
        ib = bbox((saturation > 10) & (pf > 240))
        tb = bbox((rgb.max(axis=2) < 200) & (saturation < 10) & (pf > 240))
        gap = tb[0] - ib[2] - 1
        content_left, content_right = ib[0] - 40, width - 41 - tb[2]
        check(f'{bid}: content centered, compact padding and gap', abs(content_left - content_right) <= 2 and 55 <= content_left <= 59 and 30 <= gap <= 35, {'left': content_left, 'right': content_right, 'gap': gap})
        check(f'{bid}: label vertical center', abs((tb[1] + tb[3]) / 2 - (height - 1) / 2) <= 2, tb)
        frames = rgba_selected(args.input / f'{bid}.mov', width, height, indices)
        alpha = {i: f[..., 3] for i, f in frames.items()}
        check(f'{bid}: first and final frame fully transparent', not alpha[0].any() and not alpha[299].any())
        check(f'{bid}: held frames byte-identical including exit start', all(np.array_equal(frames[24], frames[i]) for i in [60, 120, 239, 240]))
        check(f'{bid}: alpha opaque in hold and varied at smooth edges', alpha[60].max() == 255 and len(np.unique(alpha[60])) > 20, len(np.unique(alpha[60])))
        check(f'{bid}: sampled transitions remain unclipped', all(not (a[0].any() or a[-1].any() or a[:, 0].any() or a[:, -1].any()) for a in alpha.values()))
        checksums = [int(alpha[i].sum()) for i in indices]
        check(f'{bid}: entrance alpha rises and exit alpha falls', checksums[:6] == sorted(checksums[:6]) and checksums[9:] == sorted(checksums[9:], reverse=True), checksums)
        check(f'{bid}: PNG silhouette matches held video', np.abs(alpha[60].astype(np.int16) - pf.astype(np.int16)).max() <= 1, int(np.abs(alpha[60].astype(np.int16) - pf.astype(np.int16)).max()))
        opaque = (pf == 255) & (frames[60][..., 3] == 255)
        color_diff = np.abs(frames[60][..., :3].astype(np.int16) - rgb)[opaque]
        check(f'{bid}: codec color fidelity', float(np.quantile(color_diff, .99)) <= 5, {'p99_channel_error': float(np.quantile(color_diff, .99))})
        held_images.append((label, Image.fromarray(frames[60], 'RGBA')))
        report['badges'][bid] = {'canvas': [width, height], 'pill_bounds': silhouette, 'symbol_bounds': ib, 'label_bounds': tb, 'padding': [content_left, content_right], 'symbol_to_label_gap': gap}
        print(f'{label}: checked {width}x{height}', flush=True)
    for background, name in [('#202028', 'decoded_dark.png'), ('#FFFFFF', 'decoded_light.png')]:
        sheet = Image.new('RGBA', (1080, 2520), background)
        draw = ImageDraw.Draw(sheet)
        for index, (label, frame) in enumerate(held_images):
            x = (1080 - frame.width) // 2
            sheet.alpha_composite(frame, (x, index * 310 + 32))
            draw.text((24, index * 310 + 12), label, fill='#A0A0A8' if background != '#FFFFFF' else '#707078')
        sheet.convert('RGB').save(args.output / name)
    preview = probe(args.input / 'preview.mp4')
    pv = [s for s in preview['streams'] if s['codec_type'] == 'video'][0]
    check('preview: h264 / 5 sec / 60fps / 300frames', pv['codec_name'] == 'h264' and pv['avg_frame_rate'] == '60/1' and pv['nb_frames'] == '300' and abs(float(pv['duration']) - 5) < 1e-6)
    report['total_checks'] = len(report['checks'])
    report['passed_checks'] = sum(c['passed'] for c in report['checks'])
    report['all_passed'] = report['total_checks'] == report['passed_checks']
    (args.output / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{report["passed_checks"]}/{report["total_checks"]} checks passed', flush=True)
    raise SystemExit(0 if report['all_passed'] else 1)


if __name__ == '__main__':
    main()
