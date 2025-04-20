#!/usr/bin/perl

open IN, "< mario.smc" or die;
seek IN, 0x070000 + 0x200, 0;
while(! eof IN){
	my $start = &o2s(tell IN);
	read IN, $len, 2;
	read IN, $addr, 2;
	printf STDERR  "start=%s, addr=%04x, len=%04x\n",
	    $start, unpack("vv", $addr . $len);
	seek IN, unpack("v", $len), 1;
}
close IN;


sub o2s {
  my ($in) =@_;
  $in -= 0x200;
  my ($bank, $offs) = ($in / 0x8000, $in % 0x8000 + 0x8000);
  return sprintf "%02X/%04X", $bank, $offs;
}
