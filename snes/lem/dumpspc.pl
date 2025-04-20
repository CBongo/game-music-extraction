#!/usr/bin/perl

open IN, "< lemmings.smc" or die;
seek IN, 8*0x8000 + ( 0xe7d7 - 0x8000), 0;
read IN, $len, 2;
read IN, $addr, 2;
read IN, $buf, unpack("v", $len);
close IN;
printf STDERR  "len=%04x, addr=%04x, buf=%04x\n", unpack("vv", $len . $addr), length $buf;
open OUT, "> lemspc.bin" or die;
print OUT pack("v", length($buf)), $addr, $buf, pack("v", 0), $addr;
close OUT;
