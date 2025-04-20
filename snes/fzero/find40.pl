#!/usr/bin/perl

chdir "/export/aspirin/Music/SPCs/fzero" or die;
opendir D, "." or die;
@d = grep { !/^\./ } readdir D;
closedir D;

foreach $song (@d) {
  open S, "< $song" or warn;
  seek S, 0x130, 0;
  read S, $buf, 0x12;
  close S;
  (@v[0..7], $p) = unpack "v*", $buf;
  push @allp, $p;
  push @allv, [@v];
}
foreach $i (sort { $allp[$a] <=> $allp[$b] } (0..$#d)) {
  printf "%s:\n  %04x:", $d[$i], $allp[$i];
  foreach $v (@{$allv[$i]}) {
    printf "  %04x", $v;
  }
  print "\n\n";
}
