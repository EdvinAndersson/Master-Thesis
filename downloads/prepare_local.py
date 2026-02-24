#!/usr/bin/env python3
import os
import json
import xml.etree.ElementTree as et
from argparse import ArgumentParser, Namespace
from subprocess import check_output
from typing import Dict, List, Any, Optional
from xml.etree.ElementTree import tostring


def run_cmd(cmd: str, verbose: bool = False) -> str:
    if verbose:
        print(f"> {cmd}")
    return check_output(cmd, shell=True).decode("utf-8")


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def rm(p: str) -> None:
    run_cmd(f'rm -rf "{p}" || true')


def get_attr(raw_xml: str, attr: str) -> str:
    return raw_xml.split(attr)[1].split('"')[1]


def transcode_h264_reps(
    src_mp4: str,
    out_dir: str,
    rates_kbps: List[int],
    fps: int,
    seg_s: int,
) -> Dict[int, str]:
    """
    Create representations as MP4 files: rep_<rate>.mp4
    Uses ffmpeg libx264 so it always works with MP4 input.

    IMPORTANT: We force keyframes exactly at segment boundaries so MP4Box can
    create stable segment durations.
    """
    ensure_dir(out_dir)
    out_map: Dict[int, str] = {}
    gop = fps * seg_s  # e.g., 24fps * 5s = 120

    for r in rates_kbps:
        out_mp4 = os.path.join(out_dir, f"rep_{r}.mp4")
        if os.path.isfile(out_mp4):
            out_map[r] = out_mp4
            continue

        cmd = (
            f'ffmpeg -y -i "{src_mp4}" '
            f'-an -c:v libx264 -preset slow '
            f'-r {fps} '
            f'-g {gop} -keyint_min {gop} -sc_threshold 0 '
            f'-b:v {r}k -maxrate {2*r}k -bufsize {4*r}k '
            f'"{out_mp4}"'
        )
        run_cmd(cmd, verbose=True)
        out_map[r] = out_mp4

    return out_map


def segment_rep(video_name: str, rep_mp4: str, rate: int, seg_s: int) -> Dict[int, Dict[str, float]]:
    """
    MP4Box DASH segment one rep into:
    videos/<name>/tracks/video_<rate>/{init.mp4,1.m4s,...} + intermediate_dash.mpd
    Returns per-segment info: {seg_no: {"start_time": float}}
    """
    segment_dir = f"videos/{video_name}/tracks/video_{rate}"
    rm(segment_dir)
    ensure_dir(segment_dir)

    # Work in a temp directory so we can move outputs cleanly
    tmp = f"videos/{video_name}/tmp/mp4box_{rate}"
    rm(tmp)
    ensure_dir(tmp)

    seg_ms = seg_s * 1000
    # MP4Box writes <basename>_dash.mpd, init.mp4, *.m4s into CWD
    run_cmd(
        f'cd "{tmp}" && MP4Box -dash {seg_ms} -dash-profile live -rap -segment-name "" "{os.path.abspath(rep_mp4)}"',
        verbose=True
    )

    # Move outputs (MPD name depends on input basename)
    run_cmd(f'mv "{tmp}"/*_dash.mpd "{segment_dir}/intermediate_dash.mpd"')
    run_cmd(f'mv "{tmp}/init.mp4" "{segment_dir}/init.mp4"')
    run_cmd(f'mv "{tmp}"/*.m4s "{segment_dir}/"')

    rm(tmp)

    # Segment timing info
    segments = int(run_cmd(f'ls "{segment_dir}" | grep -E "^[0-9]+\\.m4s$" | wc -l'))
    seg_info: Dict[int, Dict[str, float]] = {}
    for seg in range(1, segments + 1):
        raw = run_cmd(f'MP4Box -std -diso "{segment_dir}/{seg}.m4s" 2>&1 | grep "SegmentIndexBox"')
        timescale = int(get_attr(raw, "timescale"))
        earliest = int(get_attr(raw, "earliest_presentation_time"))
        seg_info[seg] = {"start_time": earliest / timescale}
    return seg_info


