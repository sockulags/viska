"""Bygger en fristående Viska-mapp med PyInstaller.

    uv run python build.py         # CPU-bygge: portabelt, funkar överallt
    uv run python build.py --gpu   # tar även med CUDA-biblioteken (~+1,2 GB)

Resultat: dist/Viska/Viska.exe samt dist/Viska-<version>[-gpu].zip.
Modellen bundlas inte — den laddas ner från Hugging Face första gången
appen körs (kb-whisper-small ~0,5 GB på CPU, kb-whisper-large ~3 GB på GPU).
"""

import argparse
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from viska import __version__  # noqa: E402


def make_icon() -> Path:
    from viska.app import _make_icon_image

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    ico = assets / "viska.ico"
    _make_icon_image().save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    return ico


def copy_nvidia_dlls(dist: Path) -> None:
    # Speglar körningens uppslag (nvidia/*/bin): cuBLAS/cuDNN laddar i sin
    # tur cuda_nvrtc dynamiskt, så ALLA installerade nvidia-bin måste med.
    nvidia = Path(sysconfig.get_path("purelib")) / "nvidia"
    bin_dirs = sorted(nvidia.glob("*/bin")) if nvidia.is_dir() else []
    if not bin_dirs:
        sys.exit(
            f"Hittar inga DLL:er under {nvidia} – "
            "kör 'uv sync --extra gpu' före --gpu-bygget."
        )
    for src in bin_dirs:
        lib = src.parent.name
        shutil.copytree(src, dist / "_internal" / "nvidia" / lib / "bin")
        print(f"Kopierade {lib}-DLL:er")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--gpu", action="store_true", help="bunta med CUDA-bibliotek (cuBLAS/cuDNN)"
    )
    args = parser.parse_args()

    ico = make_icon()
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--windowed",
            "--name", "Viska",
            "--icon", str(ico),
            # faster-whisper har Silero-VAD-modellen som paketdata;
            # sv-ttk har temat som tcl-filer
            "--collect-data", "faster_whisper",
            "--collect-data", "sv_ttk",
            str(ROOT / "run_viska.py"),
        ],
        check=True,
        cwd=ROOT,
    )
    dist = ROOT / "dist" / "Viska"
    if args.gpu:
        copy_nvidia_dlls(dist)
    suffix = "-gpu" if args.gpu else ""
    zip_path = shutil.make_archive(
        str(ROOT / "dist" / f"Viska-{__version__}{suffix}"), "zip",
        root_dir=dist.parent, base_dir="Viska",
    )
    print(f"\nKlart: {dist}\\Viska.exe")
    print(f"Paket: {zip_path}")


if __name__ == "__main__":
    main()
