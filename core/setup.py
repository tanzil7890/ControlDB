"""
Build script for the ControlDB C++ Python extension (_controldb_engine).

Usage:
    pip install pybind11 setuptools
    pip install -e .  --no-build-isolation

Or via CMake (preferred for full RocksDB + Raft build):
    cmake -B build -DBUILD_TESTS=ON && cmake --build build -j$(nproc)
    pip install build/  # installs the wheel produced by CMake

Environment:
    ROCKSDB_INCLUDE  — path to RocksDB headers (default: /usr/local/include)
    ROCKSDB_LIB      — path to librocksdb.a/.so (default: /usr/local/lib)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

try:
    import pybind11
    PYBIND11_INCLUDE = pybind11.get_include()
except ImportError:
    raise SystemExit("pybind11 not found. Run: pip install pybind11")

ROOT = Path(__file__).parent
INCLUDE = ROOT / "include"
SRC     = ROOT / "src"

ROCKSDB_INCLUDE = os.environ.get("ROCKSDB_INCLUDE", "/usr/local/include")
ROCKSDB_LIB     = os.environ.get("ROCKSDB_LIB",     "/usr/local/lib")

_sources = [
    str(SRC / "hash_chain.cpp"),
    str(SRC / "wal.cpp"),
    str(SRC / "event_store.cpp"),
    str(SRC / "raft" / "log.cpp"),
    str(SRC / "raft" / "node.cpp"),
    str(ROOT / "bindings" / "python_bindings.cpp"),
]

_ext = Extension(
    name="_controldb_engine",
    sources=_sources,
    include_dirs=[
        str(INCLUDE),
        PYBIND11_INCLUDE,
        ROCKSDB_INCLUDE,
    ],
    library_dirs=[ROCKSDB_LIB],
    libraries=["rocksdb", "ssl", "crypto", "z", "pthread"],
    extra_compile_args=[
        "-std=c++17",
        "-O2",
        "-DNDEBUG",
        "-fvisibility=hidden",
    ],
    extra_link_args=["-std=c++17"],
    language="c++",
)


class BuildExt(build_ext):
    """Custom build that injects compiler-specific flags."""

    def build_extensions(self) -> None:
        ct = self.compiler.compiler_type
        opts = []
        if ct == "unix":
            opts += ["-std=c++17", "-fvisibility=hidden"]
        elif ct == "msvc":
            opts += ["/std:c++17"]
        for ext in self.extensions:
            ext.extra_compile_args += opts
        super().build_extensions()


setup(
    name="controldb-engine",
    version="0.1.0",
    description="ControlDB C++ storage engine Python bindings",
    long_description=(ROOT.parent / "README.md").read_text(encoding="utf-8")
    if (ROOT.parent / "README.md").exists() else "",
    author="ControlDB Authors",
    license="Apache-2.0",
    python_requires=">=3.9",
    ext_modules=[_ext],
    cmdclass={"build_ext": BuildExt},
    zip_safe=False,
)