def build_common_manifest(video_name: str, rates: List[int]) -> Dict[int, Dict[str, int]]:
    """
    Merge intermediate_dash.mpd representations into videos/<name>/tracks/manifest.mpd
    Returns info per rate: {'width', 'height'}
    """
    tracks_dir = f"videos/{video_name}/tracks"
    base = None
    info: Dict[int, Dict[str, int]] = {}

    for rate in rates:
        segment_dir = f"{tracks_dir}/video_{rate}"
        manifest = f"{segment_dir}/intermediate_dash.mpd"

        # Remove "Initialization" lines (match your old script behavior)
        with open(manifest, "r") as f:
            content = f.read()
        content = "\n".join([l for l in content.split("\n") if "Initialization" not in l])
        with open(manifest, "w") as f:
            f.write(content)

        tree = et.parse(manifest).getroot()
        root = tree[1][0]

        representation = None
        for child in root:
            if "Representation" in child.tag:
                representation = child
        if representation is None:
            raise RuntimeError(f"Could not find Representation in {manifest}")

        pl = sorted(rates).index(rate)
        representation.set("id", f"video{pl}")

        info[rate] = {
            "width": int(representation.get("width")),
            "height": int(representation.get("height")),
        }

        if base is None:
            base = tree
            segment_template = tree[1][0][0]
            segment_template.set("initialization", "$RepresentationID$/init.mp4")
            segment_template.set("media", "$RepresentationID$/$Number$.m4s")
        else:
            base[1][0].append(representation)

    out_manifest = f"{tracks_dir}/manifest.mpd"
    rm(out_manifest)
    with open(out_manifest, "w") as f:
        f.write(tostring(base).decode("UTF-8"))

    return info


def convert_to_yuv(video_name: str, mp4_path: str, width: int, height: int, tag: str) -> str:
    """
    Convert mp4 to yuv420p scaled to width/height at videos/<name>/yuv/<tag>.yuv
    """
    ensure_dir(f"videos/{video_name}/yuv")
    out_yuv = f"videos/{video_name}/yuv/{tag}.yuv"
    run_cmd(
        f'ffmpeg -y -i "{mp4_path}" -pix_fmt yuv420p -vsync 0 -vf scale={width}:{height} "{out_yuv}"',
        verbose=True
    )
    return out_yuv


def extract_vmaf_score(v: Any) -> float:
    """
    Robustly extract a single VMAF score from various vmafossexec JSON formats.
    Your build did NOT contain v["aggregate"], so we handle multiple schemas.
    """
    # 1) Older common schema
    if isinstance(v, dict) and "aggregate" in v and isinstance(v["aggregate"], dict):
        agg = v["aggregate"]
        if "VMAF_score" in agg:
            return float(agg["VMAF_score"])
        if "VMAF" in agg:
            return float(agg["VMAF"])

    # 2) Newer schema
    if isinstance(v, dict) and "pooled_metrics" in v and isinstance(v["pooled_metrics"], dict):
        pm = v["pooled_metrics"]
        if "vmaf" in pm and isinstance(pm["vmaf"], dict):
            vv = pm["vmaf"]
            for k in ("mean", "harmonic_mean", "min", "max"):
                if k in vv:
                    return float(vv[k])

    # 3) Sometimes nested differently
    if isinstance(v, dict) and "metrics" in v and isinstance(v["metrics"], dict):
        m = v["metrics"]
        if "vmaf" in m and isinstance(m["vmaf"], dict) and "mean" in m["vmaf"]:
            return float(m["vmaf"]["mean"])

    # 4) Fall back: walk JSON
    def walk(x: Any) -> Optional[float]:
        if isinstance(x, dict):
            for k, val in x.items():
                lk = str(k).lower()
                if lk in ("vmaf_score", "vmaf") and isinstance(val, (int, float)):
                    return float(val)
                r = walk(val)
                if r is not None:
                    return r
        if isinstance(x, list):
            for it in x:
                r = walk(it)
                if r is not None:
                    return r
        return None

    r = walk(v)
    if r is not None:
        return float(r)

    raise KeyError(f"Could not find VMAF score in JSON. Top-level keys: {list(v.keys()) if isinstance(v, dict) else type(v)}")


