#!/bin/sh -fx
python extract_akao.py ct.yaml ../snes/ct/chrono.smc --song 0x15
python extract_akao.py ff2.yaml ../snes/ff2/ff2.smc --song 0x0d
python extract_akao.py ff3.yaml ../snes/ff3/f_fan3.fig --song 0x6
python extract_akao.py ff5.yaml ../snes/ff5/ff5e.smc --song 0x2b
python extract_akao.py som.yaml ../snes/som/som1.smc --song 0xd
python extract_akao.py sd3.yaml ../snes/sd3/seiken3e.smc --song 0xc
