#!/usr/bin/perl

# exported from bank 2 $d0f1
open IN, "< wwstringtabs.bin" or die;

read IN, $buf, 85;
@len = unpack "C*", $buf;

read IN, $buf, 85;
@x5e = unpack "C*", $buf;

read IN, $buf, 85;
@vram_lo = unpack "C*", $buf;

read IN, $buf, 85;
@vram_hi = unpack "C*", $buf;

read IN, $buf, 85;
@str_lo = unpack "C*", $buf;

read IN, $buf, 85;
@str_hi = unpack "C*", $buf;

for ($i=0; $i < 85; $i++) {
  printf "string_%02x: @%04x  %04x  len %2d  5e=%02x\n",
     $i,
     256 * $vram_hi[$i] + $vram_lo[$i],
     256 * $str_hi[$i] + $str_lo[$i],
     $len[$i],
     $x5e[$i];
}
