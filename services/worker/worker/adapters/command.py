"""Self-hosted model adapters that drive an upstream repository's inference entrypoint in a
subprocess. Running the upstream code as a separate process keeps its licence and dependency
set isolated from ours, lets us kill it on cancel/timeout, and frees VRAM fully between jobs.

The exact CLI of each upstream repo is configured via environment variables (the defaults below
are starting points that MUST be validated on a GPU node — see docs/MODEL_SETUP.md)."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path

from .base import AdapterError, Cancelled, Capabilities, GenerationRequest, GenerationResult, ProgressFn


def _gpu_info() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu",  # noqa: S607
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        name, total, used, util = [x.strip() for x in out.stdout.splitlines()[0].split(",")]
        return {"gpu": name, "vram_total_mb": int(total), "vram_used_mb": int(used), "util_pct": int(util)}
    except Exception:
        return {}


class CommandAdapter:
    """Generic subprocess adapter. `cmd_template` placeholders: {python} {repo} {ckpt} {src_video}
    {ref_images_dir} {ref_image} {prompt_file} {prompt_q} {out} {width} {height} {frames} {seed} {steps}."""

    def __init__(self, name: str, caps: Capabilities, repo_env: str, ckpt_env: str, cmd_env: str,
                 default_cmd: str, sec_per_output_sec: float, timeout_s: int = 1800):
        self.name = name
        self._caps = caps
        self.repo = Path(os.environ.get(repo_env, f"/models/{name}/repo"))
        self.ckpt = Path(os.environ.get(ckpt_env, f"/models/{name}/ckpt"))
        self.cmd_template = os.environ.get(cmd_env, default_cmd)
        self.sec_per_output_sec = sec_per_output_sec
        self.timeout_s = timeout_s
        self._proc: subprocess.Popen | None = None

    def capabilities(self) -> Capabilities:
        return self._caps

    def healthcheck(self) -> dict:
        info = _gpu_info()
        ok = self.repo.exists() and self.ckpt.exists() and bool(info)
        enough_vram = info.get("vram_total_mb", 0) >= self._caps.min_vram_gb * 1024
        return {"ok": ok and enough_vram, "repo": self.repo.exists(), "ckpt": self.ckpt.exists(), **info}

    def estimate(self, req: GenerationRequest) -> dict[str, float]:
        scale = (req.width * req.height) / (480 * 832)
        return {"gpu_seconds": req.duration_s * self.sec_per_output_sec * scale,
                "vram_gb": float(self._caps.min_vram_gb)}

    def _render_cmd(self, req: GenerationRequest) -> list[str]:
        refs = req.workdir / "refs"
        refs.mkdir(exist_ok=True)
        for i, p in enumerate(req.identity_images):
            target = refs / f"{i:02d}{p.suffix}"
            if not target.exists():
                target.symlink_to(p.resolve())
        prompt_file = req.workdir / "prompt.json"
        prompt_file.write_text(json.dumps({"prompt": req.prompt, "negative_prompt": req.negative_prompt}))
        fps = int(req.params.get("fps", 16))
        frames = int(req.duration_s * fps) // 4 * 4 + 1  # Wan-family models want 4n+1 frames
        values = {
            "python": os.environ.get("MODEL_PYTHON", "python"), "repo": str(self.repo), "ckpt": str(self.ckpt),
            "src_video": str(req.source_video or ""), "ref_images_dir": str(refs),
            "ref_image": str(req.identity_images[0]) if req.identity_images else "",
            "prompt_file": str(prompt_file), "prompt_q": shlex.quote(req.prompt), "out": str(req.workdir / "raw.mp4"),
            "width": req.width, "height": req.height, "frames": frames, "seed": req.seed or 42,
            "steps": int(req.params.get("steps", 8)),
        }
        return shlex.split(self.cmd_template.format(**values))

    def generate(self, req: GenerationRequest, progress: ProgressFn, cancel: threading.Event) -> GenerationResult:
        if self._caps.tasks and req.task not in self._caps.tasks:
            raise AdapterError("unsupported_task", f"{self.name} cannot do {req.task}", retryable=False)
        cmd = self._render_cmd(req)
        log = open(req.workdir / f"{self.name}.log", "wb")  # noqa: SIM115
        t0 = time.time()
        est = self.estimate(req)["gpu_seconds"] or 60
        self._proc = subprocess.Popen(cmd, cwd=self.repo, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603
        try:
            while self._proc.poll() is None:
                elapsed = time.time() - t0
                if cancel.is_set():
                    self._proc.kill()
                    raise Cancelled()
                if elapsed > self.timeout_s:
                    self._proc.kill()
                    raise AdapterError("timeout", f"{self.name} exceeded {self.timeout_s}s")
                progress(min(0.95, elapsed / est), "generating")
                time.sleep(1.0)
        finally:
            log.close()
        tail = (req.workdir / f"{self.name}.log").read_bytes()[-4000:].decode(errors="replace")
        if self._proc.returncode != 0:
            oom = "out of memory" in tail.lower() or "CUDA error" in tail
            raise AdapterError("oom" if oom else "model_error", tail[-400:], retryable=True)
        out = req.workdir / "raw.mp4"
        if not out.exists():
            raise AdapterError("no_output", f"{self.name} produced no video", retryable=True)
        gpu_s = time.time() - t0
        return GenerationResult(video_path=out, fps=float(req.params.get("fps", 16)), gpu_seconds=gpu_s,
                                metrics={"wall_s": gpu_s, **_gpu_info()})

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()


def dreamid_v() -> CommandAdapter:
    # Official repo: github.com/bytedance/DreamID-V (Apache-2.0 code; built on Wan2.1-1.3B).
    # MediaPipe face-detection variant is preferred: avoids non-commercial InsightFace weights.
    return CommandAdapter(
        name="dreamid_v",
        caps=Capabilities(model="dreamid_v", tasks=["face_swap_v2v"], max_duration_s=10,
                          resolutions=["480x832", "720x1280"], min_vram_gb=16, max_identities=1,
                          license="Apache-2.0 (code); weights: verify model card"),
        repo_env="DREAMIDV_REPO", ckpt_env="DREAMIDV_CKPT", cmd_env="DREAMIDV_CMD",
        default_cmd=("{python} inference.py --ckpt_dir {ckpt} --video {src_video} --ref_image {ref_image} "
                     "--output {out} --size {width}*{height} --frame_num {frames} --seed {seed}"),
        sec_per_output_sec=12.0,
    )


def wan22_animate_14b() -> CommandAdapter:
    # github.com/Wan-Video/Wan2.2 (Apache-2.0 code + weights). "replacement" mode swaps the
    # character in a driving video. Run with a Lightning/LightX2V 4-step LoRA + FP8 for cost.
    return CommandAdapter(
        name="wan22_animate_14b",
        caps=Capabilities(model="wan22_animate_14b", tasks=["face_swap_v2v", "character_replace"],
                          max_duration_s=10, resolutions=["480x832", "720x1280"], min_vram_gb=48,
                          max_identities=1, license="Apache-2.0"),
        repo_env="WAN22_REPO", ckpt_env="WAN22_ANIMATE_CKPT", cmd_env="WAN22_ANIMATE_CMD",
        default_cmd=("{python} generate.py --task animate-14B --ckpt_dir {ckpt} --src_root_path {src_video} "
                     "--refert_num 1 --replace_flag --use_relighting_lora --save_file {out}"),
        sec_per_output_sec=40.0,
    )


def wan22_ti2v_5b() -> CommandAdapter:
    return CommandAdapter(
        name="wan22_ti2v_5b",
        caps=Capabilities(model="wan22_ti2v_5b", tasks=["identity_i2v"], max_duration_s=5,
                          resolutions=["704x1280"], min_vram_gb=24, license="Apache-2.0"),
        repo_env="WAN22_REPO", ckpt_env="WAN22_TI2V_CKPT", cmd_env="WAN22_TI2V_CMD",
        default_cmd=("{python} generate.py --task ti2v-5B --size 704*1280 --ckpt_dir {ckpt} "
                     "--image {ref_image} --prompt {prompt_q} --save_file {out} --offload_model True "
                     "--convert_model_dtype --t5_cpu"),
        sec_per_output_sec=60.0,
    )
