import larpix
import larpix.io
import larpix.logger

import base___no_enforce

import argparse
import json
from datetime import datetime
import h5py
import numpy as np

_default_controller_config=None
_default_chip_key=None
_default_threshold=128
_default_runtime=1
_default_disabled_list=None

rate_cut=[10000,1000,100] #,10]
suffix = ['no_cut','10kHz_cut','1kHz_cut','100Hz_cut']
v2a_nonrouted_channels=[6,7,8,9,22,23,24,25,38,39,40,54,55,56,57]

def initial_setup(ctr, controller_config):
    now = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    fname="trigger_rate_%s_" % suffix[ctr] #str(rate_cut[ctr])
    fname=fname+str(now)+".h5"
    c = base___no_enforce.main(controller_config, logger=True, filename=fname)
    return c, fname


def asic_test(c, chips_to_test, forbidden, threshold, runtime):
    channels = [i for i in range(0,64) if i not in v2a_nonrouted_channels]
    c.io.double_send_packets = False
    for chip_key in chips_to_test:
        for channel in channels: #range(64):
            p = (chip_key,channel)
            if p in forbidden:
                print(p,' skipped')
                continue
            c[chip_key].config.channel_mask[channel] = 0
            c[chip_key].config.csa_enable[channel] = 1
        c[chip_key].config.threshold_global = threshold
        chip_register_pairs=[]
        chip_register_pairs.append( (chip_key, list(range(131,139))+[64]+list(range(66,74)) ) )
        c.multi_write_configuration(chip_register_pairs)
        c.multi_write_configuration(chip_register_pairs)
        ok, diff = c.enforce_configuration(chip_key, timeout=0.01, n=10, n_verify=10)
        if not ok: print('config error',diff)
        c.logger.record_configs([c[chip_key]])

        base___no_enforce.flush_data(c)
        c.logger.enable()
        c.run(runtime,'collect data')
        c.logger.flush()
        c.logger.disable()
        print(chip_key,'\ttriggers:',len(c.reads[-1]),'\trate: {:0.2f}Hz'.format(len(c.reads[-1])/runtime))
        
        c[chip_key].config.channel_mask=[1]*64
        c[chip_key].config.csa_enable=[0]*64
        c[chip_key].config.threshold_global = 255
        c.multi_write_configuration(chip_register_pairs)
        c.multi_write_configuration(chip_register_pairs)
        ok, diff = c.enforce_configuration(chip_key, timeout=0.01, n=10, n_verify=10)
        if not ok: print('config error',diff)
        c.logger.record_configs([c[chip_key]])

              
def unique_channel_id(io_group, io_channel, chip_id, channel_id):
    return channel_id + 100*(chip_id + 1000*(io_channel + 1000*(io_group)))


def from_unique_to_chip_key(unique):
    io_group = (unique // (100*1000*1000)) % 1000
    io_channel = (unique // (100*1000)) % 1000
    chip_id = (unique // 100) % 1000
    return larpix.Key(io_group, io_channel, chip_id)

def chip_key_to_string(chip_key):
    return '-'.join([str(int(chip_key.io_group)),str(int(chip_key.io_channel)),str(int(chip_key.chip_id))])
              
def from_unique_to_channel_id(unique):
    return int(unique) % 100
              
              
def evaluate_rate(fname, ctr, runtime, forbidden):
    f = h5py.File(fname,'r')
    data_mask=f['packets'][:]['packet_type']==0
    data=f['packets'][data_mask]

    io_group=data['io_group'].astype(np.uint64)
    io_channel=data['io_channel'].astype(np.uint64)
    chip_id=data['chip_id'].astype(np.uint64)
    channel_id=data['channel_id'].astype(np.uint64)
    unique_channels = set(unique_channel_id(io_group, io_channel, chip_id, channel_id))

    for unique in sorted(unique_channels):
        channel_mask = unique_channel_id(io_group, io_channel, chip_id, channel_id) == unique
        triggers = len(data[channel_mask]['dataword'])
        if triggers/runtime > rate_cut[ctr]:
            pair = ( chip_key_to_string(from_unique_to_chip_key(unique)), from_unique_to_channel_id(unique) )
            if pair not in forbidden:
                forbidden.append(pair)
                print(pair,' added to do not enable list')
    return forbidden


def chip_key_string(chip_key):
    return '-'.join([str(int(chip_key.io_group)),str(int(chip_key.io_channel)),str(int(chip_key.chip_id))])

              
def save_do_not_enable_list(forbidden):
    d = {}
    for p in forbidden:
        #ck = chip_key_string(p[0])
        #ck = str(p[0])
        ck = p[0]
        if ck not in d: d[ck]=[]
        if p[1] not in d[ck]: d[ck].append(p[1])        
    now = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    with open('trigger-rate-DO-NOT-ENABLE-channel-list-'+now+'.json','w') as outfile:
        json.dump(d, outfile, indent=4)
        return 

              
def main(controller_config=_default_controller_config, chip_key=_default_chip_key, threshold=_default_threshold, runtime=_default_runtime, disabled_list=_default_disabled_list):
    print('START ITERATIVE TRIGGER RATE TEST')

    c = base___no_enforce.main(controller_config)
    chips_to_test = c.chips.keys()
    if not chip_key is None: chips_to_test = [chip_key]
    print('chips to test: ',chips_to_test)
    print('==> \tfound ASICs to test')

    forbidden=[] # list of (chip key, channel) to disable, to be updated as script progresses
    if disabled_list:
        print('applying disabled list: ', disabled_list)
        with open(disabled_list,'r') as f:
            disable_input=json.load(f)
            for key in disable_input.keys():
                channel_list = disable_input[key]
                for chan in channel_list:
                    forbidden.append((key,chan))
    else:
        print('No disabled list provided. Default disabled list applied.')
        for chip_key in chips_to_test:
            for channel in v2a_nonrouted_channels:
                forbidden.append((chip_key,channel))
    print('==> \tinitial channel disable list set')

    for ctr in range(len(rate_cut)):
        c, fname = initial_setup(ctr, controller_config)
        print('==> \ttesting ASICs with ',rate_cut[ctr],' Hz trigger rate threshold')
        asic_test(c, chips_to_test, forbidden, threshold, runtime)
        if ctr==3: continue
        n_initial=len(forbidden)
        forbidden = evaluate_rate(fname, ctr, runtime, forbidden)
        n_final=len(forbidden)
        print('==> \tdo not enable list updated with ',n_final-n_initial,' additional channels')
        
    save_do_not_enable_list(forbidden)
    print('END ITERATIVE TRIGGER RATE TEST')
    return c

              
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller_config', default=_default_controller_config, type=str, help='''Hydra newtork config file''')
    parser.add_argument('--chip_key', default=_default_chip_key, type=str, help='''If specified, only collect data from specified chip''')
    parser.add_argument('--threshold', default=_default_threshold, type=int, help='''Global threshold value to set (default=%(default)s)''')
    parser.add_argument('--runtime', default=_default_runtime, type=float, help='''Duration for run (in seconds) (default=%(default)s)''')
    parser.add_argument('--disabled_list', default=_default_disabled_list, type=str, help='''File containing json-formatted dict of <chip key>:[<channels>] to disable''')
    args = parser.parse_args()
    c = main(**vars(args))

