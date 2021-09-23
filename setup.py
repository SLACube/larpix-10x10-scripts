#!/usr/bin/env python3

import setuptools

VER = "0.1.0"

setuptools.setup(
    name="larpix-10x10-scripts",
    version=VER,
    author="Lawrence Berkeley National Laboratory",
    author_email="roberto@lbl.gov",
    description="LArPix Anode Tile Quality Control scripts",
    url="https://github.com/larpix/larpix-10x10-scripts",
    packages=['larpix_qc'],
    package_dir={'larpix_qc': 'larpix_qc'},
    install_requires=["numpy", "pyyaml", "matplotlib", "h5py", "tqdm", "larpix-control"],
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: by End-User Class :: Developers",
        "Operating System :: Grouping and Descriptive Categories :: OS Independent (Written in an interpreted language)",
        "Programming Language :: Python",
        "Topic :: Scientific/Engineering :: Physics"
    ],
    scripts=['larpix_qc/pedestal_qc.py','larpix_qc/leakage_qc.py','larpix_qc/selftrigger_qc.py',
             'larpix_qc/threshold_qc.py','larpix_qc/map_uart_links_test.py',
             'larpix_qc/plot_leakage.py', 'larpix_qc/plot_pedestal.py', 'larpix_qc/base.py',
             'larpix_qc/plot_selftrigger.py', 'larpix_qc/generate_report.py'],
    python_requires='>=3.6',
)
