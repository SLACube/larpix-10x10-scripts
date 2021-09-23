import sys
import time
import argparse
import graphs
import larpix
import larpix.io
import larpix.logger
import generate_config

_uart_phase = 0

_default_controller_config=None
_default_logger=False
_default_reset=True

_default_chip_id = 2
_default_io_channel = 1
_default_miso_ds = 0
_default_mosi = 0

_default_clk_ctrl = 1

clk_ctrl_2_clk_ratio_map = {
		0: 2,
		1: 4,
		2: 8,
		3: 16
		}


def power_registers():
	adcs=['VDDA', 'IDDA', 'VDDD', 'IDDD']
	data = {}
	for i in range(1,9,1):
		l = []
		offset = 0
		for adc in adcs:
			if adc=='VDDD': offset = (i-1)*32+17
			if adc=='IDDD': offset = (i-1)*32+16
			if adc=='VDDA': offset = (i-1)*32+1
			if adc=='IDDA': offset = (i-1)*32
			l.append( offset )
		data[i] = l
	return data

def flush_data(controller, runtime=0.1, rate_limit=0., max_iterations=10):
	'''
	Continues to read data until data rate is less than rate_limit

	'''
	for _ in range(max_iterations):
		controller.run(runtime, 'flush_data')
		if len(controller.reads[-1])/runtime <= rate_limit:
			break


arr = graphs.NumberedArrangement()

def get_temp_key(io_group, io_channel):
	return larpix.key.Key(io_group, io_channel, 1)

def get_good_roots(c, io_group, io_channels):
	root_chips = [11, 41, 71, 101]

	good_tile_channel_indices = []
	for n, io_channel in enumerate(io_channels):

		#writing initial config########################################################
		key = larpix.key.Key(io_group, io_channel, 1)
		c.add_chip(key)

		c[key].config.chip_id = root_chips[n]

		c.write_configuration(key, 'chip_id')
		c.remove_chip(key)

		key = larpix.key.Key(io_group, io_channel, root_chips[n])
		c.add_chip(key)
		c[key].config.chip_id = key.chip_id

		c[key].config.enable_miso_downstream = [1,0,0,0]
		c[key].config.enable_miso_differential = [1,1,1,1]
		c.write_configuration(key, 'enable_miso_downstream')

		###############################################################################


		#resetting clocks##############################################################

		c[key].config.enable_miso_downstream=[0]*4
		c[key].config.enable_miso_upstream=[0]*4
		c.write_configuration(key, 'enable_miso_downstream')
		c.write_configuration(key, 'enable_miso_upstream')
		c[key].config.clk_ctrl = _default_clk_ctrl
		c.write_configuration(key, 'clk_ctrl')
		c.io.set_uart_clock_ratio(io_channel, clk_ctrl_2_clk_ratio_map[_default_clk_ctrl], io_group=io_group)

		################################################################################

		#rewriting config
		c[key].config.enable_miso_downstream = [1,0,0,0]
		c[key].config.enable_miso_differential = [1,1,1,1]
		c.write_configuration(key, 'enable_miso_differential')
		c.write_configuration(key, 'enable_miso_downstream')
#		c[key].config.enable_mosi = [1,1,1,1]
#		c.write_configuration(key, 'enable_mosi')


#		c[key].config.enable_miso_upstream = [0,1,0,0]
#		c.write_configuration(key, 'enable_miso_upstream')
		###############################################################################

		#checking
		ok,diff = c.verify_registers([(key,122)], timeout=0.5, n=3)

		if ok:
			good_tile_channel_indices.append(n)
			print('verified root chip ' + str(root_chips[n]))
		else:
			print('unable to verify root chip ' + str(root_chips[n]))

	#checking each connection for every chip
	good_roots = [root_chips[n] for n in good_tile_channel_indices]
	good_channels = [io_channels[n] for n in good_tile_channel_indices]

	print('good root chips: ', good_roots)

	return good_roots, good_channels

