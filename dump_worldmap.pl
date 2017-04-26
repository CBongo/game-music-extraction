#!/usr/bin/perl
#
#

use Fcntl qw(SEEK_SET);

my $fn = shift;
open IN, "< $fn" or die "open $fn failed: $!\n";
read IN, $buf, 4;
my ($sCount) = unpack "V", $buf;
read IN, $buf, 4 * $sCount;
my (@sOffsets) = unpack "V$sCount", $buf;
for ($i=0; $i<@sOffsets; $i++) {
  seek IN, $sOffsets[$i] + 4, SEEK_SET;
  read IN, $buf, 8;
  if (substr($buf,0,4) eq 'AKAO') {
    $extraIsAKAO[$i] = 1;
    $extraAKAOid[$i] = unpack "v", substr($buf, 4, 2);
    $extraAKAOlen[$i] = unpack "v", substr($buf, 6, 2);
    $extraAKAOlen[$i] += 0x10;
  }
}
close IN;

print "section count: $sCount\n";
for ($i=0; $i < @sOffsets; $i++) {
  printf "section %d (\@%x)", $sOffsets[$i];
  if ($extraIsAKAO[$i]) {
     printf ' (AKAO %02X)', $extraAKAOid[$i];
  }
  print "\n";
}
