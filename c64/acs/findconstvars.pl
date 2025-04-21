#!/usr/bin/perl
#
# cab 6 june 2002
#

while (<>) {
  if (/^(....)  08F9 (....)/) {
    my $a = lc $1;
    my $v = $2;
    if (hex $v < 16) {
      $v = hex $v;
    } else {
      $v =~ s/^00//;
      $v = "\$$v";
    }
    print "\t  0x$a => ['push $v'],\n";
  } elsif (/^(....)  0906 .* (push VAR .....)/) {
    my $a = lc $1;
    my $v = $2;
    print "\t  0x$a => ['$v'],\n";
  }
}
