#!/bin/bash

# Display usage information
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Configure, build, and submit the MPAS-Model."
    echo ""
    echo "Options:"
    echo "  --no-profile    Use the normal configuration without Scalasca profiling."
    echo "  -h, --help      Display this help message and exit."
    echo ""
}

# Default suffix is scalasca
SUFFIX="scalasca"

# Parse command line arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --no-profile)
            SUFFIX="normal"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: Unknown parameter passed: $1"
            usage
            exit 1
            ;;
    esac
done

echo "Setting up MPAS-Model with configuration suffix: _${SUFFIX}"

# 1. Setup modules for the build
cp modulefiles/mpas/ursa.intel.lua_${SUFFIX} MPAS-Model/modulefiles/mpas/ursa.intel.lua
module use MPAS-Model/modulefiles
module load mpas/ursa.intel

# 2. Build the model
cd MPAS-Model
rm -rf build
source ./build.sh

# 3. Setup run directory and submit
cd ../
cp 2026012600_60km/job.fcst_real_60km_${SUFFIX} 2026012600_60km/job.fcst_real_60km
cp modulefiles/ursa.intel.lua_${SUFFIX} 2026012600_60km/modulefiles/ursa.intel.lua

cd 2026012600_60km
sbatch job.fcst_real_60km
