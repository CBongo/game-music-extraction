#!/usr/bin/perl
#
# given pacman vram address, report screen
# position as (x,y)
#
# cab 2016-11-26

use integer;

$addr = shift or die "usage: $0 <address>\n";

$addr = hex($addr);
die "addr out of range (4000-43ff)\n"
  if $addr < 0x4000 || $addr > 0x43ff;

$offset = $addr - 0x4000;

if ($offset < 0x40) {
  # bottom two rows
  $x = 29 - $offset % 0x20;
  $y = 34 + $offset / 0x20;
} elsif ($offset < 0x3c0) {
  # middle vertical section
  $x = 27 - ($offset - 0x40) / 0x20;
  $y = 2 + $offset % 0x20;
} else {
  # top two rows
  $x = 29 - $offset % 0x20;
  $y = ($offset - 0x3c0) / 0x20;
}

$offscreen = "";
if ($x < 0 || $x > 27) {
  $offscreen = "*";
}
printf "%04x = (%d%s,%d)\n", $addr, $x, $offscreen, $y;

