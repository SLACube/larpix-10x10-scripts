import larpix
import larpix.io
import larpix.logger

from larpix_qc import base
import os
import h5py
import argparse
import time
import numpy as np
import json
from collections import Counter

_default_controller_config=None
_default_pedestal_file=None
_default_trim_sigma_file='channel_scale_factor.json'
_default_disabled_list=None
_default_noise_cut=3.
_default_null_sample_time= 1. #0.5 #1 #0.25
_default_disable_rate=20.
_default_set_rate=2.
_default_cryo=False
_default_vdda=1800
_default_normalization=1.
_default_verbose=False

nonrouted_channels=[6,7,8,9,22,23,24,25,38,39,40,54,55,56,57]

def measure_background_rate_increase_trim(c, extreme_edge_chip_keys, null_sample_time, set_rate, verbose):
    print('=====> Rate threshold: ',set_rate,' Hz')
    flag = True
    while flag:
        base.flush_data(c)
        c.multi_read_configuration(extreme_edge_chip_keys, timeout=null_sample_time,message='rate check')
        triggered_channels = c.reads[-1].extract('chip_key','channel_id',packet_type=0)
        print('total rate={}Hz'.format(len(triggered_channels)/null_sample_time))
        count = 0
        for chip_key, channel in set(map(tuple,triggered_channels)):
            rate = triggered_channels.count([chip_key,channel])/null_sample_time
            if rate > set_rate:
                count += 1
                print(chip_key,' rate too high (',rate,
                      ' Hz) increasinng channel ',channel,' trim DAC to 31')
                if chip_key not in c.chips: continue
                c[chip_key].config.pixel_trim_dac[channel] = 31
                c.write_configuration(chip_key,[channel])
        c.reads = []
        if count == 0: flag = False

    return

def measure_background_rate_disable_csa(c, extreme_edge_chip_keys, csa_disable,
                                        null_sample_time, disable_rate,verbose):
    print('=====> Rate threshold: ',disable_rate,' Hz')
    flag = True
    while flag:
        base.flush_data(c)
        c.multi_read_configuration(extreme_edge_chip_keys, timeout=null_sample_time,message='rate check')
        triggered_channels = c.reads[-1].extract('chip_key','channel_id',packet_type=0)
        fifo_flags = c.reads[-1].extract('shared_fifo_full',packet_type=0)
        fifo_half_full_flags = c.reads[-1].extract('shared_fifo_half',packet_type=0)
        print('total rate={}Hz'.format(len(triggered_channels)/null_sample_time))
        print('FIFO full flags {} half {}'.format(sum(fifo_flags), sum(fifo_half_full_flags)))
        count = 0
        for chip_key, channel in set(map(tuple,triggered_channels)):
            rate = triggered_channels.count([chip_key,channel])/null_sample_time
            if rate > disable_rate:
                count += 1
                print(chip_key,' rate too high (',rate,
                      ' Hz) disabling channel: ',channel)
                if chip_key not in c.chips: continue
                c.disable(chip_key,[channel])
                c[chip_key].config.csa_enable[channel] = 0
                c[chip_key].config.channel_mask[channel] = 1
                c.write_configuration(chip_key,'csa_enable')
                c.write_configuration(chip_key,'csa_enable')
                c.write_configuration(chip_key,'channel_mask')
                c.write_configuration(chip_key,'channel_mask')
                csa_disable[chip_key].append(channel)
        c.reads = []
        if count == 0: flag = False
        #else:
        #    for chip in c.chips:
        #        c.write_configuration(chip_key,'channel_mask')

    return csa_disable

def unique_channel_id(io_group, io_channel, chip_id, channel_id):
    return channel_id + 100*(chip_id + 1000*(io_channel + 1000*(io_group)))

def from_unique_to_channel_id(unique):
    return int(unique) % 100

