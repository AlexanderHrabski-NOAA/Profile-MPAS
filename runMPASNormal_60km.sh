#!/bin/bash
cp /scratch3/NCEPDEV/global/${USER}/MPAS/MPAS-Model/modulefiles/mpas/ursa.intel.lua_normal /scratch3/NCEPDEV/global/${USER}/MPAS/MPAS-Model/modulefiles/mpas/ursa.intel.lua
rm -rf /scratch3/NCEPDEV/global/${USER}/MPAS/MPAS-Model/build
cd /scratch3/NCEPDEV/global/${USER}/MPAS/MPAS-Model
module use /scratch3/NCEPDEV/global/${USER}/MPAS/MPAS-Model/modulefiles
module load mpas/ursa.intel
cd /scratch3/NCEPDEV/global/${USER}/MPAS/MPAS-Model
source ./build.sh

cd /scratch3/NCEPDEV/global/${USER}/MPAS/2026012600_60km
cp /scratch3/NCEPDEV/global/${USER}/MPAS/2026012600_60km/job.fcst_real_60km_normal /scratch3/NCEPDEV/global/${USER}/MPAS/2026012600_60km/job.fcst_real_60km
cp /scratch3/NCEPDEV/global/${USER}/MPAS/2026012600_60km/modulefiles/ursa.intel.lua_normal /scratch3/NCEPDEV/global/${USER}/MPAS/2026012600_60km/modulefiles/ursa.intel.lua
sbatch job.fcst_real_60km