def compute_vmaf(
    video_name: str,
    rates: List[int],
    reps: Dict[int, str],
    seg_info: Dict[int, Dict[int, Dict[str, float]]],
    info: Dict[int, Dict[str, int]],
) -> None:
    """
    Produces videos/<name>/vmaf.json with per-segment VMAF, using highest rate as reference.

    IMPORTANT:
    Your vmafossexec build expects:
      vmafossexec <fmt> <w> <h> <ref.yuv> <dist.yuv> <model.json> --log out.json --log-fmt json
    NOT: --json -o ...
    """
    out_path = f"videos/{video_name}/vmaf.json"
    if os.path.isfile(out_path):
        return

    vmaf_bin = "./deps/vmaf/libvmaf/build/tools/vmafossexec"
    if not os.path.isfile(vmaf_bin):
        raise RuntimeError(f"VMAF binary not found at {vmaf_bin}")

    vmaf_model = "./deps/vmaf/model/vmaf_v0.6.1.json"
    if not os.path.isfile(vmaf_model):
        raise RuntimeError(
            f"VMAF model not found at {vmaf_model}. "
            f"List models with: ls -la ./deps/vmaf/model"
        )

    biggest = max(rates)
    w = info[biggest]["width"]
    h = info[biggest]["height"]

    ref_yuv = convert_to_yuv(video_name, reps[biggest], w, h, f"video_{biggest}")

    for rate in rates:
        cur_yuv = ref_yuv if rate == biggest else convert_to_yuv(video_name, reps[rate], w, h, f"video_{rate}")

        segments = max(seg_info[rate].keys())
        for seg in range(1, segments + 1):
            ss = seg_info[rate][seg]["start_time"]
            if seg < segments:
                t = seg_info[rate][seg + 1]["start_time"] - ss
            else:
                t = seg_info[rate][seg]["start_time"] - seg_info[rate][seg - 1]["start_time"]

            # cut both (10 fps to speed up VMAF; keep consistent between ref/dist)
            run_cmd(
                f'ffmpeg -y -s:v {w}x{h} -r 10 -i "{cur_yuv}" -ss {ss} -t {t} -pix_fmt yuv420p cut1.yuv',
                verbose=True
            )
            run_cmd(
                f'ffmpeg -y -s:v {w}x{h} -r 10 -i "{ref_yuv}" -ss {ss} -t {t} -pix_fmt yuv420p cut2.yuv',
                verbose=True
            )

            # vmafossexec JSON logging (ref first, then distorted)
            run_cmd(
                f'{vmaf_bin} yuv420p {w} {h} cut2.yuv cut1.yuv "{vmaf_model}" '
                f'--log vmaf_out.json --log-fmt json'
            )

            v = json.loads(open("vmaf_out.json", "r").read())
            seg_info[rate][seg]["vmaf"] = extract_vmaf_score(v)

            run_cmd("rm -f cut1.yuv cut2.yuv vmaf_out.json || true")

    rm(f"videos/{video_name}/yuv")
    with open(out_path, "w") as f:
        f.write(json.dumps(seg_info))


def run(args: Namespace) -> None:
    video_name = args.video
    ensure_dir("videos")
    ensure_dir(f"videos/{video_name}/tmp")
    ensure_dir(f"videos/{video_name}/tracks")

    # 1) transcode reps (force GOP aligned with segment size)
    reps = transcode_h264_reps(
        args.input,
        f"videos/{video_name}/tmp",
        args.rates,
        fps=args.fps,
        seg_s=args.segment,
    )

    # 2) segment each rep
    seg_info_all: Dict[int, Dict[int, Dict[str, float]]] = {}
    for r in args.rates:
        seg_info_all[r] = segment_rep(video_name, reps[r], r, args.segment)

    # 3) build merged MPD
    info = build_common_manifest(video_name, args.rates)

    # 4) optional VMAF
    if args.vmaf:
        compute_vmaf(video_name, args.rates, reps, seg_info_all, info)

    print("\nDone.")
    print(f"Now run:\n  python3 export.py {video_name}\n")


if __name__ == "__main__":
    p = ArgumentParser(description="Prepare a LOCAL MP4 into videos/<name>/tracks + (optional) vmaf.json for export.py")
    p.add_argument("--input", required=True, help="Path to local input MP4")
    p.add_argument("video", help="Video name folder under ./videos/")
    p.add_argument("--segment", type=int, default=5, help="Segment length in seconds (default 5)")
    p.add_argument("--fps", type=int, default=24, help="Output FPS for encodes (default 24)")
    p.add_argument(
        "--rates",
        type=int,
        nargs="+",
        default=[300, 600, 1200, 2500],
        help="Bitrates (kbps) to generate (default: 300 600 1200 2500)",
    )
    p.add_argument("-vmaf", action="store_true", help="Generate videos/<name>/vmaf.json (slow)")
    run(p.parse_args())
