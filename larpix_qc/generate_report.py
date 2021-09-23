#!/usr/bin/env python3
"""
Report generation script.
To generate the HTML file:

pandoc -s -f markdown -t html5 -o report.html -c report_style.css report.md

"""

import argparse
import json

_default_rate_threshold = 2000
_default_mean_threshold = 50
_default_std_threshold = 50

def main(log_qc,
         rate_threshold=_default_rate_threshold,
         mean_threshold=_default_mean_threshold,
         std_threshold=_default_std_threshold):
    with open(log_qc,'r') as log_qc_file:
        json_log = json.load(log_qc_file)

    report_file = open('report.md', 'w')
    print("# Quality control report", file=report_file)

    for tile in json_log.keys():
        print("## Tile %s" % tile, file=report_file)
        for stage in json_log[tile]:
            print("### %s stage" % stage, file=report_file)
            for test in json_log[tile][stage]:

                if "Leakage" in test:
                    print("#### %s" % test, file=report_file)
                    print("\nChannels with half-dynamic trigger rate above %i Hz\n" % rate_threshold,
                          file=report_file)
                    print("""| Chip | Channel | Trigger Rate [Hz] |
| --- | --- | --- |""", file=report_file)
                    for chip_key in json_log[tile][stage][test]:
                        for channel in json_log[tile][stage][test][chip_key]:
                            leakage_rate = json_log[tile][stage][test][chip_key][channel]
                            if leakage_rate > rate_threshold:
                                print("| %s | %s | %.2f |" % (chip_key, channel, leakage_rate),
                                    file=report_file)
                    print("\n![](leakage.png)\n", file=report_file)

                if "Pedestal mean" in test:
                    print("#### %s" % test, file=report_file)
                    print("\nChannels with mean ADC above %i and standard deviaton ADC above %i\n" % (mean_threshold, std_threshold),
                          file=report_file)
                    print("""| Chip | Channel | Mean ADC | Standard Deviation ADC |
| --- | --- | --- | --- |""", file=report_file)
                    for chip_key in json_log[tile][stage][test]:
                        for channel in json_log[tile][stage][test][chip_key]:
                            mean = json_log[tile][stage][test][chip_key][channel]
                            std = json_log[tile][stage]["Pedestal std"][chip_key][channel]
                            if mean > mean_threshold and std > std_threshold:
                                print("| %s | %s | %.2f | %.2f |" % (chip_key, channel, mean, std),
                                    file=report_file)
                    print("\n![](pedestal.png)\n", file=report_file)
    return True

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--log_qc',
                        type=str,
                        help='''JSON QC log file''')
    parser.add_argument('--rate_threshold',
                        default=_default_rate_threshold,
                        type=str,
                        help='''Half dynamic range trigger rate threshold''')
    parser.add_argument('--mean_threshold',
                        default=_default_mean_threshold,
                        type=str,
                        help='''Mean ADC threshold''')
    parser.add_argument('--std_threshold',
                        default=_default_std_threshold,
                        type=str,
                        help='''Standard deviation ADC threshold''')
    args = parser.parse_args()
    c = main(**vars(args))