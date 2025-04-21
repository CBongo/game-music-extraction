#!/usr/bin/perl
#
# unpacker for lengend of dragoon (PSX, US/CAN)
# cab 2005-10-15
#
# uses byte pair encoding - for info see
#
#  Gage, Philip. 'A New Algorithm for Data Compression', C Users Journal,
#  February 1994
#
#  http://www.csse.monash.edu.au/cluster/RJK/Compress/frame.html
#  (contains decode source code from Gage article)

open IN, "< SCUS_944.91" or die "Couldn't open input file: $!";
read IN, $buf, 0x800;  # read PSX header; we only use a bit

$magic = unpack "A16", $buf;
die "Not a PSX EXE\n"
  unless $magic =~ /^PS-X EXE/;

($pc, $tstart, $tlen) = unpack "x16Vx4V2", $buf;
#$sp = unpack "V", substr($buf, 0x30);
#$copyright = unpack "A*", substr($buf, 0x4c);

#printf "Text start:  %08x\n", $tstart;
#printf "Text end:    %08x\n", $tstart + $tlen - 1;
#printf "Text len:    %08x\n", $tlen;
#printf "Initial PC:  %08x\n", $pc;
#printf "Initial SP:  %08x\n", $sp;
#print "Copyright notice:\n$copyright\n";

read IN, $text, $tlen;
close IN;  # slurped in whole file

open OUT, "> lodunpacked.exe" or die "Couldn't open output file: $!";

# copy new exe header and pad to 2k
print OUT substr($text, 0, 0x100), "\0" x 0x700; 

# unpack data
$wrote = 0;
$a0 = 0x208;

while (1) {
  $blocksize = unpack "V", substr($text, $a0, 4);  # $t4
  printf "fetching blocksize from %08x = %08x\n", $a0 + $tstart, $blocksize;
  $a0 += 4;
  #last if $blocksize != 0x800;
  last if $blocksize == 0;

  $wrote += $blocksize;

  # initialize table - start with no pairs
  for ($i = 0; $i < 0x100; $i++) {
    $left[$i] = $i;
  }

  for ($t0 = 0; $t0 < 0x100; ) {
    $t3 = unpack "C", substr($text, $a0++, 1);
    #printf "%08x: t0=%02x t3=%02x\n", $a0-1, $t0, $t3;
    # hi bit set = skip literal chars
    if ($t3 >= 0x80) {
      $t0 += $t3 - 0x7f;
      $t3 = 0;
    }
    last if $t0 >= 0x100;

    for ($i = 0; $i <= $t3; $i++, $t0++) {
      $left[$t0] = unpack "C", substr($text, $a0++, 1);
      if ($left[$t0] != $t0) {
	# only read the right half of a pair if it's really a pair
	$right[$t0] = unpack "C", substr($text, $a0++, 1);
      }
    }
  }

  #print "n  s  s2\n";
  #for ($i = 0; $i < 0x100; $i++) {
  #  printf "%02x %02x %02x\n", $i, $left[$i], $right[$i];
  #}

  #for ($i = 0; $i < 0x100; $i += 0x10) {
  #  printf "%03x: ", $i;
  #  for ($j = 0; $j < 0x10; $j += 4) {
  #    for ($k = 3; $k >= 0; $k--) {
  #      printf "%02x", $left[$i+$j+$k];
  #    }
  #    print " ";
  #  }
  #  print "\n";
  #}
  #for ($i = 0; $i < 0x100; $i += 0x10) {
  #  printf "%03x: ", $i + 0x100;
  #  for ($j = 0; $j < 0x10; $j += 4) {
  #    for ($k = 3; $k >= 0; $k--) {
  #      printf "%02x", $right[$i+$j+$k];
  #    }
  #    print " ";
  #  }
  #  print "\n";
  #}
  #print "\n";

  # 801BF3C4
  $align = 4 - ($a0 % 4);
  $align = 0 if $align == 4;
  @pipe = ();
  push @pipe, unpack "C$align", substr($text, $a0, $align) if $align;
  $a0 += $align;
  printf "aligned %d bytes to %08x\n", $align, $tstart + $a0;
  
  for (; $blocksize > 0; $blocksize--) { 
    # 801BF3E0
    unless (@pipe) {
      #print "filling pipe\n";
      push @pipe, unpack "C4", substr($text, $a0, 4);
      $a0 += 4;
    }
    $t0 = shift @pipe;
    if ($left[$t0] != $t0) {
      # 801BF400
      #printf "code detected (%02x)\n", $t0;
      do {
        unshift @pipe, $right[$t0];
        #printf "adding %02x to pipeline\n", $right[$t0];
        $t0 = $left[$t0];
	$v0 = $left[$t0];
      } until $t0 == $v0;
    }
    #printf "output: %02x\n", $t0;
    print OUT pack "C", $t0;
  }
}

close OUT;
printf "Finished; wrote \$%08x bytes.\n", $wrote;

## a0=src a1=dest a2=scratch1 a3=scratch2
