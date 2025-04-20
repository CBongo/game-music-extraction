#!/usr/bin/perl

open IN, "< GRADIUS3.ZST" or die;
seek IN, 0x31013, 0;
read IN, $buf, 0x3400;
close IN;
open OUT, "> g3spc.bin" or die;
print OUT pack("vv", length($buf), 0x400), $buf, pack("vv", 0, 0x400);
close OUT;
