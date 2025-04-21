#!/usr/bin/perl
#
# fake checksum calculator for ea boot loader
# 2002-04-27  cab
#

my $startaddr = hex($ARGV[0]);
my $endaddr = hex($ARGV[1]);

open PRG, "< ACS1/EA2.PRG"
  or die;
read PRG, $buf, 0x10002; 
close PRG;

my $loadaddr = unpack "v", substr($buf, 0, 2);
my $code = substr($buf, 2);
$startaddr ||= $loadaddr;
$endaddr ||= $loadaddr + length($code);
#printf "Load addr = %04x  len = %04x\n", $loadaddr, length($code);
#printf "Start addr: %04x  End addr: %04x\n", $startaddr, $endaddr;

$getbytes = sub { my ($format, $start, $len) = @_;
                       return unpack $format,
                         substr($code, $start - $loadaddr, $len);
                     };

$sum = 0;
for ($c = $startaddr; $c < $endaddr; $c++) {
	my $b = &$getbytes("C", $c, 1);
	$sum <<= 1;
	if ($sum >= 0x100) {
	 	$sum &= 0xff;
		$sum++;
	}
	$sum += $b;
	$sum &= 0xff;
}
printf "sum=%02x\n", $sum;
