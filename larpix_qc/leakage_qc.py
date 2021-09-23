#!/usr/bin/env python3

import time
import argparse
import json

from copy import deepcopy
from collections import Counter

from larpix_qc import base

_default_controller_config = None
_default_chip_key = None
_default_threshold = 128
_default_runtime = 2
_default_channels = range(64)
_default_disabled_list = None
_default_leakage_cut = 10.
_default_invalid_cut = 0.1
_default_no_refinement = False

def run(c, runtime):
    print('run for',runtime,'sec')
    c.logger.enable()
    c.run(runtime,'collect data')
    c.logger.flush()
    c.logger.disable()

def enable_chip(c, chip_key, channels, threshold, disabled_channels):
    chip_config_pairs=[]
    initial_config = deepcopy(c[chip_key].config)
    c[chip_key].config.threshold_global = threshold
    for channel in channels:
        if chip_key in disabled_channels:
            if channel in disabled_channels[chip_key]: continue
        if channel in disabled_channels['All']: continue
        c[chip_key].config.channel_mask[channel] = 0
        c[chip_key].config.csa_enable[channel] = 1
    chip_config_pairs.append((chip_key,initial_config))
    c.io.double_send_packets = True
    c.io.group_packets_by_io_group = True
    chip_register_pairs = c.differential_write_configuration(chip_config_pairs, write_read=0, connection_delay=0.01)
    #chip_register_pairs = c.differential_write_configuration(chip_config_pairs, write_read=0, connection_delay=0.01)
    base.flush_data(c)

    ok, diff = c.enforce_configuration(chip_key, timeout=0.01, n=10, n_verify=10)
    c.io.double_send_packets = False
    c.io.group_packes_by_io_group = False
    if not ok:
        print('config error', list(diff.keys()))
        c.io.reset_larpix(length=24)
        print('brief sleep after after soft reset to clear FIFOs [ENABLE CHIP]')
        time.sleep(3)
        return False
    c.logger.record_configs([c[chip_key]])
    base.flush_data(c)
    return True

def disable_chip(c, chip_key, channels):
    c.io.double_send_packets = True
    c.io.group_packes_by_io_group = True
    chip_config_pairs=[]
    initial_config = deepcopy(c[chip_key].config)
    c[chip_key].config.threshold_global = 255
    for channel in channels:
        c[chip_key].config.channel_mask[channel] = 1
        c[chip_key].config.csa_enable[channel] = 0
    chip_config_pairs.append((chip_key,initial_config))
    chip_register_pairs = c.differential_write_configuration(chip_config_pairs, write_read=0, connection_delay=0.01)
    #chip_register_pairs = c.differential_write_configuration(chip_config_pairs, write_read=0, connection_delay=0.01)
    base.flush_data(c)

    ok, diff = c.enforce_configuration(chip_key, timeout=0.01, n=10, n_verify=10)
    c.io.double_send_packets = False
    c.io.group_packes_by_io_group = False
    if not ok:
        print('config error',list(diff.keys()))
        c.io.reset_larpix(length=24)
        print('brief sleep after after soft reset to clear FIFOs [DISABLE CHIP]')
        time.sleep(3)
        return False
    c.logger.record_configs([c[chip_key]])
    return True

def chip_key_to_string(chip_key):
    return str(chip_key.io_group)+'-'+str(chip_key.io_channel)+'-'+str(chip_key.chip_id)

def save_simple_json(record, now):
    with open('leakage-bad-channels-'+str(now)+'.json','w') as outfile:
        json.dump(record, outfile, indent=4)

def find_multimode(l):
    out=[]
    count_l = Counter(l)
    temp = count_l.most_common(1)[0][1]
    for a in l:
        if l.count(a) == temp:
            out.append(a)
    return list(set(out))

