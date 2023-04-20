#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Mar  1 09:43:05 2023

@author: jgiles
"""

# NEEDS WRADLIB 1.19 !! (OR GREATER?)

import wradlib as wrl
import numpy as np
import sys
import glob
import xarray as xr
import os
import datetime as dt
import pandas as pd
from tqdm.notebook import trange, tqdm

import warnings
warnings.filterwarnings('ignore')
import xradar as xd
import datatree as dttree

import netCDF4
import packaging

import time
start_time = time.time()

#%% Get encoding from a DWD file
dwd = xr.open_dataset("/automount/ags/jgiles/turkey_test/ras07-vol5minng01_sweeph5onem_allmoms_00-2017072700005800-pro-10392-hd5", group="sweep_0")
# display(dwd)

drop = ["szip", "zstd", "source", "chunksizes", "bzip2", "blosc", "shuffle", "fletcher32", "original_shape", "coordinates", "contiguous"]
dwd_enc = {k: {key: v.encoding[key] for key in v.encoding if key not in drop} for k, v in dwd.data_vars.items() if v.ndim == 3}
dwd_enc["PHIDP"] = dwd_enc["UPHIDP"]
dwd_enc["DBTH"] = dwd_enc["TH"]
dwd_enc["DBTV"] = dwd_enc["TV"]

#%% Import and set Dask stuff

import dask
from dask.distributed import Client
# not sure if this is needed
# client = Client(n_workers=8)
# client
from dask.diagnostics import ProgressBar

#%% Get files

# Get all files for one day
htypath = sorted(glob.glob("/automount/ags/jgiles/turkey_test/acq/OLDDATA/uza/RADAR/2017/07/27/HTY/RAW/*"))

# Create a dataframe to store the metadata of all files and then select it more easily

# Read attributes of files
radarid = []
dtime = []
taskname = []
elevation = []
nrays_expected = []
nrays_written = []
nbins = []
rlastbin = []
binlength = []
horbeamwidth = []
fpath = []

for f in htypath:
    print(".", end="")
    # Read metadata
    m = wrl.io.read_iris(f, loaddata=False, keep_old_sweep_data=True)
    # Extract info
    fname = os.path.basename(f).split(".")[0]
    radarid_ = fname[0:3]
    dtimestr = fname[3:]
    dtime_ = dt.datetime.strptime(dtimestr, "%y%m%d%H%M%S")
    taskname_ = m["product_hdr"]["product_configuration"]["task_name"].strip()
    nbins_ = m["nbins"]
    rlastbin_ = m["ingest_header"]["task_configuration"]["task_range_info"]["range_last_bin"]/100
    binlength_ = m["ingest_header"]["task_configuration"]["task_range_info"]["step_output_bins"]/100
    horbeamwidth_ = round(m["ingest_header"]["task_configuration"]["task_misc_info"]["horizontal_beam_width"], 2)
    for i in range(10):
        try:
            nrays_expected_ = m["data"][i]["ingest_data_hdrs"]["DB_DBZ"]["number_rays_file_expected"]
            nrays_written_ = m["data"][i]["ingest_data_hdrs"]["DB_DBZ"]["number_rays_file_written"]    
            elevation_ = round(m["data"][i]["ingest_data_hdrs"]["DB_DBZ"]["fixed_angle"], 2)
            break
        except KeyError:
            try:
                nrays_expected_ = m["data"][i]["ingest_data_hdrs"]["DB_DBZ2"]["number_rays_file_expected"]
                nrays_written_ = m["data"][i]["ingest_data_hdrs"]["DB_DBZ2"]["number_rays_file_written"]    
                elevation_ = round(m["data"][i]["ingest_data_hdrs"]["DB_DBZ2"]["fixed_angle"], 2)
                break
            except KeyError:
                continue
    # Append to list
    radarid.append(radarid_)
    dtime.append(dtime_)
    taskname.append(taskname_)
    elevation.append(elevation_)
    nbins.append(nbins_)
    rlastbin.append(rlastbin_)
    binlength.append(binlength_)
    #nrays_expected.append(nrays_expected_)
    #nrays_written.append(nrays_written_)
    fpath.append(f)
    horbeamwidth.append(horbeamwidth_)   

# put attributes in a dataframe
from collections import OrderedDict
df = pd.DataFrame(OrderedDict(
                  {"radarid": radarid,
                   "datetime": dtime,
                   "taskname": taskname,
                   "elevation": elevation,
                   #"nrays_expected": nrays_expected,
                   #"nrays_written": nrays_written,
                   "nbins": nbins,
                   "rlastbin": rlastbin,
                   "binlength": binlength,
                   "horbeamwidth": horbeamwidth,
                   "fpath": fpath                   
                  }))


# Let's open one scanning mode and one elevation (this will take some minutes to load)
mode = 'VOL_A'
elev = 0.

# Use the dataframe to get the paths that correspond to our selection
paths = df["fpath"].loc[df["elevation"]==elev].loc[df["taskname"]==mode]

paths = sorted(list(paths))
# print(len(paths))

# Set Engine
# engine = "netcdf4"
engine = "h5netcdf"

#%% Reading functions

# # original
# def read_single(f):
#     reindex = dict(start_angle=-0.5, stop_angle=360, angle_res=1., direction=1)
#     ds = xr.open_dataset(f, engine="iris", group="sweep_0", reindex_angle=reindex)
#     ds = ds.set_coords("sweep_mode")
#     ds = ds.rename_vars(time="rtime")
#     ds = ds.assign_coords(time=ds.rtime.min())
#     return ds

# @dask.delayed
# def process_single(f, num, dest):
#     ds = read_single(f)
#     moments = [k for k,v in ds.variables.items() if v.ndim == 2]
#     new_enc = {k: dwd_enc[k] for k in moments if k in dwd_enc}
#     shape = ds[moments[0]].shape
#     enc_new = dict(chunksizes=(1, ) + shape[1:])
#     [new_enc[k].update(enc_new) for k in new_enc]
#     dest = f"{dest}{num:03d}.nc"
#     ds.to_netcdf(dest, engine=engine, encoding=new_enc)
#     return dest

# revamped functions
def read_single(f):
    reindex = dict(start_angle=-0.5, stop_angle=360, angle_res=1., direction=1)
    ds = xr.open_dataset(f, engine="iris", group="sweep_0", reindex_angle=reindex) # not sure if sweep_0 is the name for all cases
    ds = ds.set_coords("sweep_mode")
    ds = ds.rename_vars(time="rtime")
    ds = ds.assign_coords(time=ds.rtime.min())
    ds["time"].encoding = ds["rtime"].encoding # copy also the encoding
    return ds

# @dask.delayed # We ditch dask to use multiprocessing below
def process_single(f, num, dest, scheme="unpacked", sdict={}):
    ds = read_single(f)
    moments = [k for k,v in ds.variables.items() if v.ndim == 2]
    if "unpacked" in scheme:
        valid = ["dtype", "_FillValue"]
        new_enc = {k: {key: val for key, val in ds[k].encoding.items() if key in valid} for k in moments}
    else: 
        new_enc = {k: dwd_enc[k] for k in moments if k in dwd_enc}
    
    shape = ds[moments[0]].shape
    enc_new = dict(chunksizes=(1, ) + shape[1:])
    enc_new.update(sdict) 
    [new_enc[k].update(enc_new) for k in new_enc]
    dest = f"{dest}{num:03d}.nc"
    ds.to_netcdf(dest, engine=engine, encoding=new_enc)
    return dest

#%%time  convert files in subfolder

dest = "/home/jgiles/turkey_test/test6_"
results = []
# # fill dask compute pipeline
# for i, f in tqdm(enumerate(paths)):
#     # results.append(client.compute(process_single(f, i, dest))) # some workers fail for some reason
#     results.append(process_single(f, i, dest).compute()) # this way takes about 5 min to process all paths
# # compute pipeline
# # this returns, if all results are computed
# for res in results:
#     print(res.result())    
    
# other way, much faster (about 1 min)
from multiprocessing import Pool
from functools import partial

process_single_partial = partial(process_single, dest=dest, scheme="packed")
with Pool() as P:
    results = P.starmap( process_single_partial, [(f, i) for i, f in enumerate(paths)] )
    

#%%time Reload converted files
dsr = xr.open_mfdataset(f"{dest}*", concat_dim="time", combine="nested", engine=engine)
# display(dsr)

#%% Fix encoding before writing to single file

moments = [k for k,v in dsr.variables.items() if v.ndim == 3]
shape = dsr[moments[0]].shape
enc_new= dict(chunksizes=(1, ) + shape[1:])

drop = ['szip', 'zstd', 'bzip2', 'blosc', 'coordinates']
enc = {k: {key: v.encoding[key] for key in v.encoding if key not in drop} for k, v in dsr.data_vars.items() if k in moments}
[enc[k].update(enc_new) for k in moments if k not in ["DB_HCLASS2"]]
del enc["DB_HCLASS2"]["chunksizes"]
encoding = {k: enc[k] for k in moments}
# print(encoding)


#%% Write to single daily file

dsr.to_netcdf(f"{dest}-iris-test-compressed-{engine}.nc", engine=engine, encoding=encoding)



#%% TEST Load the daily file

dsrunpckd = xr.open_dataset(f"/home/jgiles/turkey_test/test4_-iris-test-compressed-{engine}.nc")
dsrpckd = xr.open_dataset(f"/home/jgiles/turkey_test/test5_-iris-test-compressed-{engine}.nc")


# test plots
dsrunpckd.rtime[0].plot(label="unpckd")
dsrpckd.rtime[0].plot(label="pckd")

np.testing.assert_allclose(dsrunpckd.DBZH.values, dsrpckd.DBZH.values)


import matplotlib.pyplot as plt
vv = "DBTH"
tt = 0
dsrunpckd[vv][tt, 250, 0:100].plot(label="unpacked")
dsrpckd[vv][tt, 250, 0:100].plot(label="packed", ls="--")
plt.legend()
plt.suptitle(vv)

#%% OLD PART


# Get all files for one day
htypath = sorted(glob.glob("/home/jgiles/turkey_test/acq/OLDDATA/uza/RADAR/2017/07/27/HTY/RAW/*"))

# vol_hty= []

# for htyp in htypath[0:30]:
#     vol_hty.append(wrl.io.open_iris_mfdataset(htyp, reindex_angle=False))

# for ds in vol_hty:
#     print(ds.fixed_angle); print(ds.dims)

scan_strategy = {
                'SURVEILLANCE': np.array([0.]),
                'VOL_A': np.array([0., 0.7, 1.5, 3.]),
                'VOL_B': np.array([5.0, 7.0, 10.0, 15.0, 25.0]),
                'WIND': np.array([0.7, 1.5, 3.0, 5.0, 9.0]),
                }

radar_codes = {
                "ANK":  17138,
                "HTY": 17373,
                "GZT": 17259,
                "SVS": 17163,
                "AFY": 17187,
                }

# We will create a dataframe to store the metadata of all files and then select it more easily

# Read attributes of files
radarid = []
dtime = []
taskname = []
elevation = []
nrays_expected = []
nrays_written = []
nbins = []
rlastbin = []
binlength = []
horbeamwidth = []
fpath = []

for f in htypath:
    print(".", end="")
    # Read metadata
    m = wrl.io.read_iris(f, loaddata=False, keep_old_sweep_data=True)
    # Extract info
    fname = os.path.basename(f).split(".")[0]
    radarid_ = fname[0:3]
    dtimestr = fname[3:]
    dtime_ = dt.datetime.strptime(dtimestr, "%y%m%d%H%M%S")
    taskname_ = m["product_hdr"]["product_configuration"]["task_name"].strip()
    nbins_ = m["nbins"]
    rlastbin_ = m["ingest_header"]["task_configuration"]["task_range_info"]["range_last_bin"]/100
    binlength_ = m["ingest_header"]["task_configuration"]["task_range_info"]["step_output_bins"]/100
    horbeamwidth_ = round(m["ingest_header"]["task_configuration"]["task_misc_info"]["horizontal_beam_width"], 2)
    for i in range(10):
        try:
            nrays_expected_ = m["data"][i]["ingest_data_hdrs"]["DB_DBZ"]["number_rays_file_expected"]
            nrays_written_ = m["data"][i]["ingest_data_hdrs"]["DB_DBZ"]["number_rays_file_written"]    
            elevation_ = round(m["data"][i]["ingest_data_hdrs"]["DB_DBZ"]["fixed_angle"], 2)
            break
        except KeyError:
            try:
                nrays_expected_ = m["data"][i]["ingest_data_hdrs"]["DB_DBZ2"]["number_rays_file_expected"]
                nrays_written_ = m["data"][i]["ingest_data_hdrs"]["DB_DBZ2"]["number_rays_file_written"]    
                elevation_ = round(m["data"][i]["ingest_data_hdrs"]["DB_DBZ2"]["fixed_angle"], 2)
                break
            except KeyError:
                continue
    # Append to list
    radarid.append(radarid_)
    dtime.append(dtime_)
    taskname.append(taskname_)
    elevation.append(elevation_)
    nbins.append(nbins_)
    rlastbin.append(rlastbin_)
    binlength.append(binlength_)
    #nrays_expected.append(nrays_expected_)
    #nrays_written.append(nrays_written_)
    fpath.append(f)
    horbeamwidth.append(horbeamwidth_)   

# put attributes in a dataframe
from collections import OrderedDict
df = pd.DataFrame(OrderedDict(
                  {"radarid": radarid,
                   "datetime": dtime,
                   "taskname": taskname,
                   "elevation": elevation,
                   #"nrays_expected": nrays_expected,
                   #"nrays_written": nrays_written,
                   "nbins": nbins,
                   "rlastbin": rlastbin,
                   "binlength": binlength,
                   "horbeamwidth": horbeamwidth,
                   "fpath": fpath                   
                  }))


# For every elevation, select all files and open in a radar volume
modes = set(df["taskname"])


for mode in modes:
    elevations = set(df[df["taskname"] == mode]["elevation"])
    for elev in elevations:
        paths = df["fpath"].loc[df["elevation"]==elev].loc[df["taskname"]==mode]
        
        ds = wrl.io.open_iris_mfdataset(paths, reindex_angle=dict(start_angle=-0.5, stop_angle=360, angle_res=1., direction=1))

        
        # create an empty radar volume and put the previous data inside
        vol = wrl.io.RadarVolume()
        vol.append(ds)


        # Create a datatree
        dtree = dttree.DataTree(name="root")
        
        # for every elevation in the volume (there is only 1)
        for i, sw in enumerate(vol):
            
            # dim0 = list(set(sw.dims) & {"azimuth", "elevation"})[0]
            
            # check and fix how the angle variable is named
            if "fixed_angle" in sw:
                # rename the variable
                sw = sw.rename({"fixed_angle": "sweep_fixed_angle"}) 
                
            # get the sweep number according to the scan strategy
            try:
                ii = int(np.where(scan_strategy[mode] == round(float(sw.attrs["fixed_angle"]),1))[0])
            except:
                ii = 0
        
            # Put the data in the data tree
            dttree.DataTree(sw, name=f"sweep_{ii}", parent=dtree)
            
        
        # Save the datatree as netcdf 
        yyyymmdd = "".join(paths.iloc[0].split("/")[-6:-3])
        site = paths.iloc[0].split("/")[-3]
        name0 = "-".join(["0"+str(ii), 
                          yyyymmdd,
                          site,
                          str(radar_codes[paths.iloc[0].split("/")[-3]]),
                          "hd5"
                          ])
        name = "_".join([mode, "allmoms", name0 ])
        year = yyyymmdd[0:4]
        month = yyyymmdd[4:6]
        day = yyyymmdd[6:8]
        savepath = "/".join([ year, "-".join([year, month]), "-".join([year, month, day]),
                             site, mode, "0"+str(ii), "",
                             ])
        #################### HAY UN ERROR ACA , PARECIDO A LO QUE PASABA CON wrl.io.open_odim_mfdataset
        #################### AGREGUE EL MISMO FIX AL BACKEND DE IRIS PERO EL ERROR PERSISTE
        dtree.load().to_netcdf(savepath+name)
          

        
        # make a list of valid timesteps (those with dbzh > 5 in at least 1% of the bins)  
        valid = (sw["DBZH"][:]>5).sum(dim=("azimuth", "range")).compute() > sw["DBZH"][0].count().compute()*0.01
        valid = valid.time.where(valid, drop=True)
        
        # save the list as a txt file named true if there is any value, otherwise false
        if len(valid)>0:
            np.savetxt(path+"true.txt", valid.values.astype(str), fmt="%s")
        else:
            np.savetxt(path+"false.txt", valid.values.astype(str), fmt="%s")
        
        
        
        # # to load the datatree
        # vol_reload = dttree.open_datatree("_".join(name))
        
        # vol_reload["sweep_1"].ds # get as dataset
        # swp = vol_reload["sweep_1"].to_dataset() # get a sweep

# test: trying to find which files give error
something = list()
for i in range(240):
    something.append( sw["DBZH"][i].compute() )

# Test another way of loading the data
import tqdm
ds1 = [xr.open_dataset(f, engine="iris", group="sweep_0", 
                      reindex_angle=dict(start_angle=-0.5, stop_angle=360, angle_res=1., direction=1)) for f in tqdm.tqdm(paths.to_list())]
dsm = xr.concat(ds1, dim="time2")
dsm.to_netcdf("/home/jgiles/turkey_test/concattest.nc")

dsr = xr.open_dataset("/home/jgiles/turkey_test/concattest.nc")

xr.testing.assert_allclose(dsr.set_coords(["elevation", "time"]).DBZH[0], dsm.DBZH[0])

# the above workaround save the data with consecutive timesteps until 18:06, then the times are repeated from 00:00
# try again saving each file individually and then reading them together
for ii, dssave in enumerate(ds1):
    dssave.to_netcdf("/home/jgiles/turkey_test/testsave"+str(ii)+".nc")

dsr2 = xr.open_mfdataset(sorted(glob.glob("/home/jgiles/turkey_test/testsave*.nc"))) # does not work :S

# load a DWD daily file to compare
# to load the datatree
vol_reload = dttree.open_datatree("/home/jgiles/turkey_test/ras07-vol5minng01_sweeph5onem_allmoms_00-2017072700005800-pro-10392-hd5")

vol_reload["sweep_0"].ds # get as dataset
swp = vol_reload["sweep_0"].to_dataset() # get a sweep

# test loading Kai's daily file
dsr3 = xr.open_mfdataset("/automount/ftp/radar/iris/*")


##########################

# scan strategy for sorting elevations
scan_elevs = np.array([5.5, 4.5, 3.5, 2.5, 1.5, 0.5, 8.0, 12.0, 17.0, 25.0])

# get list of files in the folder
path = sys.argv[1]
ll = sorted(glob.glob(path+"/ras*hd5"))

# extract list of moments 
moments = set(fp.split("_")[-2] for fp in ll)

# discard "allmoms" from the set if it exists
moments.discard("allmoms")

# for every moment, open all files in folder (all timesteps) per moment into a dataset
vardict = {} # a dict for putting a dataset per moment
for mom in moments:
    
    # print("       Processing "+mom)
    
    # open the odim files (single moment and elevation, several timesteps)
    llmom = sorted(glob.glob(path+"/ras*_"+mom+"_*hd5"))
    
    # # there is a bug with the current implementation of xradar. Re check this in future releases
    # # Looks like now it works with a temporary fix in the files
    vardict[mom] = wrl.io.open_odim_mfdataset(llmom)
    
    # the old method seems to still work fine
    # vardict[mom] = wrl.io.open_odim(llmom, loader="h5py", chunks={})[0].data
    
# create an empty radar volume and put the previous data inside
vol = wrl.io.RadarVolume()
vol.append(xr.merge(vardict.values()))

# Create a datatree
dtree = dttree.DataTree(name="root")

# for every elevation in the volume (there is only 1)
for i, sw in enumerate(vol):
    
    # dim0 = list(set(sw.dims) & {"azimuth", "elevation"})[0]
    
    # check and fix how the angle variable is named
    if "fixed_angle" in sw:
        # rename the variable
        sw = sw.rename({"fixed_angle": "sweep_fixed_angle"}) 
        
    # get the sweep number according to the scan strategy
    try:
        ii = int(np.where(scan_elevs == round(float(sw.attrs["fixed_angle"]),1))[0])
    except:
        ii = 0

    # Put the data in the data tree
    dttree.DataTree(sw, name=f"sweep_{ii}", parent=dtree)
    

# Save the datatree as netcdf 
name = ll[0].split("_")
name[2]="allmoms"
dtree.load().to_netcdf("_".join(name))
  
# make a list of valid timesteps (those with dbzh > 5 in at least 1% of the bins)  
valid = (vardict["dbzh"]["DBZH"][:]>5).sum(dim=("azimuth", "range")).compute() > vardict["dbzh"]["DBZH"][0].count().compute()*0.01
valid = valid.time.where(valid, drop=True)

# save the list as a txt file named true if there is any value, otherwise false
if len(valid)>0:
    np.savetxt(path+"true.txt", valid.values.astype(str), fmt="%s")
else:
    np.savetxt(path+"false.txt", valid.values.astype(str), fmt="%s")



# # to load the datatree
# vol_reload = dttree.open_datatree("_".join(name))

# vol_reload["sweep_1"].ds # get as dataset
# swp = vol_reload["sweep_1"].to_dataset() # get a sweep
    