def from_unique_to_chip_id(unique):
    return int(unique//100) % 1000

def from_unique_to_chip_key(unique):
    io_group = (unique // (100*1000*1000)) % 1000
    io_channel = (unique // (100*1000)) % 1000
    chip_id = (unique // 100) % 1000
    return larpix.Key(io_group, io_channel, chip_id)

def disable_channel(c, chip_key, channel, csa_disable):
    if chip_key not in csa_disable: csa_disable[chip_key] = []
    csa_disable[chip_key].append(channel)
    c.write_configuration(chip_key, csa_registers[int(channel/8)])
    return

def disable_multiple_channels(c, csa_disable):
    chip_register_pairs = []
    for chip_key in csa_disable.keys():
        if chip_key not in c.chips: continue
        for channel in csa_disable[chip_key]:
            c[chip_key].config.csa_enable[channel] = 0
        chip_register_pairs.append( (chip_key, list(range(66,74)) ) )
    c.multi_write_configuration(chip_register_pairs)
    return

def find_pedestal(pedestal_file, noise_cut, c, verbose):
    count_noisy = 0
    f = h5py.File(pedestal_file,'r')
    data_mask = f['packets'][:]['packet_type']==0
    valid_parity_mask = f['packets'][data_mask]['valid_parity']==1
    good_data = (f['packets'][data_mask])[valid_parity_mask]
    io_group = good_data['io_group'].astype(np.uint64)
    io_channel = good_data['io_channel'].astype(np.uint64)
    chip_id = good_data['chip_id'].astype(np.uint64)
    channel_id = good_data['channel_id'].astype(np.uint64)
    unique_channels = set(unique_channel_id(io_group, io_channel, chip_id, channel_id))

    pedestal_channel, csa_disable = [{} for i in range(2)]
    for unique in sorted(unique_channels):
        channel_mask = unique_channel_id(io_group, io_channel, chip_id, channel_id) == unique

        chip_key = from_unique_to_chip_key(unique)
        if chip_key not in c.chips: continue

        if from_unique_to_channel_id(unique) in nonrouted_channels:
            continue

        adc = good_data[channel_mask]['dataword']
        if len(adc) < 2 or np.mean(adc)>200. or np.std(adc)>noise_cut or np.mean(adc)==0:
            if verbose: print(from_unique_to_chip_key(unique),' disabling channel',from_unique_to_channel_id(unique),
                              ' with %.2f pedestal ADC RMS'%np.std(adc))
            if chip_key not in csa_disable: csa_disable[chip_key] = []
            csa_disable[chip_key].append(from_unique_to_channel_id(unique))
            count_noisy += 1
            continue

        pedestal_channel[unique] = dict(mu = np.mean(adc), std = np.std(adc))

    temp, temp_mu, temp_std = [ {} for i in range(3)]
    for unique in pedestal_channel.keys():
        chip_key = from_unique_to_chip_key(unique)
        if chip_key not in temp:
            temp[chip_key], temp_mu[chip_key], temp_std[chip_key] = [ [] for i in range(3)]
        temp[chip_key].append(pedestal_channel[unique]['mu']+pedestal_channel[unique]['std'])
        temp_mu[chip_key].append(pedestal_channel[unique]['mu'])
        temp_std[chip_key].append(pedestal_channel[unique]['std'])

    pedestal_chip = {}
    for chip_key in temp.keys():
        pedestal_chip[chip_key] = dict( metric = np.mean(temp[chip_key]),
                                        mu = np.mean(temp_mu[chip_key]),
                                        median = np.median(temp_mu[chip_key]),
                                        std = np.mean(temp_std[chip_key]) )

    print('!!!!! ',count_noisy,' NOISY CHANNELS TO DISABLE !!!!!')
    return pedestal_channel, pedestal_chip, csa_disable

def disable_from_file(c, disabled_list, csa_disable):
    disable_input=dict()
    if disabled_list:
        print('applying disabled list: ',disabled_list)
        with open(disabled_list,'r') as f: disable_input=json.load(f)
    else:
        print('No disabled list provided. Default disabled list applied.')
        disable_input["All"]=[6,7,8,9,22,23,24,25,38,39,40,54,55,56,57] # channels NOT routed out to pixel pads for LArPix-v2

    chip_register_pairs = []
    for chip_key in c.chips:
        chip_register_pairs.append( (chip_key, list(range(66,74)) ) )
        for key in disable_input:
            if key==chip_key or key=='All':
                for channel in disable_input[key]:
                    c[chip_key].config.csa_enable[channel] = 0
                    if chip_key not in csa_disable: csa_disable[chip_key] = []
                    csa_disable[chip_key].append(channel)
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    return csa_disable

def from_ADC_to_mV(c, chip_key, adc, flag, vdda):
    vref = vdda * (c[chip_key].config.vref_dac/256.)
    vcm = vdda * (c[chip_key].config.vcm_dac/256.)
    if flag==True: return adc * ( (vref - vcm) / 256. ) + vcm
    else: return adc * ( (vref - vcm) / 256. )

def find_mode(l):
    a = Counter(l)
    return a.most_common(1)
    
def enable_frontend(c, channels, csa_disable, ):
    chip_register_pairs = []
    #for chip in c.chips:
    #    ok,diff = c.verify_registers([(chip, list(range(131,139))+list(range(66,74)))], timeout=0.1, n=3)
    #    if not ok: print('config error:', diff)
    for io_group, io_channels in c.network.items():
        for io_channel in io_channels:
            for chip_key in c.get_network_keys(io_group,io_channel,root_first_traversal=False):
                chip_register_pairs.append( (chip_key, list(range(131,139))+list(range(66,74))) )
                for channel in range(64):
                    if chip_key in csa_disable:
                        if channel in csa_disable[chip_key]:
                            c[chip_key].config.channel_mask[channel] = 1
                            c[chip_key].config.csa_enable[channel] = 0
                            continue
                    c[chip_key].config.channel_mask[channel] = 0
                    c[chip_key].config.csa_enable[channel] = 1

    for pair in chip_register_pairs:
        c.multi_write_configuration([pair], connection_delay=0.001)
        c.multi_write_configuration([pair], connection_delay=0.001)
        high_rate = True
        runtime = 0.5 #1
        while high_rate:
            c.run(runtime,'check rate')
            chip_triggers = c.reads[-1].extract('chip_id',chip_key=pair[0])
            channel_triggers = c.reads[-1].extract('channel_id',chip_key=pair[0])
            #chip_triggers = c.reads[-1].extract('chip_id',chip_key=pair[0],packet_type=0)
            
            fifo_half = c.reads[-1].extract('shared_fifo_half',packet_type=0)
            fifo_full = c.reads[-1].extract('shared_fifo_full',packet_type=0)
            print('\t\tfifo half full {} fifo full {}'.format(sum(fifo_half), sum(fifo_full)))
            print('total packets {}\t{} {}'.format(len(c.reads[-1]),pair[0],len(chip_triggers)))
            print('offending channel, triggers: {}'.format(find_mode(channel_triggers)))
            if len(chip_triggers)/runtime > 2000:
                #print('\t\thigh rate channels! issue soft reset and raise global threshold {}'.format(
                print('\t\thigh rate channels! raise global threshold {}'.format(
                    #c[pair[0]].config.threshold_global + 1))
                    c[pair[0]].config.threshold_global + 1))
                #if sum(fifo_half)!=0:
                #    c[pair[0]].config.load_config_defaults = 1
                #    c.write_configuration(pair[0], 'load_config_defaults')
                #    c.write_configuration(pair[0], 'load_config_defaults')
                #    c[pair[0]].config.load_config_defaults = 0
                #    print('---- ISSURING A SOFTWARE RESET ----')
                c[pair[0]].config.threshold_global += 1
                registers = [123, 64]
                c.write_configuration(pair[0], registers)
                c.write_configuration(pair[0], registers)
            else:
                high_rate = False
            ok,diff = c.enforce_registers([pair], timeout=0.1, n=3, n_verify=3)
            if not ok:
                #print('config error:', diff)
                raise RuntimeError(diff,'\nconfig error on chips',list(diff.keys()))
    #c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    #c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    #print('verifying config')
    #for chip_key in c.chips:
    #    ok,diff = c.enforce_registers([(chip_key, list(range(131, 139))+list(range(66,74)))], timeout=0.1, n=3, n_verify=3)
    #    if not ok: print('config error:', diff)

def find_global_dac_seed(c, pedestal_chip, normalization, cryo, vdda, verbose):
    global_dac_lsb = vdda/256.
    offset = 210 # [mV] at 300 K
    if cryo: offset = 390 # [mV] at 88 K
    print('PEDESTAL OFFSET: ',offset)
    chip_register_pairs = []
    for chip_key in pedestal_chip.keys():
        if chip_key not in c.chips: continue
        #mu_mV = from_ADC_to_mV(c, chip_key, pedestal_chip[chip_key]['mu'], True, vdda)
        mu_mV = from_ADC_to_mV(c, chip_key, pedestal_chip[chip_key]['median'], True, vdda)
        std_mV = from_ADC_to_mV(c, chip_key, pedestal_chip[chip_key]['std'], False, vdda)
        if verbose: print(chip_key,' pedestal: ',mu_mV,' +/- ',std_mV)
        x = (normalization * std_mV) + mu_mV
        global_dac = int(round((x-offset)/global_dac_lsb))
        if global_dac<0: global_dac = 0
        if global_dac>255: global_dac = 255
        if verbose: print(chip_key,'at global DAC',global_dac,'for %.1f mV predicted threshold'%x)
        c[chip_key].config.threshold_global = global_dac
        c[chip_key].config.pixel_trim_dac = [31]*64
        c[chip_key].config.enable_periodic_reset = 1 # registers 128
        c[chip_key].config.enable_rolling_periodic_reset = 1 # registers 128
        c[chip_key].config.periodic_reset_cycles = 64 # registers [163-165]
        chip_register_pairs.append( (chip_key, list(range(0,65))+[128,163,164,165]) )

    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    return

def load_trim_sigma(trim_sigma_file):
    trim_sigma = {}
    with open(trim_sigma_file,'r') as f:
        data = json.load(f)
        for key in data.keys():
            trim_sigma[key] = data[key][0]+data[key][1]
    return trim_sigma

def find_trim_dac_seed(c, channels, cryo, vdda,
                       pedestal_channel, pedestal_chip, trim_sigma):
    global_dac_lsb = vdda/256.
    trim_scale = 1.45 # [mV] at 300 K
    offset = 210 # [mV] at 300 K
    if cryo:
        trim_scale = 2.34 # [mV] at 88 K
        offset = 465 # [mV] at 88 K

    chip_register_pairs = []
    for i in pedestal_channel.keys():
        ped_chip_key = from_unique_to_chip_key(i)
        if ped_chip_key not in c.chips: continue
        ped_channel = from_unique_to_channel_id(i)
        if ped_channel not in channels: continue

        x = trim_sigma[str(ped_channel)] * from_ADC_to_mV(c, ped_chip_key, pedestal_channel[i]['std'], False, vdda)
        y = from_ADC_to_mV(c, ped_chip_key, pedestal_channel[i]['mu'], True, vdda)
        z = (global_dac_lsb * c[ped_chip_key].config.threshold_global) + offset
        trim_dac = int(round((x+y-z)/trim_scale))

        if trim_dac<0: trim_dac = 0
        if trim_dac>31: trim_dac = 31
        c[ped_chip_key].config.pixel_trim_dac[ped_channel] = trim_dac
        chip_register_pairs.append( (ped_chip_key, [ped_channel]) )

    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    return

def channel_start_listen(c, chip_keys, channel, csa_disable):
    chip_register_pairs = []
    flag = False
    for chip_key in chip_keys:
        if chip_key in csa_disable:
            if channel in csa_disable[chip_key]: continue
        c[chip_key].config.channel_mask[channel] = 0 # registers [131-138]
        c[chip_key].config.csa_enable[channel] = 1 # registers [66-73]
        chip_register_pairs.append( (chip_key, list(range(66,74))+list(range(131,139)) ) )
        flag = True
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    return flag

def channel_stop_listen(c, chip_keys, channel):
    chip_register_pairs = []
    for chip_key in chip_keys:
        c[chip_key].config.channel_mask[channel] = 1 # registers [131-138]
        c[chip_key].config.csa_enable[channel] = 0 # registers [66-73]
        chip_register_pairs.append( (chip_key, list(range(66,74))+list(range(131,139)) ) )
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)

def send_testpulse(c, chip_keys, channel, n_pulses, start_dac, pulse_dac):
    c.reads = []
    chip_register_pairs = []
    for chip_key in chip_keys:
        c[chip_key].config.csa_testpulse_enable = [1]*64
        c[chip_key].config.csa_testpulse_enable[channel] = 0
        c[chip_key].config.csa_testpulse_dac = start_dac
        chip_register_pairs.append( (chip_key, list(range(100,109))) )
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    packet, byte = ([] for i in range(2))

    c.start_listening()
    for i in range(n_pulses):
        chip_register_pairs = []
        for chip_key in chip_keys:
            c[chip_key].config.csa_testpulse_dac = start_dac-pulse_dac
            chip_register_pairs.append( (chip_key, 108) )
        c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)

        chip_register_pairs = []
        for chip_key in chip_keys:
            c[chip_key].config.csa_testpulse_dac = start_dac
            chip_register_pairs.append( (chip_key, [108]) )
        c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)

    read_packets, read_bytestream = c.read()
    c.stop_listening()

    for chip_key in chip_keys:
        c[chip_key].config.csa_testpulse_enable[channel] = 1
        chip_register_pairs.append( (chip_key, list(range(100,108))) )
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)

    packet.extend(read_packets)
    byte.append(read_bytestream)
    data = b''.join(byte)
    c.store_packets(packet,data,'')

    eff_dict = {}
    for chip_key in chip_keys:
        eff_dict[chip_key] = len(c.reads[-1].extract('channel_id',packet_type=0,channel_id=channel,chip_id=chip_key.chip_id))/n_pulses
        if len(c.reads[-1].extract('channel_id',packet_type=0,channel_id=channel,chip_id=chip_key.chip_id))/n_pulses>0:
            print (chip_key,' efficiency: ',len(c.reads[-1].extract('channel_id',packet_type=0,channel_id=channel,chip_id=chip_key.chip_id))/n_pulses)
    return eff_dict

