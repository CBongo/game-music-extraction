#!/usr/bin/perl

# cab 2002-06-12

$fname = shift;

open IN, "< $fname" or die;

read IN, $buf, 0x10000;
close IN;

my $loadaddr = unpack "v", $buf;
my $code = substr $buf, 2;

my ($start, $len) = map {hex} @ARGV;

$len ||= 63;  # default sprite length 

for (my $p = $start; $p < $start + $len; $p += 3) {
  my $row = substr $code, ($p-$loadaddr), 3;
  $bits = unpack "B*", $row;
  $bits =~ s/0+$//;   # trim trailing clear bits
  $bits =~ tr/01/ */;
  printf "%04X %02X %02X %02X   ; %s\n", $p, unpack("C3", $row), $bits;
}
