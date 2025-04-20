#!/usr/bin/perl

require "$ENV{HOME}/lib/snesutil.pl";

$rom = openrom(shift);
print "$rom->{title}: offset $rom->{offset} mode ",
  $rom->{hirom} ? "Hi" : "Lo", "ROM\n";