def set_pixel_trim(c, channel, status):
    chip_register_pairs = []
    for chip_key in status.keys():
        c[chip_key].config.pixel_trim_dac[channel] = status[chip_key]['pixel_trim']
        c[chip_key].config.channel_mask[channel] = 0 # registers [131-138]
        c[chip_key].config.csa_enable[channel] = 1 # registers [66-73]
        chip_register_pairs.append( (chip_key, list(range(0,64))) )
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    return

def chip_key_string(chip_key):
    return '-'.join([str(int(chip_key.io_group)),str(int(chip_key.io_channel)),str(int(chip_key.chip_id))])

def note_tagged_from_outset(channel, csa_disable, record):
    for chip_key in csa_disable.keys():
        if chip_key not in record:
            record[chip_key_string(chip_key)] = []
        if channel in csa_disable[chip_key]:
            record[chip_key_string(chip_key)].append(-1)
    return

def update(c, status, csa_disable, channel):
    chip_register_pairs = []
    for chip_key in status.keys():
        c[chip_key].config.pixel_trim_dac[channel] = status[chip_key]['pixel_trim']
        if status[chip_key]['active'] == False:
            c[chip_key].config.csa_enable[channel] = 0
            chip_register_pairs.append( (chip_key, [channel]+ list(range(66,74))) )
            continue
        if status[chip_key]['disable'] == True:
            csa_disable[chip_key].append(channel)
            c[chip_key].config.csa_enable[channel] = 0
            chip_register_pairs.append( (chip_key, [channel]+ list(range(66,74))) )
            continue
        chip_register_pairs.append( (chip_key, [channel]) )
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)

