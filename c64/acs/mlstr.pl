#!/usr/bin/perl

# cab 2002-06-07, -19

$fname = shift;

open IN, "< $fname" or die;

read IN, $buf, 0x10000;
close IN;

my $loadaddr = unpack "v", $buf;
my $code = substr $buf, 2;

my ($start) = map { hex } @ARGV;

while (1) {
  printf "%04X ", $start;

  my ($row, $col, $len) = unpack "C3", substr $code, ($start-$loadaddr), 3;

  last if $row == 0xff;

  my @chars = unpack "C$len", substr $code, ($start-$loadaddr+3), $len;
  printf "%02X %02X %02X " . (" %02X" x $len) . "\t; (%d,%d)\"%s\"",
        	$row, $col, $len, @chars, $row, $col,
		join("", map { chr &scrn2asc($_) } @chars);

  print "\n";
  $start += 3 + $len;
}
printf "FF\n";

sub scrn2asc {
  my $in = shift;
  $in &= 0x7f;  # reverse = normal
  $in += 0x40 if $in < 0x20;
  return $in;
}