def reset_board_get_controller(io_group, io_channels, pacman_version=3):
	#creating controller with pacman io
	c = larpix.Controller()
	c.io = larpix.io.PACMAN_IO(relaxed=True)
	c.io.double_send_packets = True

	if pacman_version == 3:
		vddd = 40605
		c.io.set_reg(0x00024130, 46020) # write to tile 1 VDDA
		c.io.set_reg(0x00024131, vddd) # write to tile 1 VDDD
		c.io.set_reg(0x00024132, 46020) # write to tile 2 VDDA
		c.io.set_reg(0x00024133, vddd) # write to tile 2 VDDD
		c.io.set_reg(0x00024134, 46020) # write to tile 3 VDDA
		c.io.set_reg(0x00024135, vddd) # write to tile 3 VDDD
		c.io.set_reg(0x00024136, 46020) # write to tile 4 VDDA
		c.io.set_reg(0x00024137, vddd) # write to tile 4 VDDD
		c.io.set_reg(0x00024138, 46020) # write to tile 5 VDDA
		c.io.set_reg(0x00024139, vddd) # write to tile 5 VDDD
		c.io.set_reg(0x0002413a, 46020) # write to tile 6 VDDA
		c.io.set_reg(0x0002413b, vddd) # write to tile 6 VDDD
		c.io.set_reg(0x0002413c, 46020) # write to tile 7 VDDA
		c.io.set_reg(0x0002413d, vddd) # write to tile 7 VDDD
		c.io.set_reg(0x0002413e, 46020) # write to tile 8 VDDA
		c.io.set_reg(0x0002413f, vddd) # write to tile 8 VDDD
		c.io.set_reg(0x00000014, 1) # enable global larpix power
		c.io.set_reg(0x00000010, 0b11111111) # enable tiles to be powered

		power = power_registers()
		adc_read = 0x00024001
		for i in power.keys():
			val_vdda = c.io.get_reg(adc_read+power[i][0], io_group=1)
			val_idda = c.io.get_reg(adc_read+power[i][1], io_group=1)
			val_vddd = c.io.get_reg(adc_read+power[i][2], io_group=1)
			val_iddd = c.io.get_reg(adc_read+power[i][3], io_group=1)
			print('TILE',i,
				  '\tVDDA:',(((val_vdda>>16)>>3)*4),
				  '\tIDDA:',(((val_idda>>16)-(val_idda>>31)*65535)*500*0.01),
				  '\tVDDD:',(((val_vddd>>16)>>3)*4),
				  '\tIDDD:',(((val_iddd>>16)-(val_iddd>>31)*65535)*500*0.01))

	if pacman_version == 2:
		_vddd_dac = 0xd2cd # for ~1.8V operation on single chip testboard
		_vdda_dac = 0xd2cd # for ~1.8V operation on single chip testboard
		#_vddd_dac = 0xd8e4 # for ~1.8V operation on 10x10 tile
		#_vdda_dac = 0xd8e4 # for ~1.8V operation on 10x10 tile
		_uart_phase = 0
		print('Setting larpix power...')
		mask = c.io.enable_tile()[1]
		print('tile enabled?:',hex(mask))
		c.io.set_vddd(_vddd_dac)[1]
		c.io.set_vdda(_vdda_dac)[1]
		vddd,iddd = c.io.get_vddd()[1]
		vdda,idda = c.io.get_vdda()[1]
		print('VDDD:',vddd,'mV')
		print('IDDD:',iddd,'mA')
		print('VDDA:',vdda,'mV')
		print('IDDA:',idda,'mA')
		for ch in range(1,5):
			c.io.set_reg(0x1000*ch + 0x2014, _uart_phase)
		print('set phase:',_uart_phase)

	#adding pacman!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
	for io_channel in io_channels:
		c.add_network_node(io_group, io_channel, c.network_names, 'ext', root=True)
	#!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

	#resetting larpix
	c.io.reset_larpix(length=10240)
	for io_channel in io_channels:
		c.io.set_uart_clock_ratio(io_channel, clk_ctrl_2_clk_ratio_map[0], io_group=io_group)
	###################################################################################

	return c