def update_chip(c, status):
    chip_register_pairs = []
    for chip_key in status.keys():
        chip_register_pairs.append( (chip_key, list(range(64))+ list(range(66,74)) +list(range(131,139) ) ))
        c[chip_key].config.pixel_trim_dac = status[chip_key]['pixel_trim']
        for channel in range(64):
            if status[chip_key]['disable'][channel] == True or status[chip_key]['active'][channel] == False:
                c[chip_key].config.csa_enable[channel] = 0
                c[chip_key].config.channel_mask[channel] = 1

    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    return

def silence_all(c, chip_keys):
    chip_register_pairs = []
    for chip_key in chip_keys:
        c[chip_key].config.csa_enable = [0]*64
        c[chip_key].config.channel_mask = [1]*64
        chip_register_pairs.append( (chip_key, list(range(66,74))+list(range(131,139)) ) )
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    return

def toggle_trim(c, channels, csa_disable, extreme_edge_chip_keys,
              null_sample_time, set_rate, verbose):
    status = {}
    for chip_key in c.chips:
        l = list(c[chip_key].config.pixel_trim_dac)
        status[chip_key] = dict( pixel_trim=l, active=[True]*64, disable=[False]*64)
        for channel in range(64):
            if channel in csa_disable[chip_key]:
                status[chip_key]['active'][channel] = False
                status[chip_key]['disable'][channel] = True

    iter_ctr = 0
    flag = True
    while flag:
        timeStart = time.time()
        iter_ctr += 1
        base.flush_data(c)
        c.multi_read_configuration(extreme_edge_chip_keys, timeout=null_sample_time,message='rate check')
        triggered_channels = c.reads[-1].extract('chip_key','channel_id',packet_type=0)
        print('total rate={}Hz'.format(len(triggered_channels)/null_sample_time))
        fired_channels = {}
        for chip_key, channel in set(map(tuple,triggered_channels)):
            if chip_key not in fired_channels: fired_channels[chip_key] = []
            fired_channels[chip_key].append(channel)
            rate = triggered_channels.count([chip_key,channel])/null_sample_time
            if chip_key not in status.keys(): continue
            if status[chip_key]['active'][channel] == False: continue

            if rate >= set_rate:
                if verbose: print(chip_key,' channel ',channel,' pixel trim',status[chip_key]['pixel_trim'][channel],'below noise floor -- increasing trim')
                status[chip_key]['pixel_trim'][channel] += 1
                if status[chip_key]['pixel_trim'][channel]>31:
                    status[chip_key]['pixel_trim'][channel] = 31
                    status[chip_key]['disable'][channel] = True
                    status[chip_key]['active'][channel] = False
                    csa_disable[chip_key].append(channel)
                    if verbose: print(chip_key,' channel ',channel,'pixel trim maxed out below noise floor!!! -- channel CSA disabled')
                else:
                    status[chip_key]['active'][channel] = False
                    if verbose: print('pixel trim set at',status[chip_key]['pixel_trim'][channel])
            else:
                status[chip_key]['pixel_trim'][channel] -= 1
                if status[chip_key]['pixel_trim'][channel]<0:
                    status[chip_key]['pixel_trim'][channel] = 0
                    status[chip_key]['active'][channel] = False
                    if verbose: print('pixel trim bottomed out above noise floor!!!')

        for chip_key in c.chips:
            if status[chip_key]['active'] == [False]*64: continue
            for channel in channels:
                if status[chip_key]['active'][channel] == False: continue
                if chip_key in fired_channels:
                    if channel in fired_channels[chip_key]: continue
                status[chip_key]['pixel_trim'][channel] -= 1
                if status[chip_key]['pixel_trim'][channel]<0:
                    status[chip_key]['pixel_trim'][channel] = 0
                    status[chip_key]['active'][channel] = False
                    if verbose: print('pixel trim bottomed out above noise floor!!!')

        update_chip(c, status)
        count = 0
        for chip_key in status:
            if True in status[chip_key]['active']: count+=1
            if count == 1: break
        if count == 0: flag = False
        timeEnd = time.time()-timeStart
        print('iteration ', iter_ctr,' processing time %.3f seconds\n\n'%timeEnd)

    return csa_disable

