#!/usr/bin/perl
#
# wds2txt.pl - Convert WDS files to text files
# cab 2026-05-09
#
# basically just a petscii->ascii with some handling for color codes

use strict;
use warnings;

our %colormap = (
    "\x05" => "white",
    "\x1c" => "red",
    "\x1e" => "green",
    "\x1f" => "blue",
    "\x81" => "orange",
    "\x90" => "black",
    "\x95" => "brown",
    "\x96" => "light red",
    "\x97" => "dark gray",
    "\x98" => "med gray",
    "\x99" => "light green",
    "\x9a" => "light blue",
    "\x9b" => "light gray",
    "\x9c" => "purple",
    "\x9e" => "yellow",
    "\x9f" => "cyan"
);

$/ = "\r"; # WDS files are CR-delimited

while (<>) {
    chomp;
    tr/\xc1-\xda/\x61-\x7a/; # petscii uppercase to ascii lowercase
    foreach my $char (keys %colormap) {
        s/$char/\{$colormap{$char}\}/g;
    }
    print "$_\n";
}