#!/usr/bin/perl

# map subroutine calls for PSX (MIPS R3000A)
# cab 14 Apr 2003

$fname = shift; 
$cliaddr = hex(shift);

open IN, "< $fname" or die;
read IN, $buf, 0x800;  # read entire header; we only use a bit

die "Not a PSX EXE\n"
  unless $buf =~ /^PS-X EXE/;

($pc, $tstart, $tlen) = unpack "x16Vx4V2", $buf;
$sp = unpack "V", substr($buf, 0x30);
$copyright = unpack "A*", substr($buf, 0x4c);

read IN, $exe, $tlen;
close IN;

push @todo, $pc;  # start at program start

$SIG{'INT'} = sub { $got_signal = 1; };

while (@todo) {
  last if $got_signal;  # dump current state

  my $s = shift @todo;
  #printf "Trying %08x\n", $s;
  next if $subs{$s};   # already done this one?
  #printf "Doing %08x\n", $s;

  $subs{$s} = [];  # prep list for addition of child subs

  next if $s < $tstart;  # catch syscalls

  for ($p=$s; !$got_signal; $p += 4) {
    $w = unpack "V", substr($exe, $p-$tstart, 4);
    $op = $w >> 26;
    #printf "%08x: %08x op=%d\n", $p, $w, $op;
    if ($op == 3) {  # jal
      my $target = (($w & 0x03ffffff) << 2) | ($p & 0xf0000000);
      #printf "  jal %08x\n", $target;
      next if $target > 0x80070000;  # catch bogus calls (FF9)
      push @todo, $target unless $subs{$target};
      push @{$subs{$s}}, $target;
    } elsif ($op == 0) {  # SPECIAL
      $funct = $w & 0x3f;
      $rs = ($w & 0x03e00000) >> 21;
      #printf "  SPECIAL funct=%02x rs=%d\n", $funct, $rs;
      last if $funct == 8 && $rs == 31;  # jr $ra
    }
  }
}

# done; print results
print "Total subs: ", scalar(keys %subs), "\n";
if ($cliaddr) { 
  foreach $s (sort keys %subs) {
    if (grep { $cliaddr == $_ } @{$subs{$s}}) {
      &print_tree($s, 0, 3);
    }
  }
} else {
  &print_tree($pc, 0, -1);
}

sub print_tree {
  my ($addr, $depth, $maxdepth) = @_;
  my $notdone = (grep { $addr == $_ } @todo) ? "**" : "";

  printf "%s%08x%s\n", "  " x $depth, $addr, $notdone;

  return if $maxdepth >= 0 && $maxdepth == $depth;

  foreach $child (@{$subs{$addr}}) {
    &print_tree($child, $depth+1, $maxdepth) unless $child == $addr; # trap recursion
  }
}
