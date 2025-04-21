#!/usr/bin/perl

$fname = shift;
open IN, "< $fname" or die;

read IN, $buf, 0x2000;
close IN;

my $loadaddr = unpack "v", $buf;
my $code = substr $buf, 2;

for ($a = 0; $a < length $code; $a += 0x10) {
	my @bytes = unpack "C16", substr($code, $a, 0x10);
	my $format = "%04X";
	for (my $c = 0; $c < @bytes; $c++) {
		$format .= " " if ($c % 4 == 0);
		$format .= " %02X";
	}
	printf "$format\n", $a + $loadaddr, @bytes;
}
