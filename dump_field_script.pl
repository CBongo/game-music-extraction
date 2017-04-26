#!/usr/bin/perl
#
#

use Fcntl qw(SEEK_SET);

my $fn = shift;
open IN, "< $fn" or die "open $fn failed: $!\n";
read IN, $buf, 28;
my (@ptrs) = unpack "V7", $buf;
read IN, $buf, 16;
my ($unk1, $nEnt, $visEnt, $sOffset, $nExtraOffsets) =
  unpack "vc2v2", $buf;
read IN, $buf, 8;
my ($creator) = unpack "A8", $buf;
read IN, $buf, 8; 
my ($fieldname) = unpack "A8", $buf;
read IN, $buf, 8 * $nEnt;
my (@entNames) = unpack "A8" x $nEnt, $buf;
read IN, $buf, 4 * $nExtraOffsets;
my (@extraOffsets) = unpack "V$nExtraOffsets", $buf;
for ($i=0;$i < $nEnt; $i++) {
  read IN, $buf, 64;
  $entScripts[$i] = [ &dedup(unpack "v32", $buf) ]; 
}
my $hLen = tell IN;
for ($i=0; $i<@extraOffsets; $i++) {
  seek IN, $extraOffsets[$i] + 28, SEEK_SET;
  read IN, $buf, 8;
  if (substr($buf,0,4) eq 'AKAO') {
    $extraIsAKAO[$i] = 1;
    $extraAKAOid[$i] = unpack "v", substr($buf, 4, 2);
    $extraAKAOlen[$i] = unpack "v", substr($buf, 6, 2);
    $extraAKAOlen[$i] += 0x10;
  }
}
close IN;

printf "hdr size: %x\n", $hLen;
print "hdr ptrs:", join(",", map {sprintf "%08x", $_} @ptrs), "\n";
printf "u1=%04x nent=%d visent=%d\n", $unk1, $nEnt, $visEnt;
printf "string offset=%04x  nextra=%d\n", $sOffset, $nExtraOffsets;
printf "creator=%s  field name=%s\n", $creator, $fieldname;
print  "extra offsets: ";
for ($i=0; $i < @extraOffsets; $i++) {
  printf "%x", $extraOffsets[$i];
  if ($extraIsAKAO[$i]) {
     printf '(AKAO %02X)', $extraAKAOid[$i];
  }
  print "," unless $i + 1 == @extraOffsets;
}
print  "\nentities:\n";
for ($i=0; $i < $nEnt; $i++) {
  printf "%3d %-8s %s\n", $i, $entNames[$i], join(",", map {sprintf "%x", $_} @{$entScripts[$i]}), "\n";
}

sub dedup {
  my $i = @_;
  #print "deduping: ", join(",", map {sprintf "%x", $_} @_), "\n";
  #print "initial i=$i\n";
  while (--$i > 0) {
     #printf "loop: %d %x %x\n", $i, $_[$i], $_[$i-1];
     last if $_[$i] != $_[$i-1];
  }
  #print "final i=$i\n";
  my (@out) = @_[0..$i];
  #print "deduping out: ", join(",", map {sprintf "%x", $_} @out), "\n";
  return @out;
}
