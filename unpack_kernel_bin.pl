#!/usr/bin/perl
#
# unpack ff7 kernel.bin file
# cab 2017-04-09

use IO::Uncompress::Gunzip qw(gunzip $GunzipError) ;

my (@sections) = (
 'command',
 'attack',
 'savemap',
 'initcharstats',
 'item',
 'weapon',
 'armor',
 'accessory',
 'materia',
 'command-descr',
 'magic-descr',
 'item-descr',
 'weapon-descr',
 'armor-descr',
 'accessory-descr',
 'materia-descr',
 'keyitem-descr',
 'command-names',
 'magic-names',
 'item-names',
 'weapon-names',
 'armor-names',
 'accessory-names',
 'materia-names',
 'keyitem-names',
 'battle-text',
 'summon-names');

my $fn = shift;
open IN, "< $fn" or die "open $fn failed: $!";
foreach $s (@sections) {
  my $n = read IN, $buf, 6;
  warn "short read $n, expected 6\n" unless $n == 6;

  my ($len, $unk, $num) = unpack "v3", $buf;
  printf "unpacking file %d (%s) len %04x unk %04x\n",
    $num, $s, $len, $unk;

  gunzip \*IN => "kdecomp/$s", InputLength => $len
     or warn "gunzip failed: $GunzipError\n";
}
close IN;
