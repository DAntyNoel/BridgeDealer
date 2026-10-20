"""Build the vendored DDS C ABI with CMake and a local C++20 compiler."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(os.cpu_count() or 2, 8))
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if sys.platform not in ("darwin", "linux"):
        parser.error("Use macOS, Linux, or WSL2; native Windows is not supported by this wrapper")
    cmake = shutil.which("cmake")
    if not cmake:
        parser.exit(
            2, "CMake not found. Install CMake >= 3.16 and a C++20 compiler; see README.md.\n"
        )
    root = Path(__file__).resolve().parents[1]
    build = root / "build/dds"
    subprocess.run(
        [cmake, "-S", str(root), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"], check=True
    )
    subprocess.run([cmake, "--build", str(build), "--parallel", str(args.jobs)], check=True)
    name = "libdds.dylib" if sys.platform == "darwin" else "libdds.so"
    library = root / "lib" / name
    if not library.is_file():
        raise FileNotFoundError(library)
    print(f"DDS 3.1.0 built: {library}")


if __name__ == "__main__":
    main()
