"""The PyTorch device for Laya and Docling (TORCH_DEVICE): cpu, rocm (AMD) or cuda (NVIDIA).

PyTorch's ROCm build drives AMD GPUs through its `torch.cuda` API, so "rocm" maps to the torch
device "cuda". The choice is verified once at startup: asking for a GPU that the installed
PyTorch build can't use stops the server with instructions, instead of silently using the CPU.
(Ollama picks its own GPU; this setting doesn't affect the answer or embedding models.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

INSTALL_HINT = {
    "rocm": "Install the ROCm build with `./run.sh --gpu rocm` (or `uv sync --no-group cpu --group rocm`).",
    "cuda": "Install a CUDA build of PyTorch (`uv sync --no-group cpu`).",
}


class DeviceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    requested: str  # cpu | rocm | cuda
    torch_device: str  # what torch is given: cpu | cuda
    name: str  # e.g. "AMD Radeon RX 9070 XT (gfx1201)"


def resolve_device(requested: str, torch: Any = None) -> Device:
    """Check TORCH_DEVICE against the installed PyTorch build and the GPUs it can see."""
    requested = requested.strip().lower()
    if requested == "cpu":
        return Device("cpu", "cpu", "cpu")
    if requested not in INSTALL_HINT:
        raise DeviceError(f"TORCH_DEVICE must be cpu, rocm or cuda, got {requested!r}")
    if torch is None:
        import torch  # heavy; only needed for GPUs

    build = (
        "rocm"
        if getattr(torch.version, "hip", None)
        else "cuda"
        if getattr(torch.version, "cuda", None)
        else "cpu"
    )
    if build != requested:
        raise DeviceError(
            f"TORCH_DEVICE={requested} but PyTorch {torch.__version__} is the {build} build. "
            f"{INSTALL_HINT[requested]}"
        )
    if not torch.cuda.is_available() or torch.cuda.device_count() == 0:
        raise DeviceError(
            f"TORCH_DEVICE={requested} but PyTorch sees no GPU. For AMD: check /dev/kfd "
            "exists and your user is in the render and video groups."
        )
    props = torch.cuda.get_device_properties(0)
    arch = getattr(props, "gcnArchName", "").split(":")[0]
    return Device(requested, "cuda", f"{props.name} ({arch})" if arch else props.name)
