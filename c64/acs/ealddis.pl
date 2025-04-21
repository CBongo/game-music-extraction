#!/usr/bin/perl
#
# disassemble p-code in old EA (ACS, RDS) loader
# 2002-04-27  cab
# done before on the c64...
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

@ops = (['JMP',   'abs'],
	['AND',   'imm'],
	['JSR',   'abs'],
	['JSRML', 'abs'],
	['LD',    'imm'],
	['LD',    'abs'],
	['BEQ',   'abs'],
	['ST',    'abs'],
	['SUB',   'imm'],
	['JMPML', 'abs'],
	['RTS',   'imp'],
	['LD',    'ind'],
	['ASL',   'imp'],
	['INC',   'abs'],
	['ADD',   'abs'],
	['EOR2C', 'imp'],
	['BNE',   'abs'],
	['SUB',   'abs'],
	['BPL',   'abs'],
	['SETXY', 'two']);

for ($c = $startaddr; $c < $endaddr; ) {
	my $cmd = &$getbytes("C", $c, 1);
	last if $cmd > @ops;  # bail on invalid opcode
	
	my ($label, $amode) = @{$ops[$cmd]};
	if ($amode eq 'abs' || $amode eq 'ind') {
		my $operand = &$getbytes("v", $c+1, 2) ^ 0x292d;
		printf "%04X            %-5s \$%04X%s\n",
			$c, $label, $operand, $amode eq 'ind' ? "+A" : "";
		$c += 3;
	} elsif ($amode eq 'two') {
		my @operands = &$getbytes("C2", $c+1, 2);
		$operands[0] ^= 0x2d;
		$operands[1] ^= 0x29;
		printf "%04X            %-5s #\$%02X,#\$%02X\n",
			$c, $label, @operands;
		$c += 3
	} elsif ($amode eq 'imm') {
		my $operand = &$getbytes("C", $c+1, 1) ^ 0x6b;
		printf "%04X            %-5s #\$%02X\n", $c, $label, $operand;
		$c += 2;
	} elsif ($amode eq 'imp') {
		printf "%04X            %-5s\n", $c, $label;
		$c += 1;
	}
}
print "\n";  # cause we're gonna cat a bunch of these together
