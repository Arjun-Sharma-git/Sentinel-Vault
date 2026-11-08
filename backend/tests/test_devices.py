"""TORCH_DEVICE validation against stand-ins for the CPU and ROCm builds of PyTorch."""

from types import SimpleNamespace

import pytest

from app.devices import DeviceError, resolve_device


def fake_torch(*, hip=None, cuda=None, gpus=0, name="GPU", arch=""):
    props = SimpleNamespace(name=name, gcnArchName=arch)
    return SimpleNamespace(
        __version__="2.15.0",
        version=SimpleNamespace(hip=hip, cuda=cuda),
        cuda=SimpleNamespace(
            is_available=lambda: gpus > 0, device_count=lambda: gpus, get_device_properties=lambda i: props
        ),
    )


def test_cpu_needs_nothing():
    assert resolve_device("cpu").torch_device == "cpu"


def test_rx_9070_xt_on_rocm():
    torch = fake_torch(hip="7.14", gpus=1, name="AMD Radeon RX 9070 XT", arch="gfx1201:sramecc-:xnack-")
    device = resolve_device("ROCm", torch=torch)
    assert device.torch_device == "cuda"  # ROCm builds expose AMD GPUs through torch.cuda
    assert device.name == "AMD Radeon RX 9070 XT (gfx1201)"


def test_rocm_requested_with_cpu_build_explains_the_fix():
    with pytest.raises(DeviceError, match=r"cpu build.*--gpu rocm"):
        resolve_device("rocm", torch=fake_torch())


def test_rocm_build_without_a_visible_gpu():
    with pytest.raises(DeviceError, match="render and video"):
        resolve_device("rocm", torch=fake_torch(hip="7.14", gpus=0))


def test_unknown_device():
    with pytest.raises(DeviceError, match="cpu, rocm or cuda"):
        resolve_device("tpu", torch=fake_torch())
