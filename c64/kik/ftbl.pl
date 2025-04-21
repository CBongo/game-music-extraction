#!/usr/bin/perl

@notenames = qw(C C# D D# E F F# G G# A A# B);

open IN, "< kik1_1f80.bin" or die;
read IN, $buf, 0x80;
close IN;

@b = unpack "C*", $buf;

for ($i=0; $i < 0x40; $i++) {
  printf "%02x %3s  %02x%02x  %d\n", $i, &notename($i), $b[$i+0x40], $b[$i], $b[$i+0x40]*256+$b[$i];
}

sub notename {
  my $n = shift;
  return "" unless $b[$n] || $b[$n+0x40];  # zero = ""
  $n -= 2;
  my $octave = int($n/12) + 2;
  my $notename = $notenames[$n % 12];
  return $notename . $octave;
}