def save_config_to_file(c, chip_keys, csa_disable, verbose, outdir='./'):
    if not os.path.isdir(outdir): 
        os.makedirs(outdir)

    chip_register_pairs = []
    for chip_key in chip_keys:
        c[chip_key].config.csa_enable = [1]*64
        c[chip_key].config.channel_mask= [0]*64
        if chip_key in csa_disable:
            for channel in csa_disable[chip_key]:
                c[chip_key].config.csa_enable[channel] = 0
                c[chip_key].config.channel_mask[channel] = 1
        chip_register_pairs.append( (chip_key, list(range(66,74))+list(range(131,139))))
    c.multi_write_configuration(chip_register_pairs, connection_delay=0.001)
    for chip_key in chip_keys:
        time_format = time.strftime('%Y_%m_%d_%H_%S_%Z')
        config_filename = 'config-'+str(chip_key)+'-'+time_format+'.json'
        fpath = os.path.join(outdir, config_filename)
        c[chip_key].config.write(fpath, force=True)
        if verbose: print('\t',chip_key,'saved to',config_filename)
    return

def save_stats(record):
    time_format = time.strftime('%Y_%m_%d_%H_%S_%Z')
    with open('config-record-'+time_format+'.json', 'w') as outfile:
        json.dump(record, outfile, indent=4)

