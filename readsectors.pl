#!/usr/bin/perl
#
# readsectors - extract sectors from raw psx disc image
# cab 2017-03-30

use Fcntl qw(SEEK_SET);
use File::Basename;

$sector_size = 2352;

my ($filename, $start, $size) = @ARGV;

die "usage: $0 <filename> <startsector> <size>\n" unless $filename && defined $start && $size;

open IN, "< $filename" or die "open $filename failed: $!\n";
seek IN, hex($start) * $sector_size, SEEK_SET or die "seek failed: $!\n";

($fn, $dirs, $ext) = fileparse($filename, qr/\.[^.]*/);
open OUT, "> $dirs$fn-$start$ext" or die "open output failed: $!\n";

$size = hex($size);

while ($size > 0) {
	$nr = read IN, $buf, $sector_size;
	warn "read less than sector size ($nr)" unless $nr == $sector_size;
	#printf STDERR "MSF %02x:%02x:%02x  mode %d\n", 
	#	unpack "C4", substr($buf, 12, 4);
	print OUT substr($buf, 24, $size < 2048 ? $size : 2048);
	$size -= 2048;
}
close IN;
close OUT;