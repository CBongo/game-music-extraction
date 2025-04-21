#!/usr/bin/perl
#

use Compress::Zlib;

$fn = shift or die "usage: $0 <filename>\n";

open IN, "< $fn" or die "open failed: $!\n";
read IN, $buf, 16;
($sig, $version, $rsize, $psize, $crc)
  = unpack "A3CV3", $buf;
printf STDERR "Signature: %s    Version: %d\n", $sig, $version;
printf STDERR "Reserved length: %x  Program length: %x  CRC32: %08x\n",
   $rsize, $psize, $crc;

if ($rsize > 0) {
  read IN, $buf, $rsize;
}
read IN, $buf, $psize;
close IN;
$out = substr(uncompress($buf), 0x800);
#$out =~ s/\0*$//;
#print $out;

if (substr($out,0,4) eq 'AKAO') {
   $aid = unpack "v", substr($out, 4, 2);
   printf "AKAO id %02x = %s\n", $aid, $fn;
}
