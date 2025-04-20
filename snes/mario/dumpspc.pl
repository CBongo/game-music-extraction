#!/usr/bin/perl

open IN, "< mario.smc" or die;
seek IN, 0x070000 + 0x200, 0;
read IN, $len, 2;
read IN, $addr, 2;
read IN, $buf, unpack("v", $len);
close IN;
printf STDERR  "len=%04x, addr=%04x, buf=%04x\n", unpack("vv", $len . $addr), length $buf;
open OUT, "> mariospc.bin" or die;
print OUT pack("v", length($buf)), $addr, $buf, pack("v", 0), $addr;
close OUT;
