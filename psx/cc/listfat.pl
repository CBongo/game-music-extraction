#!/usr/bin/perl
#
# display fat type 2 from chrono cross psx
# cab 2017-05-18 or so
#

# more info from chronocompendium:
#
# The Table of Contents (hereafter TOC) is literally the most important
# file to work with for purposes of Chrono Cross modification. It is
# essentially a giant pointer table that the game engine uses to determine
# where every file begins and ends, and occupies 12 sectors beginning at
# sector 24 (offset 0xc000 in an ISO, offset 0xdcc8 in a BIN).
#
# The TOC entries have a four byte stride and can be presented thus:
#
# SS SS FS BB
# 
# Where...
# 
#    S = "Logical Sector"; 2.5-byte little-endian pointer indicating the sector boundary on which the file begins.
#    F = "Flag"; the first bit of this nybble will be set if and only if this TOC entry is a duplicate of the next real record.
#    BB = Number of zeroed buffer bytes between the end of the file and the next sector boundary, divided by 8.
#
# For instance, the entry for file 0001 is 4E 01 00 F7. To find the
# starting location of this file in a CD image, we first reverse the
# first 2.5 bytes (yielding 0x0014e) then multiply the result by 2048
# for an ISO image (= 0xa7000) or by 2354 for a BIN (= 0xbff3c, then add
# an additional 24 to account for the sector header, giving a final
#  total of 0xbff54). 

$fn = shift or die "usage: $0 <filename>\n";

open IN, "< $fn" or die "open failed: $!\n";

read IN, $buf, 4;  # prime the pump
until (eof IN) {
  my (@b) = unpack "C4", $buf;
  my ($offs, $sizeflag, $sizemod) = &parse_entry(@b);
  last if $offs == 0;
  
  read IN, $nextbuf, 4;
  my (@nb) = unpack "C4", $nextbuf;
  my ($nextoffs) = &parse_entry(@nb);
  
  my $size = $sizeflag ? 0 : ($nextoffs - $offs) * 2048 - $sizemod;
  
  printf "%04x ", $line++;
  printf join(" ", ("%02x") x 4), @b;
  printf "   sector %6x  size %8x\n", $offs, $size;
  
  $buf = $nextbuf;
}
close IN;

sub parse_entry {
	my $offs = $_[0] + ($_[1] << 8) + ($_[2] << 16);
	my $sizeflag = $offs & 0x800000;
	$offs &= 0x7FFFFF;
	my $sizemod = $_[3] << 3;
	return $offs, $sizeflag, $sizemod;
}
