#!/usr/bin/perl

$f = shift or die "usage: $0 <filename>\n";

open IN, "< $f" or die "open failed: $!\n";
while (!eof IN) {
  $nr = read IN, $buf, 8;
  #print "Got $nr bytes\n";
  last if $nr != 8;
  ($nlen, $earlen, $ext, $parent) = unpack "C2Vv", $buf;
  #print "Got len=$nlen elen=$earlen ext=$ext parent=$parent\n";
  last if $nlen == 0;
  read IN, $dname, $nlen;
  $dname = "" if $dname eq "\0";
  read IN, $buf, 1 if $nlen % 2;  #padding byte align to 16 bits
  printf "%5d %5d %-8s %8x\n", ++$index, $parent, $dname, $ext;
}
