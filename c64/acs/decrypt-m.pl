#!/usr/bin/perl

# cab 06-03-2002
# decrypt ACS music module "M"

open M, "< ACS1/M.PRG" or die;
read M, $buf, 0x10000;
close M;

my ($loadaddr) = unpack "v", $buf;
my $code = substr $buf, 2;

substr($code, (0x8009-$loadaddr), 2) = pack "v", 0x444c;

for ($p = 0x8009; $p < 0x8dbb; $p += 2) {
  my $w1 = unpack "v", substr $code, ($p-$loadaddr), 2;
  my $w2 = unpack "v", substr $code, ($p-$loadaddr)+2, 2;
  my $xw = $w1 ^ $w2;
  substr($code, ($p-$loadaddr)+2, 2) = pack "v", $xw;
}

open OUT, "> m-dec.bin" or die;
print OUT pack "v", $loadaddr;
print OUT $code;
close OUT;

