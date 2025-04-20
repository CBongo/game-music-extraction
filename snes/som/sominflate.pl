#!/usr/bin/perl

# decompress secret of mana code
# chris bongaarts  25 Mar 2001

open ROM, "< som1.smc" or die;

seek ROM, &s2o(0x077c00), 0;
read ROM, $buf, 4;

($shiftbyte, $end) = unpack "Cxn", $buf;
$shiftcount = 5 - $shiftbyte;
$shiftand   = 2 ** $shiftcount - 1;

read ROM, $buf, 0x83fc; # this ought to be enough
close ROM;

$out = "\0" x $end;
$src = $dest = 0;

while ($dest < $end) {
  my ($c1) = unpack "C", substr($buf, $src++, 1);
  if ($c1 < 0x80) {
    substr($out, $dest, $c1+1) = substr($buf, $src, $c1+1);
    $src  += $c1 + 1;
    $dest += $c1 + 1;
  } else {
    my ($c2) = unpack "C", substr($buf, $src++, 1);
    my $offset = ($c1 & 0x7f & $shiftand) * 0x100 + $c2 + 1;
    my $len = (($c1 & 0x7f) >> $shiftcount) + 2;
      #printf "c1=%02x, c2=%02x, offs=%04x, len=%04x\n", $c1, $c2, $offset, $len;
    &mvn($len, $dest-$offset, $dest);
    $dest += $len + 1;
  }
  #printf "src=%04x dest=%04x len=%04x\n", $src, $dest, length $out;
}

open OUT, "> sominflate.bin" or die;
print OUT $out;
close OUT;

sub mvn {
  # emulate 65816's MVN (move negative) operation
  my ($a, $x, $y) = @_;
  while ($a-- >= 0) {
    substr($out, $y++, 1) = substr($out, $x++, 1);
  }
}
sub s2o {
  # hirom
  my ($in) = @_;
  return $in + 0x200;
}