def init_initial_network(c, io_group, io_channels, paths):
	root_chips = [path[0] for path in paths]

	still_stepping = [True for root in root_chips]
	ordered_chips_by_channel = [ [] for io_channel in io_channels  ]

	for ipath, path in enumerate(paths):

		step = 0

		while step < len(path)-1:
			step += 1
			next_key = larpix.key.Key(io_group, io_channels[ipath], path[step])
			prev_key = larpix.key.Key(io_group, io_channels[ipath], path[step-1])

			if prev_key.chip_id in root_chips:
				#this is the first step. need to re-add root chip
				temp_key = get_temp_key(io_group, io_channels[ipath])
				c.add_chip(temp_key)
				c[temp_key].config.chip_id = prev_key.chip_id
				c.write_configuration(temp_key, 'chip_id')
				c.remove_chip(temp_key)
				c.add_chip(prev_key)
				c[prev_key].config.chip_id = prev_key.chip_id
				c[prev_key].config.enable_miso_downstream = arr.get_uart_enable_list(prev_key.chip_id)
				c[prev_key].config.enable_miso_differential = [1,1,1,1]
				c.write_configuration(prev_key, 'enable_miso_downstream')
				c.write_configuration(prev_key, 'enable_miso_differential')
				ordered_chips_by_channel[ipath].append(prev_key.chip_id)
			
			c[prev_key].config.enable_miso_upstream = arr.get_uart_enable_list(prev_key.chip_id, next_key.chip_id)
			c.write_configuration(prev_key, 'enable_miso_upstream')

			temp_key = get_temp_key(io_group, io_channels[ipath])
			c.add_chip(temp_key)
			c[temp_key].config.chip_id = next_key.chip_id
			c.write_configuration(temp_key, 'chip_id')
			c.remove_chip(temp_key)

			c.add_chip(next_key)
			c[next_key].config.chip_id = next_key.chip_id
			c[next_key].config.enable_miso_downstream = arr.get_uart_enable_list(next_key.chip_id, prev_key.chip_id)
			c[next_key].config.enable_miso_differential =[1,1,1,1]
			c.write_configuration(next_key, 'enable_miso_downstream')
			ordered_chips_by_channel[ipath].append(next_key.chip_id)
			
		for chip_ids in ordered_chips_by_channel[ipath][::-1]:
			key = larpix.key.Key(io_group, io_channels[ipath], chip_ids)
			c[key].config.enable_miso_downstream=[0]*4
			c[key].config.enable_miso_upstream=[0]*4
			c.write_configuration(key, 'enable_miso_downstream')
			c.write_configuration(key, 'enable_miso_upstream')
			c[key].config.clk_ctrl = _default_clk_ctrl
			c.write_configuration(key, 'clk_ctrl')
		c.io.set_uart_clock_ratio(io_channels[ipath], clk_ctrl_2_clk_ratio_map[_default_clk_ctrl], io_group=io_group)

	return True

def test_network(c, io_group, io_channels, paths):
	root_chips = [path[0] for path in paths]
	step = 0
	still_stepping = [True for path in paths]
	valid = [True for path in paths]
	while any(still_stepping):
		step += 1

		for ipath, path in enumerate(paths):
			
			if not still_stepping[ipath] or not valid[ipath]:
				continue

			if step > len(path)-1:
				still_stepping[ipath] = False
				continue

			next_key = larpix.key.Key(io_group, io_channels[ipath], path[step])
			prev_key = larpix.key.Key(io_group, io_channels[ipath], path[step-1])

			if prev_key.chip_id in root_chips:
				c[prev_key].config.chip_id = prev_key.chip_id
				c[prev_key].config.enable_miso_downstream = arr.get_uart_enable_list(prev_key.chip_id)
				c[prev_key].config.enable_miso_differential = [1,1,1,1]
				c.write_configuration(prev_key, 'enable_miso_downstream')

			c[prev_key].config.enable_miso_upstream = arr.get_uart_enable_list(prev_key.chip_id, next_key.chip_id)
			c.write_configuration(prev_key, 'enable_miso_upstream')

			c[next_key].config.chip_id = next_key.chip_id
			c[next_key].config.enable_miso_downstream = arr.get_uart_enable_list(next_key.chip_id, prev_key.chip_id)
			c[next_key].config.enable_miso_differential =[1,1,1,1]
			c.write_configuration(next_key, 'enable_miso_downstream')

			if (path[step-1], path[step]) in arr.good_connections:
				#already verified links
				print(next_key, 'already verified')
				continue

			ok, diff = c.verify_registers([(next_key, 122)], timeout=0.5, n=3)
			print(next_key, ok )

			if ok:
				arr.add_good_connection((path[step-1], path[step]))
				continue

			else:
				#planned path to traverse has been interrupted... restart with adding excluded link
				arr.add_onesided_excluded_link((prev_key.chip_id, next_key.chip_id))
				still_stepping[ipath] = False
				valid[ipath] = False

	return all(valid)

