#!/bin/bash

# Display usage information
usage() {
    echo "Usage: $0 [OPTIONS] <RUNNAME>"
    echo "Configure, build, and submit the MPAS-Model."
    echo ""
    echo "Arguments:"
    echo "  RUNNAME         (Required) The name of the run directory."
    echo ""
    echo "Options:"
    echo "  --no-profile    Use the normal configuration without Scalasca profiling."
    echo "  -h, --help      Display this help message and exit."
    echo ""
}

# Default suffix is scalasca
SUFFIX="scalasca"
RUNNAME=""

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
        -*)
            echo "Error: Unknown parameter passed: $1"
            usage
            exit 1
            ;;
        *)
            if [ -z "$RUNNAME" ]; then
                RUNNAME="$1"
            else
                echo "Error: Too many arguments provided: $1"
                usage
                exit 1
            fi
            shift
            ;;
    esac
done

# Ensure RUNNAME was provided
if [ -z "$RUNNAME" ]; then
    echo "Error: Missing required argument <RUNNAME>."
    usage
    exit 1
fi

echo "Setting up MPAS-Model with configuration suffix: _${SUFFIX}"
echo "Run directory: ${RUNNAME}"

# 1. Setup modules for the build
cp modulefiles/mpas/ursa.intel.lua_${SUFFIX} MPAS-Model/modulefiles/mpas/ursa.intel.lua
module use MPAS-Model/modulefiles
module load mpas/ursa.intel

# 2. Build the model
cd MPAS-Model
rm -rf build
source ./build.sh
cd ../

# 3. Setup run directory and submit
mkdir -p "${RUNNAME}"

ln -s fix/* ${RUNNAME}
ln -s config/mpas/* ${RUNNAME}
ln -s config/scalasca/* ${RUNNAME}
ln -s ic/* ${RUNNAME}
ln -s partition/* ${RUNNAME}
ln -s utils/module-setup.sh ${RUNNAME}
cp "job-cards/job.fcst_real_60km_${SUFFIX}" "${RUNNAME}/job.fcst_real_60km"
cp "modulefiles/ursa.intel.lua_${SUFFIX}" "${RUNNAME}/modulefiles/ursa.intel.lua"

cd "${RUNNAME}"
sbatch job.fcst_real_60km
