#!/bin/bash
set -e

# Change to the directory where this script is located (utils)
UTILS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$UTILS_DIR"

echo "=== Building GKlib ==="
if [ ! -d "GKlib" ]; then
    git clone https://github.com/KarypisLab/GKlib
fi
cd GKlib

# Configure, build, and install GKlib (shared=1 required for METIS shared=1)
make config shared=1 cc=icx prefix="$PWD/install"
make -j 8
make install

# Fix the lib/lib64 issue for Rocky Linux 9 / CMake behavior
cd install
if [ -d "lib64" ] && [ ! -d "lib" ]; then
    echo "Linking lib64 to lib..."
    ln -s lib64 lib
fi

# Return to the utils directory
cd "$UTILS_DIR"

echo "=== Building METIS ==="
if [ ! -d "METIS" ]; then
    git clone -b v5.2.1 https://github.com/KarypisLab/METIS.git
fi
cd METIS

# Configure, build, and install METIS
make config shared=1 cc=icx prefix="$PWD/install" gklib_path="$UTILS_DIR/GKlib/install"
make install -j 8

echo "=== Build Complete ==="
echo "gpmetis executable is located in: $UTILS_DIR/METIS/install/bin"
