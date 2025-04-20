#!/usr/bin/perl

open IN, "< fzero.smc" or die;
seek IN, 0x008000 + 0x200, 0;
#open OUT, "> fzerospc.bin" or die;
for (;;) {
  read IN, $buf, 4;
  ($len, $addr) = unpack "v2", $buf;
  printf STDERR  "len=%04x, addr=%04x\n", $len, $addr;
  last unless $len > 0;
  open OUT, sprintf "> fzspc-%04x-%d.bin", $addr, $c++;
  #print OUT pack("v2", $len, $addr);
  last unless $len > 0;
  read IN, $buf, $len;
  print OUT $buf;
}
close OUT;
close IN;
