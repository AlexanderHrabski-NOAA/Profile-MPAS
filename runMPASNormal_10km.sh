#!/bin/bash
cp /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/MPAS-Model/modulefiles/mpas/ursa.intel.lua_normal /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/MPAS-Model/modulefiles/mpas/ursa.intel.lua
rm -rf /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/MPAS-Model/build
cd /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/MPAS-Model
module use /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/MPAS-Model/modulefiles
module load mpas/ursa.intel
cd /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/MPAS-Model
source ./build.sh

cd /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/2026012600_10km
cp /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/2026012600_10km/job.fcst_real_10km_normal /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/2026012600_10km/job.fcst_real_10km
cp /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/2026012600_10km/modulefiles/ursa.intel.lua_normal /scratch3/NCEPDEV/global/Georgios.Britzolakis/MPAS/2026012600_10km/modulefiles/ursa.intel.lua
sbatch job.fcst_real_10km

