#!/usr/bin/perl
#
# extract akao music data from final fantasy IX archive file
# cab 2017-04-30

use Fcntl qw(SEEK_SET);

$sec_size = 0x800;  # size of one sector

@dirtypes = ("???", "???", "normal", "hierarchic");
@dbtypes = ("???", "???", "3D Model", "3D Anim", "TIM Image", "Script",
			"???", "Song data", "???", "Instrument data", 
			"Field tiles", "Field walkmesh", "Battle scenes", "???",
			"???", "???", "???", "???", "CLUT/TPage");

$indent = "";
			
open IN, "< FF9.IMG" or die "open failed: $!\n";
read IN, $buf, 4*4;
($sig, $dircount) = unpack "a4x4V", $buf;
#print "sig=$sig dircount=$dircount\n";
die "bad file sig $sig\n" unless $sig =~ /^FF9/;

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
		seek IN, $f->{fsec} * $sec_size, SEEK_SET;
		read IN, $buf, 1;
		my ($db) = unpack "C", $buf;
		if ($db == 0xDB) {
			$f->{is_db}=1;
			#printf "Found DB file dir %x file %x\n", $d, $e;
		}
	}
}

if (@ARGV == 2) {
	@todo = [map {hex} @ARGV];
} else {
	for ($d=0; $d < @files; $d++) {
		for ($e=0; $e < @{$files[$d]}; $e++) {
			if ($files[$d][$e]{is_db}) {
				push @todo, [$d, $e];
				#printf "Selecting DB file dir %x file %x\n", $d, $e;
			}
		}
	}
}
foreach (@todo) {
	($d, $f) = @$_;
	$s = $files[$d][$f]{fsec};
	$len = $files[$d][$f+1]{fsec} - $s;
	printf "Extracting subdir %02x file %04x (sector %06x len %x)\n",
		$d, $f, $s, $len;
	#&write_file($d, $f, $s * $sec_size, $len * $sec_size);
	&process_db_data($s * $sec_size);
}
close IN;

sub write_file {
	my ($d, $f, $start, $len) = @_;
	my $buf;
	
	open OUT, sprintf("> ff9img_%02x_%04x.bin", $d, $f) or die "open output failed: $!\n";
	seek IN, $start, SEEK_SET;
	while ($len > 0) {
		my $curlen = ($len > 1048576) ? 1048576 : $len;
		read IN, $buf, $curlen;
		print OUT $buf;
		$len -= $curlen;
	}
	close OUT;
}

sub process_db_data {
	my ($start) = @_;
	my $buf;
	seek IN, $start, SEEK_SET;
	read IN, $buf, 4;
	my ($db, $nptrs) = unpack "C2", $buf;
	if ($db == 0xDB) {
		my (@ptrs, @offsets, @ptype);
		printf "%sdir %x file %x found DB structure at offset %x with %d obj types:\n",
			$indent, $d, $f, $start, $nptrs;
		for (my $p=0; $p < $nptrs; $p++) {
			read IN, $buf, 4;
			my (@pb) = unpack "C4", $buf;
			my $ptr = $pb[0] + $pb[1] * 0x100 + $pb[2] * 0x10000;
			push @ptrs, $ptr;
			push @offsets, $ptr - 4 + tell IN;
			push @ptype, $pb[3];
		}
		for (my $p=0; $p < $nptrs; $p++) {
			printf "%s  type %02x \@ %x (%x) ",
				$indent, $ptype[$p], $ptrs[$p], $offsets[$p];
			seek IN, $offsets[$p], SEEK_SET;
			read IN, $buf, 4;
			my ($dtype, $nobjs) = unpack "C2", $buf;
			printf "dtype %02x (%s) nobjs %02x\n",
				$dtype, $dbtypes[$dtype], $nobjs;
			my $oidlen = $nobjs * 2 + ($nobjs % 2 * 2);
			my $oplen  = $nobjs * 4;
			read IN, $buf, $oidlen + $oplen + 4;
			for (my $obj=0; $obj < $nobjs; $obj++) {
				my ($oid) = unpack "v", substr($buf, $obj*2, 2);
				my ($optr) = unpack "V", substr($buf, $obj*4 + $oidlen, 4);
				my $objoffset = $optr + $offsets[$p] + 4 + $obj*4 + $oidlen;
				printf "%s   %2d ID %04x ptr %x (offset %06x)",
					$indent, $obj, $oid, $optr, $objoffset;
				if ($dtype >= 7 && $dtype <= 9) {
					#likely AKAO types
					seek IN, $objoffset, SEEK_SET;
					read IN, $abuf, 8;
					my ($amagic, $aid, $alen) = unpack "A4v2", $abuf;
					if ($amagic eq "AKAO") {
						printf " AKAO id %x len %x\n", $aid, $alen;
					}
				} elsif ($dtype == 0x1B) {
					#embedded DB type
					print ":\n";
					$indent .= "    ";
					&process_db_data($objoffset);
					$indent =~ s/    $//;
				} else {
					print "\n";
				}
			}
			my ($endptr) = unpack "V", substr($buf, $oidlen + $oplen, 4);
			printf "%s    end pointer: %x (offset %06x)\n", 
				$indent, $endptr, $endptr + $offsets[$p];
		}
	} 
}
