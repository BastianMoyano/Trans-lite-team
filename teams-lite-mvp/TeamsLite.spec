from pathlib import Path

from PyInstaller.utils.hooks import collect_all


root = Path(SPECPATH)
datas = []
binaries = []
hiddenimports = []
# Only what the app runs: Parakeet (onnx_asr + onnxruntime) and audio capture (soundcard).
# The optional WeVisOne visual model stack (torch, transformers, ...) is left out: it was
# ~600 MB of the bundle and the feature is disabled. Re-add it here if it is ever enabled.
for package in (
    "huggingface_hub",
    "onnx_asr",
    "onnxruntime",
    "soundcard",
):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

analysis = Analysis(
    [str(root / "launcher.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "vllm", "triton",
        # Unused visual-model stack and heavy optional dependencies pulled in transitively.
        "torch", "torchvision", "torchaudio", "transformers", "accelerate", "wevisone_vl",
        "qwen_vl_utils", "safetensors", "tokenizers", "av", "scipy", "matplotlib",
        # onnxruntime tooling (graph optimizers, quantization); inference does not use it.
        "onnxruntime.transformers", "onnxruntime.quantization", "onnxruntime.tools",
    ],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="TeamsLite",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX is known to corrupt torch/onnxruntime DLLs
    console=False,
)
bundle = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,  # UPX is known to corrupt torch/onnxruntime DLLs
    name="TeamsLite",
)
