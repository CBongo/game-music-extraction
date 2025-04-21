#!/usr/bin/perl
#
# unlzs - decompress LZSS compressed ff7 files
# http://wiki.qhimm.com/view/FF7/LZS_format
# cab 2017-04-02

$fn = shift or die "usage: $0 <filename>\n";

open IN, "< $fn" or die "open $fn failed: $!\n";
open OUT, "> $fn.dec" or die "open $fn.dec failed: $!\n";

if (read(IN, $buf, 4) < 4) { 
  die "init read failed: $!\n";
}
$clen = unpack "V", $buf;
printf "compressed length = %8x\n", $clen;

while ($clen > 0) {
  # read "control byte" - msb to lsb, 1=literal, 0=backref
  $nr = read IN, $buf, 1;
  last if $nr < 1;

  $cb = unpack "C", $buf;
  #printf "got ctrl byte: %02x\n", $cb;
  for ($b = 8; $b > 0; $b--) {
    if ($cb & 1) {
      # literal
      $nr = read IN, $buf, 1;
      #printf "copying literal byte: %02x\n", unpack("C", $buf);
      $outbuf .= $buf;
    } else {
      # backref
      $nr = read IN, $buf, 2;
      ($br1,$br2) = unpack "C2", $buf;
      $roff = (($br2 & 0xF0) << 4) | $br1;
      $rlen = ($br2 & 0xF) + 3;
      #printf "got ref value: %02x %02x  offset: %03x len: %1x\n",
      #  $br1, $br2, $roff, $rlen;
      $boff = length($outbuf) - ((length($outbuf) - 18 - $roff) & 0xFFF);
      #printf "calculated buffer offset: %x  from pos: %x\n", $boff, length($outbuf);
      while ($rlen-- > 0) {
	# char by char to handle special cases
        if ($boff < 0) {
	  #print "-zero fill\n";
          $outbuf .= "\0";
        } else {
	  $outbuf .= substr($outbuf, $boff, 1);
          #printf "copied ref byte %02x\n", unpack "C", substr($outbuf, $boff, 1);
	}
	$boff++;
      }
    }
    $cb >>= 1;
  }
}
print OUT $outbuf;
close OUT;
close IN;

