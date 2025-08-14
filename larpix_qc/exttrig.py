#!/usr/bin/env python3
'''
'''

from larpix_qc import base
from copy import deepcopy
from collections import defaultdict
from random import shuffle

import fire
import os
import json
import time
import numpy as np
import larpix.logger

import larpix.format.rawhdf5format as rhdf5
import larpix.format.pacman_msg_format as pacman_msg_fmt

def _enforce_config(controller, chip_keys=None, check_csa=False):
    c = controller

    if chip_keys is None:
        chip_keys = list(c.chips.keys())

    ok,diff = c.enforce_configuration(
        chip_keys,
        timeout=0.01, connection_delay=0.01, 
        n=10, n_verify=10
    )

    if ok: return 

    if check_csa:
        csa_enable = [reg in range(66,74) for key, regs in diff.items() for reg in regs]
        if all(csa_enable): return

    raise RuntimeError(diff,'\nconfig error on chips',list(diff.keys()))

def save_raw_format(controller, runtime):
    c = controller
    c.io.disable_packet_parsing = True
    while True:
        counter = 0; last_count = 0
        c.io.enable_raw_file_writing = True
        c.io.raw_filename = time.strftime(c.io.default_raw_filename_fmt)
        c.io.join()
        rhdf5.to_rawfile(filename=c.io.raw_filename, io_version=pacman_msg_fmt.latest_version)
        print('new run file at ',c.io.raw_filename)

        c.start_listening()
        start_time = time.time()
        last_time = start_time
        while True:
            c.read()
            now = time.time()
            if now > start_time + runtime: break
            if now > last_time + 5 and c.io.raw_filename and os.path.isfile(c.io.raw_filename):
                counter = rhdf5.len_rawfile(c.io.raw_filename, attempts=0)
                print(' average message rate [delta_t = {:0.2f} s]: {:0.2f} ({:0.02f}Hz) \r'.format(now-last_time,counter-last_count,(counter-last_count)/(now-last_time+1e-9)),end='')
                last_count = counter
                last_time = now

        c.stop_listening()
        c.read()
        c.io.join()
        break

def save(controller, runtime):
    c = controller

    timestamp = time.strftime('%Y_%m_%d_%H_%M_%S_%Z')
    outfile = f'exttrig_{timestamp}.h5'
    print('saving to', outfile)

    c.logger = larpix.logger.HDF5Logger(outfile)
    c.logger.record_configs(list(c.chips.values()))

    base.flush_data(c)

    c.logger.enable()
    c.run(runtime, f'ext. trig.')
    c.logger.flush()
    print('packets read', len(c.reads[-1]))
    c.logger.disable()

def enable_channel(chip_key, disabled_channel_map):
    enable = np.full(64, True)
    idx = np.unique(
        disabled_channel_map.get('All', []) \
        + disabled_channel_map.get('chip_key', [])
    )

    if len(idx) == 0:
        return enable
    
    enable[idx] = False
    return enable

def load_active_pixels(fpath, max_score=None, limit=None):
    scores = np.loadtxt(fpath)

    uids = np.arange(len(scores))
    if max_score is not None:
        mask = scores<=max_score
        uids = uids[mask]
        scores = scores[mask]

    # sort according to scores
    idx = np.argsort(scores)
    uids = uids[idx]
    scores = scores[idx]
    
    if limit is not None:
        uids = uids[:limit]
        scores = uids[:limit]
    return set(uids)

def set_active_pixels(chip_id, active_pixels):
    enable = np.full(64, False)
    for ch in range(len(enable)):
        uid = (chip_id-11)*64 + ch
        if uid in active_pixels:
            enable[ch] = True
    return enable

def configure(controller, disabled_channel_map, active_pixels=None):
    c = controller
    c.io.group_packets_by_io_group = True
    c.io.double_send_packets = True

    print('configuring external trigger...')
    chip_config_pairs = []
    for chip_key, chip in reversed(c.chips.items()):
        chip_config_pairs.append((chip_key,deepcopy(chip.config)))
        chip.config.enable_hit_veto = 0
        chip.config.enable_periodic_reset = 0

        csa_enable = enable_channel(chip_key, disabled_channel_map)

        if active_pixels is not None:
            csa_enable &= set_active_pixels(chip.chip_id, active_pixels)

        mask = (~csa_enable).astype(int).tolist()

        chip.config.csa_enable = csa_enable.astype(int).tolist()
        chip.config.external_trigger_mask = mask
        chip.config.channel_mask = mask

    print('writing config...')
    diff_write_cfg = lambda : c.differential_write_configuration(
        chip_config_pairs, write_read=0, connection_delay=0.01
    )

    diff_write_cfg()
    diff_write_cfg()
    base.flush_data(c)

    print('enforcing config...')
    _enforce_config(c, check_csa=True)

    c.io.group_packets_by_io_group = False
    c.io.double_send_packets = False

def main(
        controller_config, 
        disabled_list=None, 
        save_raw=False, 
        runtime=30,
        max_score=None, 
        limit=None
):
    input('STOP the external trigger and press ENTER to continue ...')

    # load disabled channel list
    if disabled_list is None:
        print('using default disabled channels')
        # channels NOT routed out to pixel pads for LArPix-v2
        disabled_channel_map = {'ALL': [6,7,8,9,22,23,24,25,38,39,40,54,55,56,57]}
    else:
        print('loading disabled channels from', disabled_list)
        with open(disabled_list, 'r') as f:
            disabled_channel_map= json.load(f)

    # load active pixels
    #active_pixels = load_active_pixels('short_fiber.txt', max_score, limit)
    active_pixels = None
    
    #timestamp = time.strftime('%Y_%m_%d_%H_%M_%S_%Z')
    #outfile = f'exttrig-{timestamp}.h5'
    #c = base.main(
    #    controller_config=controller_config, logger=True, filename=outfile
    #)
    c = base.main(controller_config=controller_config, logger=False)

    configure(c, disabled_channel_map, active_pixels)


    print('Wait 3 seconds for cooling the ASICs...'); time.sleep(3)

    input('START the external trigger and press ENTER to continue ...')

    if save_raw:
        save_raw_format(c, runtime)
    else:
        save(c, runtime)

    print('END EXT TRIG RUN')

    print('soft reset')
    c.io.reset_larpix(length=24)
   
if __name__ == '__main__':
    fire.Fire(main)
