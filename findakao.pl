#!/usr/bin/perl

use Fcntl qw(SEEK_SET);

$sector_size = 0x930;

$fn = shift or die "usage: $0 <filename>\n";

open IN, "< $fn" or die "open failed: $!\n";
for ($s=0; !eof IN; $s++) {
	last unless seek IN, $s * $sector_size + 24, SEEK_SET;
	read IN, $buf, 8;
	if (substr($buf,0,4) eq 'AKAO') {
		$aid = unpack "v", substr($buf, 4, 2);
		$alen = unpack "v", substr($buf, 6, 2);
		printf "%06x AKAO id %4x  len %4x\n", $s, $aid, $alen;
	} else {
		#printf "%06x no\n", $s;
	}
}