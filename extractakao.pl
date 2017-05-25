#!/usr/bin/perl

use Fcntl qw(SEEK_SET);

$sector_size = 2352;

$fn = shift or die "usage: $0 <filename>\n";

open IN, "< $fn" or die "open failed: $!\n";

while (<>) {
	my ($s, $id, $len) = map {hex} /^(\S+) AKAO id +(\S+)  len +(\S+)/;
	#printf "%x %x %x\n", $s, $id, $len;
	next unless $len > 0;
	
	seek IN, $s * $sector_size, SEEK_SET;
	my $outfn = sprintf "akao/%02x-%05x.bin", $id, $s;
	open OUT, "> $outfn" or warn "open $outfn failed: $!\n";
	while ($len > 0) {
		read IN, $buf, ($len > 0x800 ? $sector_size : $len + 24);
		print OUT substr($buf, 24, 0x800);
		$len -= 0x800
	}
	close OUT;			
}
close IN;