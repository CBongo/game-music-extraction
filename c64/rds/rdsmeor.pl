#!/usr/bin/perl

@magic = (0x9e, 0x92);

open IN, "< mainmenu.prg" or die;
read IN, $buf, 2;  # skip start addr
read IN, $buf, 0x2000;
close IN;

open OUT, " > mainmenu_decrypted.prg" or warn;
print OUT pack("v", 0x6100);   # start addr

# decrypt 6200-7c80 -> 6100
$a000 = 0;
for ($x = 0x100; $x < 0x1b80; $x++) {
	($b1, $b2) = unpack "CC", substr($buf, $x, 2);
	$a = $magic[$a000] ^ $b1 ^ $b2;
	$a &= 0xff;
	#printf "%04x  %02x\n", $x+0x6000, $a;
	print OUT pack "C", $a;
	$h[$a000][$a]++;
	$a000 ^= 1;
}
close OUT;
	
foreach $h (@h) {
	foreach $v (keys @{$h}) {
		printf "%02x %6d\n", $v, $$h[$v];
	}
	print "\n";
}