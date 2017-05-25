#!/usr/bin/perl
#
# display fat type 2 from chrono cross psx
# cab 2017-05-18 or so
#

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
