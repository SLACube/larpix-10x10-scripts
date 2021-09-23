#!/usr/bin/env python3

import argparse
import json
import os.path
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

def unique_channel_id_2_str(unique_id):
    return (unique_id//(256*256*64)).astype(int).astype(str) \
        + '-' + ((unique_id//(256*64))%256).astype(int).astype(str) \
        + '-' + ((unique_id//64)%256).astype(int).astype(str) \
        + '-' + (unique_id%64).astype(int).astype(str)

threshold = 128
gain = 4 # mV /ke-
lsb = 3.91

_default_disabled_list = None
_default_log_qc = "log_qc.json"
_default_tile_id = 1
geometrypath = '/home/brussell/batch2-tiles/bern/geometry/layout-2.4.0.yaml'

with open(geometrypath) as fi:
    geo = yaml.load(fi,Loader=yaml.FullLoader)
chip_pix = dict([(chip_id, pix) for chip_id,pix in geo['chips']])

def xy_rate(data):
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
    rates = [data[key]['rate'] for key in data if 'rate' in data[key]]

    return x, y, rates

def plot_summary(data_original, data_updated):
    fig,axes = plt.subplots(1,2,
                            sharex=True,
                            num='leakage_rate',
                            figsize=(12,5),
                            tight_layout=True)
    x_original, y_original, rates_original = xy_rate(data_original)
    x_updated, y_updated, rates_updated = xy_rate(data_updated)

    max_rate = max(max(rates_original),max(rates_updated))

    sc0 = axes[0].scatter(x_original,y_original,
                          marker='.',
                          norm=colors.LogNorm(vmax=max_rate),
                          c=rates_original)

    sc1 = axes[1].scatter(x_updated,y_updated,
                          marker='.',
                          norm=colors.LogNorm(vmax=max_rate),
                          c=rates_updated)

    divider = make_axes_locatable(axes[0])
    cax0 = divider.append_axes("right", size="5%", pad=0.05)

    divider = make_axes_locatable(axes[1])
    cax1 = divider.append_axes("right", size="5%", pad=0.05)

    fig.colorbar(sc0, cax=cax0, label='Rate [Hz]')
    fig.colorbar(sc1, cax=cax1, label='Rate [Hz]')

    axes[0].set(xlabel='x [mm]', ylabel='y [mm]', aspect='equal')
    axes[1].set(xlabel='x [mm]', aspect='equal')

    fig.suptitle("Leakage rate",fontsize=20)

    return fig, axes

def update_log_qc(log_qc_file, tile_id, all_channels):
    if os.path.exists(log_qc_file):
        with open(log_qc_file,'r') as log_qc:
            existing_log = json.load(log_qc)

        if 'Leakage rate' not in existing_log[str(tile_id)]['Warm tile']:
            existing_log[str(tile_id)]['Warm tile']['Leakage rate'] = {}

        existing_chips = existing_log[str(tile_id)]['Warm tile']['Leakage rate']
        for chip in all_channels:
            if chip in existing_chips:
                for channel in all_channels[chip]:
                    if str(channel) not in existing_chips[chip]:
                        existing_chips[chip][channel] = all_channels[chip][channel]

            else:
                existing_chips[chip] = all_channels[chip]

        existing_log['%i'%tile_id]['Warm tile']['Leakage rate'] = existing_chips
        with open(log_qc_file,'w') as log_qc:
            json.dump(existing_log, log_qc, indent=4)
    else:
        with open(log_qc_file,'w') as log_qc:
            json.dump({tile_id:{'Warm tile':{'Leakage rate':all_channels}}}, log_qc, indent=4)

def analyze_data(file, disabled_list=None):
    print('opening', file)

    with h5py.File(file,'r') as f:
        data_mask = f['packets']['packet_type'] == 0
        print(len(f['packets'][data_mask]),' data packets')
        data_mask = np.logical_and(f['packets']['valid_parity'], data_mask)
        print(len(f['packets'][data_mask]),' valid parity data packets')
        dataword = f['packets']['dataword'][data_mask]
        timestamp = f['packets']['timestamp'][data_mask]
        unique_id = unique_channel_id(f['packets'][data_mask])

    unique_id_set = np.unique(unique_id)
    data = defaultdict(dict)
    all_channels = {}

    for channel in tqdm(unique_id_set, desc="Analyzing channels..."):
        id_mask = unique_id == channel
        adc = dataword[id_mask]
        livetime = np.max(timestamp[id_mask]) - np.min(timestamp[id_mask])
        runtime = livetime / 1e7 # seconds

        if runtime:
            rate_i = len(adc) / runtime
            chip_channel_key = unique_channel_id_2_str(channel)
            chip_key = "-".join(chip_channel_key.split("-")[:3])
            channel_key = int(chip_channel_key.split("-")[-1])

            if disabled_list:
                if chip_key in disabled_list:
                    if channel_key in disabled_list[chip_key]:
                        if chip_key in all_channels:
                            all_channels[chip_key][channel_key] = rate_i
                        else:
                            all_channels[chip_key] = {channel_key:rate_i}
            else:
                if chip_key in all_channels:
                    all_channels[chip_key][channel_key] = rate_i
                else:
                    all_channels[chip_key] = {channel_key:rate_i}

            data[channel] = dict(
                adc = adc,
                rate = rate_i,
                leakage = (rate_i)*threshold*lsb*(1000/gain)/1000 # e- / ms
            )

    return data, all_channels

def main(leakage_file,
         leakage_file_updated,
         disabled_list=_default_disabled_list,
         log_qc_file=_default_log_qc,
         tile_id=_default_tile_id):

    disabled_channels = None
    if disabled_list:
        with open(disabled_list,'r') as f:
            disabled_channels = json.load(f)

    data_original, all_channels_original = analyze_data(leakage_file, disabled_channels)
    data_updated, all_channels_updated = analyze_data(leakage_file_updated)

    update_log_qc(log_qc_file, tile_id, all_channels_original)
    update_log_qc(log_qc_file, tile_id, all_channels_updated)

    fig, axes = plot_summary(data_original, data_updated)
    axes[0].set_title('Original\n%s' % leakage_file, fontsize='small')
    axes[1].set_title('Updated\n%s' % leakage_file_updated, fontsize='small')
    fig.savefig("leakage.png")

    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--leakage_file',
                        type=str,
                        help='''Leakage HDF5 file''')
    parser.add_argument('--leakage_file_updated',
                        type=str,
                        help='''Leakage HDF5 file with updated bad channel list applied''')
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