def main(controller_config=_default_controller_config,
         chip_key=_default_chip_key,
         threshold=_default_threshold,
         runtime=_default_runtime,
         channels=_default_channels,
         disabled_list=_default_disabled_list,
         leakage_cut=_default_leakage_cut,
         invalid_cut=_default_invalid_cut,
         no_refinement=_default_no_refinement):
    print('START ROUGH LEAKAGE')

    disabled_channels=dict()
    if disabled_list:
        print('applying disabled_list: ',disabled_list)
        with open(disabled_list,'r') as f: disabled_channels = json.load(f)
    else:
        disabled_channels["All"]=[6,7,8,9,22,23,24,25,38,39,40,54,55,56,57] # channels NOT routed out to pixel pads for LArPix-v2
        print('WARNING: no default disabled list applied')
    bad_channel_list = deepcopy(disabled_channels)
    n_bad_channels=0
    for key in bad_channel_list.keys():
        if key=="All": continue
        for channel in bad_channel_list[key]: n_bad_channels+=1
    print('\n\n\n===========\tStarting with ',n_bad_channels,' bad channels \t ===========\n\n\n')
    print('Initial bad channel list: ',bad_channel_list)

    now = time.strftime("%Y_%m_%d_%H_%M_%S_%Z")
    leakage_fname="leakage_%s.h5" % now
    c = base.main(controller_config, logger=True, filename=leakage_fname)

    chips_to_test = c.chips.keys()
    if not chip_key is None:
        chips_to_test = [chip_key]
    time.sleep(2)

    for chip_key in chips_to_test:
        time.sleep(0.5)

        avg_chan_trig_rate=leakage_cut+1
        count_failed_channels=0
        while avg_chan_trig_rate>=leakage_cut:

            if count_failed_channels>49:
                print('!!!!!!\tTEST FAIL: persistent failed channels on ASIC not disabled\t!!!!!!')
                return

            flag_enable = enable_chip(c, chip_key, channels, threshold, bad_channel_list)
            if not flag_enable:
                flag_enable = enable_chip(c, chip_key, channels, threshold, bad_channel_list)
            run(c, runtime)
            attempts = 0
            flag_disable = False
            while not flag_disable and attempts < 5:
                flag_disable = disable_chip(c, chip_key, channels)
                attempts += 1
            if not flag_disable:
                print('!!!!!!\tTEST FAIL: persistent configuration error in chip %s\t!!!!!!' % chip_key)
                return

            all_packets = len(c.reads[-1])
            avg_trig_rate = all_packets/runtime
            avg_chan_trig_rate = all_packets/runtime/len(channels)
            invalid_packets = c.reads[-1].extract('channel_id',valid_parity=0)
            invalid_fraction = len(invalid_packets)/all_packets if all_packets else 0
            print(chip_key,'\ttriggers:',all_packets,'\trate: {:0.2f}Hz (per channel: {:0.2f}Hz)\t invalid packet fraction: {:0.2f}'.format(avg_trig_rate, avg_chan_trig_rate,invalid_fraction))
            channel_to_disable=[]
            invalid_channel_to_disable = [] # THIS WASN'T DEFINED BEFORE, SO I ADDED IT HERE, MAYBE IT'S WRONG PLACE?
            # print(c.reads[-1].extract('channel_id'))
            triggered_channels = c.reads[-1].extract('channel_id')
            mode_channel = find_multimode(triggered_channels)
            print("channels",triggered_channels,mode_channel)
            if avg_chan_trig_rate>leakage_cut:
                triggered_channels = c.reads[-1].extract('channel_id')
                mode_channel = find_multimode(triggered_channels)

                for mc in mode_channel:
                    channel_to_disable.append(mc)
                    print('leakage rate exceeded. disable channel ', mc)

                n_bad_channels+=1
                count_failed_channels+=1

            if invalid_fraction>invalid_cut:
                mode_channel = find_multimode(invalid_packets)

                for mc in mode_channel:
                    invalid_channel_to_disable.append(mc)

                if avg_chan_trig_rate>leakage_cut:
                    for ctd in invalid_channel_to_disable:
                        if ctd not in channel_to_disable:
                            channel_to_disable.append(ctd)
                            print('invalid fraction exceeded. disable channel ', ctd)
                            n_bad_channels+=1
                            count_failed_channels+=1

            for mode_channel in channel_to_disable:
                _chip_key_string_=chip_key_to_string(chip_key)

                if _chip_key_string_ not in bad_channel_list:
                    bad_channel_list[_chip_key_string_]=[]

                bad_channel_list[_chip_key_string_].append(mode_channel)
                print('disabled channel ',mode_channel,' on ',chip_key)
            if len(channel_to_disable)>0:
                c.io.reset_larpix(length=24)
                time.sleep(3)

    now = time.strftime("%Y_%m_%d_%H_%M_%S_%Z")
    save_simple_json(bad_channel_list, now)

    if not no_refinement:

        leakage_fname="recursive_leakage_%s.h5" % now
        c = base.main(controller_config, logger=True, filename=leakage_fname)
        for chip_key in chips_to_test:
            time.sleep(0.5)

            flag_enable = enable_chip(c, chip_key, channels, threshold, bad_channel_list)
            if not flag_enable:
                flag_enable=enable_chip(c, chip_key, channels, threshold, bad_channel_list)
            run(c, runtime)
            flag_disable = disable_chip(c, chip_key, channels)
            if not flag_disable:
                flag_disable = disable_chip(c, chip_key, channels)

            all_packets = len(c.reads[-1])
            avg_trig_rate = all_packets/runtime
            avg_chan_trig_rate = all_packets/runtime/len(channels)
            invalid_packets = len(c.reads[-1].extract('channel_id',valid_parity=0))
            invalid_fraction = invalid_packets/all_packets if all_packets else 0
            print(chip_key,'\ttriggers:',all_packets,'\trate: {:0.2f}Hz (per channel: {:0.2f}Hz)\t invalid packet fraction: {:0.2f}'.format(avg_trig_rate, avg_chan_trig_rate,invalid_fraction))
            if avg_trig_rate>leakage_cut*1.5 or invalid_fraction>invalid_cut*1.2:
                print('!!!!!!\tTEST FAIL: persistant failed channel\t!!!!!!')
                return

    print('END ROUGH LEAKAGE')
    print('Soft reset issued')
    c.io.reset_larpix(length=24)

    print('\n\n\n===========\t',n_bad_channels,' bad channels\t ===========\n\n\n')
    return c

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller_config', default=_default_controller_config, type=str, help='''Hydra newtork config file''')
    parser.add_argument('--chip_key', default=_default_chip_key, type=str, help='''If specified, only collect data from specified chip''')
    parser.add_argument('--threshold', default=_default_threshold, type=int, help='''Global threshold value to set (default=%(default)s)''')
    parser.add_argument('--runtime', default=_default_runtime, type=float, help='''Duration for run (in seconds) (default=%(default)s)''')
    parser.add_argument('--channels', default=_default_channels, type=json.loads, help='''List of channels to collect data from (json formatted)''')
    parser.add_argument('--disabled_list', default=_default_disabled_list, type=str, help='''json-formatted dict of <chip key>:[<channels>] to disable''')
    parser.add_argument('--leakage_cut', default=_default_leakage_cut, type=float, help='''Leakage rate cut: ASIC channel average leakage rate not be exceeded''')
    parser.add_argument('--invalid_cut', default=_default_invalid_cut, type=float, help='''Invalid packet fraction cut: invalid packet fraction not to be exceeded''')
    parser.add_argument('--no_refinement', default=_default_no_refinement, action='store_true', help='''If flag present, leakage rate is not run recursively to measure leakage rate with all bad channels removed''')
    args = parser.parse_args()
    c = main(**vars(args))