def test_chip(c, io_group, io_channel, path, ich, all_paths_copy, io_channels_copy):
	#-loop over directions
	#-check if chip in that direction is in current network
	#---if in network:
	# 	shut off all current misos through existing network, 
	#   re-route through current chip using upstrean command from current chip
	#   read configuration through current chip
	# ** if we can't read the command, then either the upstream from current chip isn't working, or the
	# ** mosi on the current chip isn't working, or the downstream miso on next/current mosi bridge isnt working
	# 
	# to test:
	# - upstream on current or downstream on next:
	#----change register through current configuration
	#----disable miso upstream on current
	#----enable miso downstream on next back through og path
	#----read config through original path, verify register
	#----true: upstream miso works, downstream no
	#----false: upstream miso on current doesn't work
	#----***IF upstream miso doesn't work, we need an additional test
	#	   to make sure that the downstream miso on next works
	#	** test:
	#	   -disable miso downstream from previous path
	#	   -enable downstream miso from next to current
	#		-disbale miso us from current (for good measure, we know it doesnt work)
	#		-read register from chip
	
	chip = path[ich]
	#check if last chip in path
	
	mover_directions = [arr.right, arr.left, arr.up, arr.down]

	for direction in mover_directions:
		next_chip = direction(chip)
		if next_chip <  2: #at the boundary of the board
			continue
		if ich < len(path)-1:
			if next_chip == path[ich+1]: #already know connection works
				continue
		if next_chip == path[ich-1]:
			continue

		if (chip, next_chip) in arr.good_connections: #
			continue

		real_io_channel = -1

		if not(next_chip in path):
			key = larpix.key.Key(io_group, io_channel, next_chip)

			for _ipath, _path in enumerate(all_paths_copy):
				if next_chip in _path:
					real_io_channel = io_channels_copy[_ipath]
					break
			if real_io_channel > 0:

				real_key = larpix.key.Key(io_group, real_io_channel, next_chip)
				try:
					c.add_chip(key)
				except:
					c.remove_chip(key)
					c.add_chip(key)

				c[key].config = c[real_key].config
		else:
			next_index = path.index(next_chip)
			if next_index < ich:
				print('not testing,', chip, next_chip)
				continue
			if next_index == 0:
				#next chip is root chip!
				continue

		## starting test
		next_key = larpix.key.Key(io_group, io_channel, next_chip)
		curr_key = larpix.key.Key(io_group, io_channel, chip)
		#if next_chip in current network
		if real_io_channel > 0:
			next_key = larpix.key.Key(io_group, real_io_channel, next_chip)

		print('starting test of', chip, 'to', next_chip)

		#turn off current downstream misos
		next_ds_backup = c[next_key].config.enable_miso_downstream.copy()
		c[next_key].config.enable_miso_downstream = [0,0,0,0]
		c.write_configuration(next_key, 'enable_miso_downstream')

		if real_io_channel < 0:
		#turn off upstream from previous chip in path
			prev_chip = path[next_index-1]
			prev_key = larpix.key.Key(io_group, io_channel, prev_chip)
			prev_us_backup = c[prev_key].config.enable_miso_upstream.copy()
			c[prev_key].config.enable_miso_upstream = [0,0,0,0]
			c.write_configuration(prev_key, 'enable_miso_upstream')
			
		#at this point the next chip is completely cut off. Now we talk to it through our current chip
		curr_us_backup = c[curr_key].config.enable_miso_upstream.copy()
		c[curr_key].config.enable_miso_upstream = arr.get_uart_enable_list(chip, next_chip)
		c.write_configuration(curr_key, 'enable_miso_upstream')

		
		#if real io > 0, next key is not in network, so we need to make new key in network
		new_next_key = next_key
		if real_io_channel > 0:
			new_next_key = larpix.key.Key(io_group, io_channel, next_chip)

		c[new_next_key].config.enable_miso_downstream = arr.get_uart_enable_list(next_chip, chip)
		c.write_configuration(new_next_key, 'enable_miso_downstream')
		
		#check if we can communicate with it
		ok, diff = c.verify_registers([(new_next_key, 122)], timeout=0.5, n=3) #just reading chip id
		if True:
			if ok:
				print('successfully tested uart', chip, next_chip)
				#everything looks good. Lets cleanup.
				arr.add_good_connection( (chip, next_chip) )
			else:
				print(chip, next_chip, '2 sided connection broken')
				arr.add_onesided_excluded_link((chip, next_chip))

			if True: #cleanup
				c[curr_key].config.enable_miso_upstream = curr_us_backup
				c.write_configuration(curr_key, 'enable_miso_upstream')

				if real_io_channel < 0:
					c[prev_key].config.enable_miso_upstream = prev_us_backup
					c.write_configuration(prev_key, 'enable_miso_upstream')

				c[next_key].config.enable_miso_downstream = next_ds_backup
				c.write_configuration(next_key, 'enable_miso_downstream')

				#test configs
				ok, diff = c.verify_registers([(next_key, 122), (curr_key, 122)], timeout=0.5, n=3)
				if real_io_channel < 0:
					ok2, diff2 = c.verify_registers([(prev_key, 122)], timeout=0.5, n=3)
					ok = (ok and ok2)

				if ok:
					continue
				else:
					if real_io_channel < 0:
						c[prev_key].config.enable_miso_upstream = prev_us_backup
						c.write_configuration(prev_key, 'enable_miso_upstream')

					c[curr_key].config.enable_miso_upstream = curr_us_backup
					c.write_configuration(curr_key, 'enable_miso_upstream')

					c[next_key].config.enable_miso_downstream = next_ds_backup
					c.write_configuration(next_key, 'enable_miso_downstream')

					ok, diff = c.verify_registers([(next_key, 122), (curr_key, 122)], timeout=0.5, n=3)
					if real_io_channel < 0:
						ok2, diff2 = c.verify_registers([(prev_key, 122)], timeout=0.5, n=3)
						ok = (ok and ok2)

					if ok:
						continue
					else:
						c[next_key].config.enable_miso_downstream = next_ds_backup
						c.write_configuration(next_key, 'enable_miso_downstream')

						c[curr_key].config.enable_miso_upstream = curr_us_backup
						c.write_configuration(curr_key, 'enable_miso_upstream')

						if real_io_channel < 0:

							c[prev_key].config.enable_miso_upstream = prev_us_backup
							c.write_configuration(prev_key, 'enable_miso_upstream')


						ok, diff = c.verify_registers([(next_key, 122), (curr_key, 122)], timeout=0.5, n=3)

						if real_io_channel < 0:
							ok2, diff2 = c.verify_registers([(prev_key, 122)], timeout=0.5, n=3)
							ok = (ok and ok2)

						continue
	return True
	


