#!/usr/bin/env python3

import argparse
from collections import defaultdict

from tqdm import tqdm
import h5py
import matplotlib.pyplot as plt
from matplotlib import colors
from mpl_toolkits.axes_grid1 import make_axes_locatable

import numpy as np
import yaml

def unique_channel_id(channel):
    return ((channel['io_group'].astype(int)*256 + channel['io_channel'].astype(int))*256 \
            + channel['chip_id'].astype(int))*64 + channel['channel_id'].astype(int)

threshold = 128
gain = 4 # mV /ke-
lsb = 3.91

_default_runtime = 2

geometrypath = '/home/brussell/batch2-tiles/bern/geometry/layout-2.4.0.yaml'

with open(geometrypath) as fi:
    geo = yaml.load(fi,Loader=yaml.FullLoader)
chip_pix = dict([(chip_id, pix) for chip_id,pix in geo['chips']])

def xy_mean_std_rate(data):
    for ch_id in data.keys():

        pix = chip_pix[int((ch_id//64)%256)][int(ch_id%64)] if (ch_id//64)%256 in chip_pix else None
        if pix:
            data[ch_id]['x'] = geo['pixels'][pix][1]
            data[ch_id]['y'] = geo['pixels'][pix][2]
        else:
            data[ch_id]['x'] = np.nan
            data[ch_id]['y'] = np.nan

    x = [data[key]['x'] for key in data if 'x' in data[key]]
    y = [data[key]['y'] for key in data if 'y' in data[key]]
    means = [data[key]['mean'] for key in data if 'mean' in data[key]]
    stds = [data[key]['std'] for key in data if 'std' in data[key]]
    rates = [data[key]['rate'] for key in data if 'rate' in data[key]]

    return x, y, means, stds, rates

def plot_summary(data):
    fig,axes = plt.subplots(3,1,
                            sharex=True,
                            num='selftrigger',
                            figsize=(5,12),
                            tight_layout=True)

    x, y, means, stds, rates = xy_mean_std_rate(data)

    sc0 = axes[0].scatter(x,y,
                          marker='.',
                          c=means)

    sc1 = axes[1].scatter(x,y,
                          marker='.',
                          c=stds)

    sc2 = axes[2].scatter(x,y,
                        marker='.',
                        c=rates)

    divider = make_axes_locatable(axes[0])
    cax0 = divider.append_axes("right", size="5%", pad=0.05)

    divider = make_axes_locatable(axes[1])
    cax1 = divider.append_axes("right", size="5%", pad=0.05)

    divider = make_axes_locatable(axes[2])
    cax2 = divider.append_axes("right", size="5%", pad=0.05)

    fig.colorbar(sc0, cax=cax0, label='Mean ADC')
    fig.colorbar(sc1, cax=cax1, label='Standard deviation ADC')
    fig.colorbar(sc2, cax=cax2, label='Rate [Hz]')

    axes[0].set(ylabel='y [mm]', aspect='equal')
    axes[1].set(ylabel='y [mm]', aspect='equal')
    axes[2].set(ylabel='y [mm]', xlabel='x [mm]', aspect='equal')

    fig.suptitle("Self-trigger run",fontsize=20)

    return fig, axes

def analyze_data(file):
    print('opening', file)

    with h5py.File(file,'r') as f:
        data_mask = f['packets']['packet_type'] == 0
        print(len(f['packets'][data_mask]),' data packets')
        data_mask = np.logical_and(f['packets']['valid_parity'], data_mask)
        print(len(f['packets'][data_mask]),' valid parity data packets')
        dataword = f['packets']['dataword'][data_mask]
        unique_id = unique_channel_id(f['packets'][data_mask])
        unixtime = f['packets']['timestamp'][f['packets']['packet_type'] == 4]
        livetime = np.max(unixtime) - np.min(unixtime)

    unique_id_set = np.unique(unique_id)
    data = defaultdict(dict)

    for channel in tqdm(unique_id_set, desc="Analyzing channels..."):

        id_mask = unique_id == channel

        if np.sum(id_mask) < 3:
            continue

        masked_dataword = dataword[id_mask]
        data[channel]['mean'] = np.mean(masked_dataword)
        data[channel]['std'] = np.std(masked_dataword)
        data[channel]['rate'] = len(masked_dataword) / (livetime + 1e-9)

    return data

def main(input_file):

    data = analyze_data(input_file)

    fig, axes = plot_summary(data)
    axes[0].set_title(input_file,fontsize='small')
    fig.savefig("selftrigger.png")

    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input_file',
                        type=str,
                        help='''Self-trigger HDF5 file''')
    args = parser.parse_args()
    c = main(**vars(args))
