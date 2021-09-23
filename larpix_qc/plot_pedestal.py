#!/usr/bin/env python3

import argparse
from collections import defaultdict
import os.path
import json

import numpy as np
import matplotlib.pyplot as plt
from matplotlib import colors
import h5py
import yaml
from tqdm import tqdm
from mpl_toolkits.axes_grid1 import make_axes_locatable

_default_disabled_list = None
_default_log_qc = "log_qc.json"
_default_tile_id = 1

geometrypath = '/home/brussell/batch2-tiles/bern/geometry/layout-2.4.0.yaml'

with open(geometrypath) as fi:
    geo = yaml.load(fi,Loader=yaml.FullLoader)
chip_pix = dict([(chip_id, pix) for chip_id,pix in geo['chips']])

def unique_channel_id_2_str(unique_id):
    return (unique_id//(256*256*64)).astype(int).astype(str) \
        + '-' + ((unique_id//(256*64))%256).astype(int).astype(str) \
        + '-' + ((unique_id//64)%256).astype(int).astype(str) \
        + '-' + (unique_id%64).astype(int).astype(str)

def unique_channel_id(channel):
    return ((channel['io_group'].astype(int)*256 + channel['io_channel'].astype(int))*256 \
            + channel['chip_id'].astype(int))*64 + channel['channel_id'].astype(int)


def update_log_qc(log_qc_file, tile_id, all_channels_mean, all_channels_std):
    if os.path.exists(log_qc_file):
        with open(log_qc_file,'r') as log_qc:
            existing_log = json.load(log_qc)

        if 'Pedestal mean' not in existing_log[str(tile_id)]['Warm tile']:
            existing_log[str(tile_id)]['Warm tile']['Pedestal mean'] = {}

        if 'Pedestal std' not in existing_log[str(tile_id)]['Warm tile']:
            existing_log[str(tile_id)]['Warm tile']['Pedestal std'] = {}

        existing_chips_mean = existing_log[str(tile_id)]['Warm tile']['Pedestal mean']
        existing_chips_std = existing_log[str(tile_id)]['Warm tile']['Pedestal std']
        for chip in all_channels_mean:
            if chip in existing_chips_mean:
                for channel in all_channels_mean[chip]:
                    if str(channel) not in existing_chips_mean[chip]:
                        existing_chips_mean[chip][channel] = all_channels_mean[chip][channel]

            if chip in existing_chips_std:
                for channel in all_channels_std[chip]:
                    if str(channel) not in existing_chips_std[chip]:
                        existing_chips_std[chip][channel] = all_channels_std[chip][channel]
            else:
                existing_chips_mean[chip] = all_channels_mean[chip]
                existing_chips_std[chip] = all_channels_std[chip]

        existing_log[str(tile_id)]['Warm tile']['Pedestal mean'] = existing_chips_mean
        existing_log[str(tile_id)]['Warm tile']['Pedestal std'] = existing_chips_std

        with open(log_qc_file,'w') as log_qc:
            json.dump(existing_log, log_qc, indent=4)
    else:
        with open(log_qc_file,'w') as log_qc:
            json.dump({tile_id:{'Warm tile':{'Pedestal mean':all_channels_mean,
                                             'Pedestal std':all_channels_std}}},
                      log_qc, indent=4)

def analyze_data(filename, disabled_list=None):

    print("opening", filename)
    with h5py.File(filename,'r') as f:
        data_mask = f['packets']['packet_type'] == 0
        print(len(f['packets'][data_mask]),' data packets')
        data_mask = np.logical_and(f['packets']['valid_parity'], data_mask)
        print(len(f['packets'][data_mask]),' valid parity data packets')
        dataword = f['packets']['dataword'][data_mask]
        unique_id = unique_channel_id(f['packets'][data_mask])

    unique_id_set = np.unique(unique_id)
    data = defaultdict(dict)
    all_channels_mean = {}
    all_channels_std = {}

    for channel in tqdm(unique_id_set, desc="Analyzing channels..."):

        id_mask = unique_id == channel

        if np.sum(id_mask) < 3:
            continue

        masked_dataword = dataword[id_mask]
        mean = np.mean(masked_dataword)
        std = np.std(masked_dataword)
        data[channel]['mean'] = mean
        data[channel]['std'] = std

        chip_channel_key = unique_channel_id_2_str(channel)
        chip_key = "-".join(chip_channel_key.split("-")[:3])
        channel_key = int(chip_channel_key.split("-")[-1])

        if disabled_list:
            if chip_key in disabled_list:
                if channel_key in disabled_list[chip_key]:
                    if chip_key in all_channels_mean:
                        all_channels_mean[chip_key][channel_key] = mean
                        all_channels_std[chip_key][channel_key] = std
                    else:
                        all_channels_mean[chip_key] = {channel_key:mean}
                        all_channels_std[chip_key] = {channel_key:std}
        else:
            if chip_key in all_channels_mean:
                all_channels_mean[chip_key][channel_key] = mean
                all_channels_std[chip_key][channel_key] = std
            else:
                all_channels_mean[chip_key] = {channel_key:mean}
                all_channels_std[chip_key] = {channel_key:std}

    return data, all_channels_mean, all_channels_std

def xy_mean_stds(data):
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

    return x, y, means, stds

def plot_summary(data_original, data_updated):
    fig,axes = plt.subplots(2,2,sharex=True,num='pedestal',figsize=(11,9),tight_layout=True)
    x_original, y_original, means_original, stds_original = xy_mean_stds(data_original)
    x_updated, y_updated, means_updated, stds_updated = xy_mean_stds(data_updated)

    max_means = max(max(means_original), max(means_updated))
    max_stds = max(max(stds_original), max(stds_updated))

    sc_means = axes[0][0].scatter(x_original,y_original,
                                  marker='.',
                                  norm=colors.LogNorm(vmax=max_means),
                                  c=means_original)

    sc_stds = axes[1][0].scatter(x_original,y_original,
                                 marker='.',
                                 norm=colors.LogNorm(vmax=max_stds),
                                 c=stds_original)

    sc1_means = axes[0][1].scatter(x_updated,y_updated,
                                   marker='.',
                                   norm=colors.LogNorm(vmax=max_means),
                                   c=means_updated)

    sc1_stds = axes[1][1].scatter(x_updated,y_updated,
                                  marker='.',
                                  norm=colors.LogNorm(vmax=max_stds),
                                  c=stds_updated)

    divider = make_axes_locatable(axes[0][0])
    cax00 = divider.append_axes("right", size="5%", pad=0.05)
    fig.colorbar(sc_means, cax=cax00, label='Mean ADC')

    divider = make_axes_locatable(axes[1][0])
    cax10 = divider.append_axes("right", size="5%", pad=0.05)
    fig.colorbar(sc_stds, cax=cax10, label='Standard deviation ADC')

    divider = make_axes_locatable(axes[0][1])
    cax01 = divider.append_axes("right", size="5%", pad=0.05)
    fig.colorbar(sc1_means, cax=cax01, label='Mean ADC')

    divider = make_axes_locatable(axes[1][1])
    cax11 = divider.append_axes("right", size="5%", pad=0.05)
    fig.colorbar(sc1_stds, cax=cax11, label='Standard deviation ADC')

    axes[0][0].set(ylabel='y [mm]', aspect='equal')
    axes[1][0].set(xlabel='x [mm]', ylabel='y [mm]', aspect='equal')
    axes[0][1].set(ylabel='y [mm]', aspect='equal')
    axes[1][1].set(xlabel='x [mm]', ylabel='y [mm]', aspect='equal')

    fig.suptitle("Pedestal",fontsize=20)

    return fig, axes


def main(pedestal_file,
         pedestal_file_updated,
         disabled_list=_default_disabled_list,
         log_qc_file=_default_log_qc,
         tile_id=_default_tile_id):

    disabled_channels = None
    if disabled_list:
        with open(disabled_list,'r') as f:
            disabled_channels = json.load(f)

    data_original, all_channels_mean_original, all_channels_std_original  = analyze_data(pedestal_file, disabled_channels)
    data_updated, all_channels_mean_updated, all_channels_std_updated = analyze_data(pedestal_file_updated)
    update_log_qc(log_qc_file, tile_id, all_channels_mean_original, all_channels_std_original)
    update_log_qc(log_qc_file, tile_id, all_channels_mean_updated, all_channels_std_updated)

    fig, axes = plot_summary(data_original, data_updated)
    axes[0][0].set_title('Original\n%s' % pedestal_file, fontsize='small')
    axes[0][1].set_title('Updated\n%s' % pedestal_file_updated, fontsize='small')
    fig.savefig("pedestal.png")

    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pedestal_file',
                        type=str,
                        help='''Pedestal HDF5 file''')
    parser.add_argument('--pedestal_file_updated',
                        type=str,
                        help='''Pedestal HDF5 file with updated bad channel list applied''')
    parser.add_argument('--disabled_list',
                        default=_default_disabled_list,
                        type=str,
                        help='''File containing JSON-formatted dict of <chip key>:[<channels>] you'd like disabled''')
    parser.add_argument('--log_qc_file',
                        default=_default_log_qc,
                        type=str,
                        help='''File containing JSON-formatted QC log''')
    parser.add_argument('--tile_id',
                        default=_default_tile_id,
                        type=int,
                        help='''Tile ID''')
    args = parser.parse_args()
    c = main(**vars(args))
