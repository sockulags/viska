"""Bygger en fristående Viska-mapp med PyInstaller.

    uv run python build.py         # CPU-bygge: portabelt, funkar överallt
    uv run python build.py --gpu   # tar även med CUDA-biblioteken (~+1,2 GB)
    uv run python build.py --engine [--gpu]   # bara dikteringsmotorn (för Referat)

Resultat: dist/Viska/Viska.exe samt dist/Viska-<version>[-gpu].zip, eller
dist/viska-engine/viska-engine.exe samt dist/viska-engine-<version>[-gpu].zip.
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

# Motorbygget (viska-engine.exe) har inget fönster, ingen tray och ingen egen
# polering: Referat visar gränssnittet och polerar via sina egna leverantörer.
ENGINE_EXCLUDED = (
    "tkinter", "sv_ttk", "darkdetect", "pystray", "PIL",
    "llama_cpp", "openai_codex", "codex_cli_bin",
)

# Codex-SDK:n drar in en egen kopia av Codex-binären på 354 MB. Viska använder
# den Codex användaren redan har installerad (viska.codex.binary), så paketet
# ska inte med i bygget — annars fyrdubblas nedladdningen.
EXCLUDED_PACKAGES = ("codex_cli_bin",)


def make_icon() -> Path:
    from viska.app import _make_icon_image

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    ico = assets / "viska.ico"
    _make_icon_image().save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    return ico


# Det CTranslate2 behöver på Windows: cuBLAS (laddas vid namn) och CUDA-
# runtime. Windows-bygget av CTranslate2 4.8 använder inte cuDNN, som ensamt
# är nästan 900 MB; Referats transkriberingskomponent har aldrig haft den.
ENGINE_NVIDIA_LIBS = ("cublas", "cuda_runtime")


def copy_nvidia_dlls(dist: Path, only: tuple[str, ...] | None = None) -> None:
    # Speglar körningens uppslag (nvidia/*/bin).
    nvidia = Path(sysconfig.get_path("purelib")) / "nvidia"
    bin_dirs = sorted(nvidia.glob("*/bin")) if nvidia.is_dir() else []
    if only is not None:
        bin_dirs = [d for d in bin_dirs if d.parent.name in only]
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
    parser.add_argument(
        "--engine", action="store_true",
        help="bygg bara dikteringsmotorn (viska --engine) som konsolprogram",
    )
    args = parser.parse_args()
    if args.engine:
        build_engine(args.gpu)
        return

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
            # openai_codex importeras först när Codex-motorn används, så
            # beroendeanalysen hittar den aldrig på egen hand. Utan det här
            # blev "Codex" ett val som kraschade på ImportError i exe:n.
            "--collect-submodules", "openai_codex",
            *[arg for pkg in EXCLUDED_PACKAGES for arg in ("--exclude-module", pkg)],
            str(ROOT / "run_viska.py"),
        ],
        check=True,
        cwd=ROOT,
    )
    dist = ROOT / "dist" / "Viska"
    # llama.cpp:s CUDA-DLL:er är ~930 MB — de eftermonteras via
    # "Ladda ner polering" i stället för att blåsa upp universalpaketet.
    # Python-skalet (llama_cpp-paketet) följer med; motorn pekas ut i
    # körning via LLAMA_CPP_LIB_PATH.
    for package in EXCLUDED_PACKAGES:
        # --exclude-module stoppar importen men PyInstaller kan ändå ha
        # samlat in paketets datafiler; binären ska bort ur bygget.
        leftover = dist / "_internal" / package
        if leftover.is_dir():
            size_mb = sum(f.stat().st_size for f in leftover.rglob("*")) // 1_000_000
            shutil.rmtree(leftover)
            print(f"Städade bort {package} ({size_mb} MB) – Viska använder din egen Codex")
    llama_lib = dist / "_internal" / "llama_cpp" / "lib"
    if llama_lib.is_dir():
        size_mb = sum(f.stat().st_size for f in llama_lib.rglob("*")) // 1_000_000
        shutil.rmtree(llama_lib)
        print(f"Städade bort bundlad llama.cpp-motor ({size_mb} MB) – opt-in i appen")
    if args.gpu:
        copy_nvidia_dlls(dist)
    suffix = "-gpu" if args.gpu else ""
    zip_path = shutil.make_archive(
        str(ROOT / "dist" / f"Viska-{__version__}{suffix}"), "zip",
        root_dir=dist.parent, base_dir="Viska",
    )
    print(f"\nKlart: {dist}\\Viska.exe")
    print(f"Paket: {zip_path}")


def build_engine(gpu: bool) -> None:
    """viska-engine.exe: motorläget som eget program. Ett konsolprogram, för
    att stdin och stdout ska finnas; Referat startar det dolt."""
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--console",
            "--name", "viska-engine",
            "--collect-data", "faster_whisper",
            *[arg for pkg in ENGINE_EXCLUDED for arg in ("--exclude-module", pkg)],
            str(ROOT / "run_engine.py"),
        ],
        check=True,
        cwd=ROOT,
    )
    dist = ROOT / "dist" / "viska-engine"
    if gpu:
        copy_nvidia_dlls(dist, only=ENGINE_NVIDIA_LIBS)
    suffix = "-gpu" if gpu else ""
    zip_path = shutil.make_archive(
        str(ROOT / "dist" / f"viska-engine-{__version__}{suffix}"), "zip",
        root_dir=dist,
    )
    size_mb = sum(f.stat().st_size for f in dist.rglob("*")) // 1_000_000
    print(f"\nKlart: {dist / 'viska-engine.exe'} ({size_mb} MB)")
    print(f"Paket: {zip_path}")


if __name__ == "__main__":
    main()
