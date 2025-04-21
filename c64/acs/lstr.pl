#!/usr/bin/perl

# cab 2002-06-07

$fname = shift;

open IN, "< $fname" or die;

read IN, $buf, 0x10000;
close IN;

my $loadaddr = unpack "v", $buf;
my $code = substr $buf, 2;

my ($start) = map { hex } @ARGV;

printf "%04X ", $start;

my ($len) = unpack "C", substr $code, ($start-$loadaddr), 1;

my @chars = unpack "C$len", substr $code, ($start-$loadaddr+1), $len;
printf "     %02X " . (" %02X" x $len) . "\t; \"%s\"",
      	$len, @chars, join("", map { chr &scrn2asc($_) } @chars);

print "\n";

sub scrn2asc {
  my $in = shift;
  $in &= 0x7f;  # reverse = normal
  $in += 0x40 if $in < 0x20;
  return $in;
}