def main(tile_number, generate_configuration, tile_id, pacman_version):
	tile_name = 'id_' + tile_id 
	io_group = 1
	io_channels = [ 1 + 4*(tile_number - 1) + n for n in range(4)]
	#io_channels = [1, 2, 4]
	c = reset_board_get_controller(io_group, io_channels, pacman_version)

	root_chips, io_channels = get_good_roots(c, io_group, io_channels)
	print(root_chips)
	c = reset_board_get_controller(io_group, io_channels, pacman_version)

	#need to init whole network first and write clock frequency, then we can step through and test

	existing_paths = [ [chip] for chip in root_chips  ]

	#initial network
	paths = arr.get_path(existing_paths)
	print('path including', sum(  [len(path) for path in paths] ), 'chips' )

	#bring up initial network and set clock frequency
	init_initial_network(c, io_group, io_channels, paths)
	#test network to make sure all chips were brought up correctly
	ok = test_network(c, io_group, io_channels, paths)

	while not ok:
		c = reset_board_get_controller(io_group, io_channels, pacman_version)

		existing_paths = [ [chip] for chip in root_chips  ]

		#initial network
		paths = arr.get_path(existing_paths)
		print('path inlcuding', sum(  [len(path) for path in paths] ), 'chips' )

		#bring up initial network and set clock frequency
		init_initial_network(c, io_group, io_channels, paths)

		#test network to make sure all chips were brought up correctly
		ok = test_network(c, io_group, io_channels, paths)

	#existing network is full initialized, start tests
	chips_to_test = [] #keeps track of chips that weren't tested during this run for whatever reason

	##
	##
	print('\n***************************************')
	print(  '***Starting Test of Individual Chips***')
	print(  '***************************************\n')
	##
	##

	for ipath, path in enumerate(paths):
		for ich in range(len(path)):
			ok = test_chip(c, io_group, io_channels[ipath], path, ich, paths.copy(), io_channels.copy())
			#only returns whether or not a test was performed, not the test status
			if not ok:
				chips_to_test.append(path[ich])

	#chips which are untested
	missing_chips = [chip for chip in arr.all_chips() if not any( [chip in path for path in paths] ) ]
	for chip in missing_chips:
		chips_to_test.append(chip)

	print('untested', chips_to_test)
	print('bad (one-way) links: ', arr.excluded_links)
	print('tested', len(arr.good_connections) + len(arr.excluded_links), 'uarts')

	######
	##generating config file
	paths = arr.get_path(existing_paths)
	_name = 'tile-' + tile_name + "-autoconfig"
	if generate_configuration:
		print('writing configuration', _name + '.json, including', sum(  [len(path) for path in paths] ), 'chips'  )
		generate_config.main(_name, io_group, root_chips, io_channels, arr.excluded_links, arr.excluded_chips)

	return

if __name__ == '__main__':
	parser = argparse.ArgumentParser()
	parser.add_argument('--tile_number', default=1, type=int, help='''Tile number being tested''')
	parser.add_argument('--pacman_version', default=3, type=int, help='''Pacman version in use''')
	parser.add_argument('--tile_id', default='1', type=str, help='''Tile number being tested''')
	parser.add_argument('--generate_configuration', default=True, type=bool, help='''Flag to write configuration file with name tile-(tile number).json''')
	args = parser.parse_args()
	c = main(**vars(args))