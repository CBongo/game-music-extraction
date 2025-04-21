#!/usr/bin/perl
#

die "usage: $0 <filename>\n" unless defined($f = shift);

open IN, "< $f" or die "open failed: $!\n";

read IN, $buf, 2;
$startaddr = unpack "v", $buf;
printf "Start address: %04x\n", $startaddr;

while (!eof IN) {
	# 8 chars per row
	read IN, $cbuf, 8*8;
	for ($i = 0; $i < 8; $i++) {
		for ($j = 0; $j < 8; $j++) {
			$b = unpack "B8", substr($cbuf, $j * 8 + $i, 1);
			$b =~ tr/01/ #/;
			print "$b ";
		}
		print "\n";
	}
	print "\n";
}
