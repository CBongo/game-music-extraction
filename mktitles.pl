#!/usr/bin/perl

while (<>) {
  # AKAO id 63 = psf/FF7 418 Staff Roll.minipsf
  if (/AKAO id (..) = psf\/Chrono Cross (.+)\.psf/) {
    $t[hex($1)] = $2;
  }
}

print "\@titles = (\n";
foreach (@t) {
  print qq("$_",\n);
}
print ");\n";
