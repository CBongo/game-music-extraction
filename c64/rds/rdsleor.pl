#!/usr/bin/perl

open IN, "< rdsloader.prg" or die;
read IN, $buf, 2;  # skip start addr
read IN, $buf, 0x1c00;
close IN;

for ($x = 0x75; $x > 0; $x--) {
	$b = unpack "C", substr($buf, $x-1, 1);
	$a = ($a ^ $b) << 1;
	$a++ if $c;
	if ($a > 0xff) {
		$a &= 0xff;
		$c = 1;
	} else {
		$c = 0;
	}
	printf "b:%02x a:%02x c:%d\n", $b, $a, $c;
}
printf "final value: %02x\n", $a;
	
	