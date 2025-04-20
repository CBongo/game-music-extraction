#!/usr/bin/perl

open IN, "< chrono.smc" or die;
seek IN, 0x0724c3 + 0x200, 0;
read IN, $len, 2;
read IN, $buf, unpack("v", $len);
close IN;
printf STDERR  "len=%04x, buf=%04x\n", unpack("v", $len), length $buf;
open OUT, "> ctspc.bin" or die;
print OUT pack("vv", length($buf), 0x200), $buf, pack("vv", 0, 0x200);
close OUT;
