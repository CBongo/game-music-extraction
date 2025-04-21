#!/usr/bin/perl
#
# cab 2002-06-15
# convert ACS absolute sector # to track/sector
# from acs main $1435

use integer;

$a = hex(shift);

$d = $a / 0x2a8 + 1;  # sectors/disk
$a = $a % 0x2a8;

if ($a < 0x165) {
  $t = $a / 21 + 1;
  $s = $a % 21;
} else {
  $a += 2;  # skip directory/BAM? (T18S0-1)
  if ($a < 0x1ea) {
    $a -= 0x165;
    $t = $a / 19 + 18;
    $s = $a % 19;
  } elsif ($a < 0x256) {
    $a -= 0x1ea;
    $t = $a / 18 + 25;
    $s = $a % 18;
  } else {
    $a -= 0x256;
    $t = $a / 17 + 31;
    $s = $a % 17;
  }
}
  
print "D$d " if $d > 1;
print "T$t S$s\n";
