#!/usr/bin/perl

use Fcntl qw(SEEK_SET);

my $fn = shift;
open IN, "< $fn" or die "open $fn failed: $!\n";
if ($fn =~ /TXZ/) {
  # world map file
  read IN, $buf, 4;
  ($nExtraOffsets) = unpack "V", $buf;
  read IN, $buf, 4 * $nExtraOffsets;
  (@extraOffsets) = unpack "V$nExtraOffsets", $buf;
  # correct for header
  foreach (@extraOffsets) {
    $_ += 4;
  }
} else {
  # assume field script DAT file
  read IN, $buf, 28;
  read IN, $buf, 16;
  ($unk1, $nEnt, $visEnt, $sOffset, $nExtraOffsets) =
    unpack "vc2v2", $buf;
  read IN, $buf, 8;
  read IN, $buf, 8; 
  read IN, $buf, 8 * $nEnt;
  read IN, $buf, 4 * $nExtraOffsets;
  (@extraOffsets) = unpack "V$nExtraOffsets", $buf;
  # correct for header
  foreach (@extraOffsets) {
    $_ += 28;
  }
}
  for ($i=0; $i<@extraOffsets; $i++) {
    seek IN, $extraOffsets[$i], SEEK_SET;
    read IN, $buf, 8;
    if (substr($buf,0,4) eq 'AKAO') {
      $extraIsAKAO[$i] = 1;
      $extraAKAOid[$i] = unpack "v", substr($buf, 4, 2);
      $extraAKAOlen[$i] = unpack "v", substr($buf, 6, 2);
      $extraAKAOlen[$i] += 0x10;  # len excludes header
    }
  }

print  "extra AKAO offsets:\n";
for ($i=0; $i < @extraOffsets; $i++) {
  next unless $extraIsAKAO[$i];
  $outfn = &mkfname($i, $extraAKAOid[$i]);
  next if -f $outfn;
  printf "offset %x id %04x len %04x ", $extraOffsets[$i], $extraAKAOid[$i], $extraAKAOlen[$i];
  seek IN, $extraOffsets[$i], SEEK_SET;
  #if ($i + 1 < @extraOffsets) {
    #$alen = $extraOffsets[$i+1]-$extraOffsets[$i];
  #} else {
    #$alen = 0x200000; # read to end - just make it big enough
  #}
  $alen = $extraAKAOlen[$i];
  $nr = read IN, $buf, $alen;
  printf "(got %x of %x)\n", $nr, $alen;
  open OUT, "> akao/$outfn" or warn "open failed: $!\n";
  print OUT $buf;
  close OUT;
}

sub mkfname {
  my ($i, $id) = @_;

  my $out = $fn;
  $out =~ s/^.*\///;
  $out =~ s/\..+$//;
  return scalar sprintf("%02x.bin", $id);
}
