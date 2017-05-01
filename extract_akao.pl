#!/usr/bin/perl
#
# extract akao music data from final fantasy IX archive file
# cab 2017-04-30

use Fcntl qw(SEEK_SET);

$sec_size = 0x800;  # size of one sector

@dirtypes = ("???", "???", "normal", "hierarchic");

open IN, "< FF9.IMG" or die "open failed: $!\n";
read IN, $buf, 4*4;
($sig, $dircount) = unpack "a4x4V", $buf;
print "sig=$sig dircount=$dircount\n";

for ($d=0; $d < $dircount; $d++) {
	read IN, $buf, 4*4;
	my ($dtype, $nfiles, $dsec, $f1sec) = unpack "V4", $buf;
	last if $dtype == 4;   # end of dir marker
	#printf "%4d type %d (%s), %3d files, dirsec=%02x  first #filesec=%05x\n",
	#	$d, $dtype, $dirtypes[$dtype], $nfiles, $dsec, $f1sec;
	my (%dent) = (type => $dtype, nfiles => $nfiles,
					dsec => $dsec, f1sec => $f1sec);
	push @dirs, \%dent;
}

for ($d=0; $d < @dirs; $d++) {
	my ($dsec, $nfiles) = ($dirs[$d]{dsec}, $dirs[$d]{nfiles});
	#printf "subdir %d \@ %02x (%d files):\n",
	#	$d, $dsec, $nfiles;
	next if $dirs[$d]{type} == 3;  # hier type
	seek IN, $dsec * $sec_size, SEEK_SET;
	for ($e=0; $e <= $nfiles; $e++) {
		read IN, $buf, 2 + 2 + 4;
		my ($fid, $ftype, $fsec) = unpack "v2V", $buf;
		#printf "%04x id %04x type %04x sector %06x\n",
		#	$e, $fid, $ftype, $fsec; 
		my (%fent) = (id => $fid, type => $ftype, fsec => $fsec);
		push @{$files[$d]}, \%fent;
	}
	
	for ($e=0; $e < @{$files[$d]}; $e++) {
		my $f = $files[$d][$e];
		last if $f->{id} == 0xFFFF;
		#printf "%04x id %04x type %04x sector %06x len %6x\n",
		#	$e, $f->{id}, $f->{type}, $f->{fsec},
		#	($files[$d][$e+1]{fsec} - $f->{fsec}) * $sec_size; 
	}
}

exit if @ARGV < 2;

($d, $f) = map {hex} @ARGV;
$s = $files[$d][$f]{fsec};
$len = $files[$d][$f+1]{fsec} - $s;
printf "Extracting subdir %02x file %04x (sector %06x len %x)\n",
	$d, $f, $s, $len;
open OUT, sprintf("> ff9img_%02x_%04x.bin", $d, $f) or die "open output failed: $!\n";
seek IN, $s * $sec_size, SEEK_SET;
while ($len > 0) {
	$curlen = ($len > 1024) ? 1024 : $len;
	read IN, $buf, $curlen * $sec_size;
	print OUT $buf;
	$len -= $curlen;
}
close OUT;
seek IN, $s * $sec_size, SEEK_SET;
read IN, $buf, 4;
($db, $nptrs) = unpack "C2", $buf;
if ($db == 0xDB) {
	my (@ptrs, @offsets, @ptype);
	printf "dir %x file %x found DB structure with $nptrs pointers:\n", $d, $f;
	for ($p=0; $p < $nptrs; $p++) {
		read IN, $buf, 4;
		my (@pb) = unpack "C4", $buf;
		my $ptr = $pb[0] + $pb[1] * 0x100 + $pb[2] * 0x10000;
		push @ptrs, $ptr;
		push @offsets, $ptr - 4 + tell IN;
		push @ptype, $pb[3];
	}
	for ($p=0; $p < $nptrs; $p++) {
		printf " %2d type %02x \@ %x (%x)\n",
			$p, $ptype[$p], $ptrs[$p], $offsets[$p];
		seek IN, $offsets[$p], SEEK_SET;
		read IN, $buf, 4;
		my ($dtype, $nobjs) = unpack "C2", $buf;
		printf "   type %02x nobjs %02x\n", $dtype, $nobjs;
		read IN, $buf, $nobjs * 6 + 4 + ($nobjs % 2 * 2);
		for ($obj=0; $obj < $nobjs; $obj++) {
			my ($oid) = unpack "v", substr($buf, $obj*2, 2);
			my ($optr) = unpack "V", substr($buf, $obj*4 + $nobjs*2 + ($nobjs % 2 * 2), 4);
			printf "   %2d ID %04x ptr %x (offset %06x)\n",
				$obj, $oid, $optr, $optr - 4 + tell IN;
		}
		my ($endptr) = unpack "V", substr($buf, $nobjs * 6 + $nobjs % 2 * 2, 4);
		printf "    end pointer: %x (offset %06x)\n", 
			$endptr, $endptr + $offsets[$p];
	}
}
close IN;