def main(controller_config=_default_controller_config,
         pedestal_file=_default_pedestal_file,
         trim_sigma_file=_default_trim_sigma_file,
         disabled_list=_default_disabled_list,
         noise_cut=_default_noise_cut,
         null_sample_time=_default_null_sample_time,
         disable_rate=_default_disable_rate,
         set_rate=_default_set_rate,
         cryo=_default_cryo,
         vdda=_default_vdda,
         normalization=_default_normalization,
         verbose=_default_verbose,
         outdir='./',
         **kwargs):

    time_initial = time.time()

    c = base.main(controller_config=controller_config)
    base.flush_data(c, runtime=2)
    print('START THRESHOLD\n')

    channels = [ i for i in range(64) if i not in nonrouted_channels ]
    chip_keys = c.chips

    extreme_edge_chip_keys = []
    for io_group in c.network:
        for io_channel in c.network[io_group]:
            extreme_edge_chip_ids = [chip_id for chip_id, deg in c.network[io_group][io_channel]['miso_us'].out_degree() if deg==0]
            extreme_edge_chip_keys += [larpix.Key(io_group, io_channel, chip_id) for chip_id in extreme_edge_chip_ids]
    read_extreme_edge = [(key,0) for key in extreme_edge_chip_keys]

    timeStart = time.time()
    pedestal_channel, pedestal_chip, csa_disable = find_pedestal(pedestal_file, noise_cut, c, verbose)
    timeEnd = time.time()-timeStart
    print('==> %.3f seconds --- pedestal evaluation \n\n'%timeEnd)

    timeStart = time.time()
    csa_disable = disable_from_file(c, disabled_list, csa_disable)
    timeEnd = time.time()-timeStart
    print('==> %.3f seconds --- disable channels from input list'%timeEnd)

    timeStart = time.time()
    find_global_dac_seed(c, pedestal_chip, normalization, cryo, vdda, verbose)
    timeEnd = time.time()-timeStart
    print('==> %.3f seconds --- set global DAC seed \n\n'%timeEnd)

    timeStart = time.time()
    enable_frontend(c, channels, csa_disable)
    timeEnd  = time.time()-timeStart
    print('==> %.3f seconds --- enable frontend \n\n'%timeEnd)

    timeStart = time.time()
    dr = disable_rate*50
    csa_disable = measure_background_rate_disable_csa(c, extreme_edge_chip_keys, csa_disable, null_sample_time, dr, verbose)
    timeEnd = time.time() - timeStart
    print('==> %.3f seconds --- measured background rate with seeded global DAC & trim DAC maxed out\n --> silence channels that exceed rate\n\n'%timeEnd)

    timeStart = time.time()
    dr = disable_rate*5
    csa_disable = measure_background_rate_disable_csa(c, extreme_edge_chip_keys, csa_disable, null_sample_time, dr, verbose)
    timeEnd = time.time() - timeStart
    print('==> %.3f seconds --- measured background rate with seeded global DAC & trim DAC maxed out\n --> silence channels that exceed rate\n\n'%timeEnd)

    timeStart = time.time()
    dr = disable_rate*0.5
    csa_disable = measure_background_rate_disable_csa(c, extreme_edge_chip_keys, csa_disable, null_sample_time, dr, verbose)
    timeEnd = time.time() - timeStart
    print('==> %.3f seconds --- measured background rate with seeded global DAC & trim DAC maxed out\n --> silence channels that exceed rate\n\n'%timeEnd)

    ###timeStart = time.time()
    ###trim_sigma = load_trim_sigma(trim_sigma_file)
    ###timeEnd = time.time() - timeStart
    ###print('==> %.3f seconds --- load trim DAC scaling fractor from --trim_sigma_file \n\n'%timeEnd)

    ###timeStart = time.time()
    ###find_trim_dac_seed(c, channels, cryo, vdda, pedestal_channel, pedestal_chip, trim_sigma)
    ###timeEnd = time.time() - timeStart
    ###print('==> %.3f seconds --- set trim DAC seed \n\n'%timeEnd)

    ###timeStart = time.time()
    ###measure_background_rate_increase_trim(c, extreme_edge_chip_keys, null_sample_time, disable_rate, verbose)
    ###timeEnd = time.time() - timeStart
    ###print('==> %.3f seconds --- measured background rate with seeded global & trim DACs\n --> trim DAC maxed out for channels that exceed rate\n\n'%timeEnd)

    ###timeStart = time.time()
    ###measure_background_rate_increase_trim(c, extreme_edge_chip_keys, null_sample_time, disable_rate, verbose)
    ###timeEnd = time.time() - timeStart
    ###print('==> %.3f seconds --- measured background rate with seeded global & trim DACs\n --> trim DAC maxed out for channels that exceed rate\n\n'%timeEnd)

    ###timeStart = time.time()
    ###measure_background_rate_increase_trim(c, extreme_edge_chip_keys, null_sample_time, disable_rate, verbose)
    ###timeEnd = time.time() - timeStart
    ###print('==> %.3f seconds --- measured background rate with seeded global & trim DACs\n --> trim DAC maxed out for channels that exceed rate\n\n'%timeEnd)

    timeStart = time.time()
    toggle_trim(c, channels, csa_disable, extreme_edge_chip_keys,
                null_sample_time, set_rate, verbose)
    timeEnd = time.time() - timeStart
    print('==> %.3f seconds --- toggle trim DACs'%timeEnd)

    timeStart = time.time()
    save_config_to_file(c, chip_keys, csa_disable, verbose, outdir)
    timeEnd = time.time()-timeStart
    print('==> %.3f seconds --- saving to json config file \n'%timeEnd)

    time10 = time.time()-time_initial
    print('END THRESHOLD ==> %.3f seconds total run time'%time10)
    return c

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--controller_config',
                        default=_default_controller_config,
                        type=str,
                        help='''ASIC hydra network json''')
    parser.add_argument('--pedestal_file',
                        default=_default_pedestal_file,
                        type=str,
                        help='''Path to pedestal file''')
    parser.add_argument('--trim_sigma_file',
                        default=_default_trim_sigma_file,
                        type=str,
                        help='''Path to channel-dependent trim DAC scaling file''')
    parser.add_argument('--disabled_list',
                        default=_default_disabled_list,
                        type=str,
                        help='''File containing json-formatted dict of <chip key>:[<channels>] to disable''')
    parser.add_argument('--noise_cut',
                        default=_default_noise_cut,
                        type=float,
                        help='''Disable channel CSA if pedestal ADC RMS exceeds this value''')
    parser.add_argument('--null_sample_time',
                        default=_default_null_sample_time,
                        type=float,
                        help='''Time to self-trigger null pulse''')
    parser.add_argument('--disable_rate',
                        default=_default_disable_rate,
                        type=float,
                        help='''Disable channel CSA if rate exceeded''')
    parser.add_argument('--set_rate',
                        default=_default_set_rate,
                        type=float,
                        help='''Modify pixel trim DAC if rate exceeded''')
    parser.add_argument('--cryo',
                        default=_default_cryo,
                        action='store_true',
                        help='''Flag for cryogenic operation''')
    parser.add_argument('--vdda',
                        default=_default_vdda,
                        type=float, help='''VDDA''')
    parser.add_argument('--normalization',
                        default=_default_normalization,
                        type=float, help='''Seeded threshold scale factor''')
    parser.add_argument('--verbose',
                        default=_default_verbose,
                        action='store_true',
                        help='''Print to screen debugging output''')
    parser.add_argument('--outdir',
                        default='./',
                        help='Output directory'
                        )
    args = parser.parse_args()
    c = main(**vars(args))

