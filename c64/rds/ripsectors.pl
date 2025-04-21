#!/usr/bin/perl
#
# rip sectors from beginning of ea game
# cab 2002-04-28
# updated 2012-10-06 for racing destruction set

open D64, "< ../d64/racdesa.d64" or die;

read D64, $buf, 683 * 0x100;
close D64;

$out  = &read_sectors(1, 0..19) . &read_sectors(2, 0..7);
$x28 = 8;
$x2c = 0x800;
$c6bf = 0xd9;
$c6c0 = 0x14;

do {
  $x28 = &c4d3;
  &eor2c;
  &eor2c;
  &eor2c;
  &eor2c;
  #printf "2c/2d=%04x, 28=%02x\n", $x2c, $x28
} until $x28 == 0 || $x2c == 0x8c00;

open OUT, "> rdsloader.prg" or die;
print OUT pack "v", 0x7000;  # start address
print OUT $out;
close OUT;

sub eor2c {
  &halfeor;
  &halfeor;
  $x28 = ($x2c >> 8) ^ 0x7f;
}

sub halfeor {
	if ($x2c >= 0x7000) {
		my $old = unpack "C", substr($out, $x2c - 0x7000, 1);
		my $new = $old ^ $x28;
		substr($out, $x2c - 0x7000, 1) = pack "C", $new; 
		printf "%04x %04x->%04x bf=%04x c0=%04x 28=%04x\n", $x2c,
			 $old, $new, $c6bf, $c6c0, $x28;
	}
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

# assumes sectors are in the 21 sector/track zone
# which is true for these EA games
sub read_sectors {
  my ($t, @s) = @_;
  my $out;

  foreach $s (@s) {
    $out .= substr $buf, (($t - 1) * 21 + $s) * 0x100, 0x100;
  }
  return $out;
}
