#!/bin/sh -x

f=ff7us-disc1.bin

./readsectors.pl $f 000db 75af0
./readsectors.pl $f 001c7 c800
./readsectors.pl $f 001e0 2000
./readsectors.pl $f 001e4 3d4f0
./readsectors.pl $f 0025f 2000
./readsectors.pl $f 0d6d8 14dbb
./readsectors.pl $f 00b36 1049b
./readsectors.pl $f 00631 9ab0
./readsectors.pl $f 004d3 111bb
./readsectors.pl $f 00573 13e21
./readsectors.pl $f 1efa6 1774
./readsectors.pl $f 1efa9 f414
./readsectors.pl $f 0027f 8ea9
./readsectors.pl $f 009c4 36f3
./readsectors.pl $f 0076c 7a6d
./readsectors.pl $f 007ad 855a
