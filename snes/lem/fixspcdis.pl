#!/usr/bin/perl

while (<>) {
  s/\r//g;
  s/^-//;
  s/^(.... 5A.*)MOVW/${1}CMPW/; # wrong opcode decode
  s/(\$..)(,\#)(\$..)/$3$2$1/;  # fix zeropage immediates
  s/(\$..)(<d>,)(\$..)(<s>)/$3$2$1$4/; # zp-zp
  print;
}
