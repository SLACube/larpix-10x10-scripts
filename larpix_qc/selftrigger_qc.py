#!/usr/bin/env python3
'''
Loads specified configuration file and collects data until killed (cleanly exits)

For usage:
  selftrigger_qc.py --help

'''
import os
import argparse
import json
import time

import larpix.format.rawhdf5format as rhdf5
import larpix.format.pacman_msg_format as pacman_msg_fmt

from larpix_qc import base
from larpix_qc import enforce_loaded_config

_default_config_name=None
_default_controller_config=None
_default_runtime=10*60 # 10-min run files
_default_outdir='./'
_default_disabled_list=None

def main(config_name=_default_config_name, controller_config=_default_controller_config, runtime=_default_runtime, outdir=_default_outdir, disabled_list=_default_disabled_list):
    print('START RUN')
    startTime = time.time()

    c = None
    if config_name is None:
        c = base.main(controller_config)
    else:

        if disabled_list:
            print('applying disabled_list: ',disabled_list)
            with open(disabled_list,'r') as f: disabled_channels = json.load(f)
        else:
            disabled_channels = {}
            disabled_channels["All"]=[6,7,8,9,22,23,24,25,38,39,40,54,55,56,57] # channels NOT routed out to pixel pads for LArPix-v2
            print('WARNING: no default disabled list applied')

        if controller_config is None: c = enforce_loaded_config.main(config_name, logger=False, disabled_channels=disabled_channels)
        else: c = enforce_loaded_config.main(config_name, controller_config, logger=False, disabled_channels=disabled_channels)

    print(time.time()-startTime,' seconds to load & enforce configuration')

    trigger_forward_enable=False
    if trigger_forward_enable:
        external_trigger_channel = 6
        for chip in c.chips:
            print('enabling external triggers')
            c[chip].config.external_trigger_mask[external_trigger_channel] = 0
            c[chip].config.channel_mask[external_trigger_channel] = 0
            c.write_configuration(chip)
        c.io.set_reg(0x02014,0x0000) # enable forward triggers to larpix
    else:
        c.io.set_reg(0x02014,0xffffffff) # disable forward triggers to larpix
    print('Wait 3 seconds for cooling the ASICs...'); time.sleep(3)

    c.io.disable_packet_parsing = True
    while True:
        counter = 0; last_count = 0
        c.io.enable_raw_file_writing = True
        #c.io.raw_filename = time.strftime(c.io.default_raw_filename_fmt)
        c.io.raw_filename = os.path.join(
            outdir,
            time.strftime(c.io.default_raw_filename_fmt)
        )
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
    print('END RUN')
    print('Soft reset issued'); c.io.reset_larpix(length=24)
    return c

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config_name', default=_default_config_name, type=str, help='''Directory or filename to load chip configurations from''')
    parser.add_argument('--controller_config', default=_default_controller_config, type=str, help='''Hydra network configuration file''')
    parser.add_argument('--outdir', default=_default_outdir, type=str, help='''Directory to send data files to''')
    parser.add_argument('--runtime', default=_default_runtime, type=float, help='''Time duration before flushing remaining data to disk and initiating a new run (in seconds) (default=%(default)s)''')
    parser.add_argument('--disabled_list', default=_default_disabled_list, type=str, help='''File containing json-formatted dict of <chip key>:[<channels>] you'd like disabled''')
    args = parser.parse_args()
    c = main(**vars(args))
