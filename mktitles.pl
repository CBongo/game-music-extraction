#!/usr/bin/perl

while (<>) {
  # AKAO id 63 = psf/FF7 418 Staff Roll.minipsf
  if (/AKAO id (..) = psf\/FF7 (.+)\.minipsf/) {
    $t[hex($1)] = $2;
  }
}

print "\@titles = (\n";
foreach (@t) {
  print qq("$_",\n);
}
print ");\n";
