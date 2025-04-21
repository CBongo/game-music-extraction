#!/usr/bin/perl
#
# rip sectors from beginning of ea game
# cab 2002-04-28

open D64, "< ACS1/ACS1.D64" or die;

read D64, $buf, 683 * 0x100;
close D64;

$out  = &read_sectors(1, 0..19) . &read_sectors(2, 0..9);
$x28 = 8;
$x2c = 0x800;
$c6bf = 0x73;
$c6c0 = 0x14;

do {
  $x28 = &c4d3;
  &eor2c;
  &eor2c;
  &eor2c;
  &eor2c;
} until $x28 == 0 || $x2c == 0x2600;

open OUT, "> acsloader.bin" or die;
print OUT pack "v", 0x0800;  # start address
print OUT $out;
close OUT;

sub eor2c {
  &halfeor;
  &halfeor;
  $x28 = ($x2c >> 8) ^ 0x7f;
}

sub halfeor {
  my $old = unpack "C", substr($out, $x2c - 0x800, 1);
  my $new = $old ^ $x28;
  substr($out, $x2c - 0x800, 1) = pack "C", $new; 
  #printf "%04x %04x->%04x bf=%04x c0=%04x 28=%04x\n", $x2c,
	 #$old, $new, $c6bf, $c6c0, $x28;
  $x2c++;
}

sub c4d3 {
  if (--$c6bf == $c6c0) {
    $c6bf = 0xf1;
    return ++$c6c0;
  } else {
    return $c6bf;
  }
}

sub read_sectors {
  my ($t, @s) = @_;
  my $out;

  foreach $s (@s) {
    $out .= substr $buf, (($t - 1) * 21 + $s) * 0x100, 0x100;
  }
  return $out;
}
