#!/usr/local/bin/perl 

while (<>) {
  s/\$(..),\#\$(..)/\$$2,\#\$$1/;
  print;
}