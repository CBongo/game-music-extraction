#!/usr/bin/perl

open ROM, "< lemmings.smc" or die;
open OUT, "> lemmus.hex" or die;

seek ROM, &s2o(0x08d4c7), 0;

read ROM, $buf, 0x2d * 2;
@sptr = map { $_ + 0x080000 } unpack "v*", $buf;

for ($i = 0; $i < 0x1c; $i++) {
    read ROM, $buf, 7;
    my ($samp, $tbl) = unpack "v2", substr($buf, 3, 4);
    my ($song) = &unpack3(substr($buf, 0, 3));
    push @sdata, $song;
    push @samp, $samp + 0x080000;
    push @tbl, $tbl + 0x080000;
}

# spc data start addr
read ROM, $buf, 3;

for ($i = 0; $i < 8; $i++) {
  read ROM, $buf, 4;
  my ($gsamp, $gdata) = unpack "v*", $buf;
  push @gsamp, $gsamp + 0x080000;
  push @gdata, $gdata + 0x080000;
}

for ($i = 0; $i < @sdata; $i++) {
  printf OUT "  %02x: data %06x  samp %06x  tbl %06x  (",
	 $i, $sdata[$i], $samp[$i], $tbl[$i];
  my ($addr) = 0x08d521 + $i * 7;
  my $first = 1;
  for ($j = 0; $j < @sptr; $j++) {
    if ($sptr[$j] == $addr) {
        print OUT "," unless $first;
	printf OUT "%02x", $j;
	$first = 0;
    }
  }
  print OUT ")\n";
}
print OUT "\n";
for ($i = 0; $i < @gdata; $i++) {
    printf OUT "  %02x:              samp %06x  tbl %06x\n",
	 $i, $gsamp[$i], $gdata[$i];
}

for ($song = 0; $song < @gdata; $song++) {
  my (@sampptrs, $gblock);
  print OUT "\n";
  printf OUT "Global Set %02x:\n", $song;
  @sampptrs = &readsamp($gsamp[$song]);
  print OUT "  Samples: ", join(' ', map { sprintf "%06x", $_ } @sampptrs), "\n";

  ($gblock) = &readblocks($gdata[$song], 1);
  print OUT "  TBL block:\n";
  &hexdump(@{$gblock});
}

print OUT "\n";

for ($song = 0; $song < @sdata; $song++) {
  my (@sampptrs, @sblocks, $tblock);
  print OUT "\n";
  printf OUT "Song %02x:\n", $song;
  @sampptrs = &readsamp($samp[$song]);
  print OUT "  Samples: ", join(' ', map { sprintf "%06x", $_ } @sampptrs), "\n";

  ($tblock) = &readblocks($tbl[$song], 1);
  @sblocks  = &readblocks($sdata[$song], 0);
  print OUT "  TBL block:\n";
  &hexdump(@{$tblock});
  print OUT "\n  Song data blocks:";
  foreach $block (@sblocks) {
    print OUT "\n";
    &hexdump(@{$block});
  }
}

close OUT;
close ROM;

sub readsamp {
  my ($addr) = @_;
  my ($buf, @sampptrs);

  seek ROM, &s2o($addr), 0;
  while (1) {
    read ROM, $buf, 3;
    my ($s) = unpack3($buf);
    last if $s % 0x10000 == 0;
    push @sampptrs, $s;
  }
  return @sampptrs;
}
sub readblocks {
  my ($addr, $single) = @_;
  my ($buf, @blocks);

  seek ROM, &s2o($addr), 0;
  while (1) {
    read ROM, $buf, 4;
    my ($len, $addr) = unpack "v*", $buf;
    last if $len == 0;
    read ROM, $buf, $len;
    push @blocks, [ $addr, $buf ]; 
    last if $single;
  }
  return @blocks;
}

sub hexdump {
  my ($addr, $data) = @_;

  for (my $p = 0; $p < length $data; $p += 16) {
    printf OUT "    %04x: ", $addr + $p;
    print OUT join(' ', map {sprintf "%02x", ord }
			 split //, substr($data, $p, 8)),
	 ' ', join(' ', map {sprintf "%02x", ord }
			 split //, substr($data, $p + 8, 8)), "\n";
  }
}

sub s2o {
  my ($bank, $offs) = (int($_[0] / 0x10000), $_[0] % 0x10000);
  return $bank * 0x8000 + $offs - 0x8000;
}
sub unpack3 {
  my ($lo, $mid, $hi) = map { ord } split //, $_[0];
  return $lo + 0x100 * $mid + 0x10000 * $hi;
}
