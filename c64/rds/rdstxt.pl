#!/usr/bin/perl
#
# racing destruction set music decompiler
#
# cab 2014-11-27
#

@notenames = qw(C C# D D# E F F# G G# A A# B);

#preload disassembly to include inline
open TXT, "< rdsloader-90.txt" or warn;
while (<TXT>) {
  chomp;
  next if /:$/;  # skip label-only lines
  my ($a) = split / +/, $_, 2;
  $disasm{hex $a} = $_;
}

#print "DISASM DUMP:\n";
#print join("\n", map { "$_ $disasm{$_}"} sort keys %disasm),"\n";
#exit;

open IN, "< rdsloader-90.prg" or die;

read IN, $buf, 2;   # load address
read IN, $buf, 0x800;  # slurp in data

@vstart = getmem(0x9020, 6, "v3");
push @vstart, 0x96c5;  # end of v3 data

foreach $v (1..3) {
	decompile_voice($v, $vstart[$v-1], $vstart[$v]);
}

sub decompile_voice {
	my ($v, $start, $end) = @_;
	
	printf "Voice %d (%04x)\n", $v, $start;
	my $notemode = 0;
	for ($addr = $start; $addr < $end; $addr++) {
		if ($notemode) {
			my $b1 = getmem($addr, 1);
			if ($b1 & 0x80) {
				# done, back to ML
				$notemode = 0;
				#printf "%04x  END\n\n", $addr;
				print "\n";
			} else {
				# note - get 2nd byte for dur
				my $b2 = getmem($addr+1, 1);
				my $len = $b2 & 0xf;
				my $noteoff = $b2 >> 4;
				my $notetext = "Tie";
				unless ($b1 == 0x7f) {
					my ($note, $oct) = notename($b1);
					$notetext = "$note$oct";
				}
				printf "%04x  %02x %02x  %s (%d/%d)\n",
					$addr, $b1, $b2, $notetext, $noteoff, $len;
				$addr++;
			}
		} else {
			my (@t) = getmem($addr, 3);
			my $target = ($t[2] << 8) + $t[1];
			#printf "%04x %02x %02x %02x\n", $addr, @t;

			if ($t[0] == 0xa9) {  # LDA #$xx
				$accum = $t[1];
			        #print "$disasm{$addr}\n" if $disasm{$addr};
				#printf "%04x  accum=%02x\n", $addr, $accum;
				$addr++;
			} elsif ($t[0] == 0xd0 ||
				 $t[0] == 0xf0) {
			        #print "$disasm{$addr}\n" if $disasm{$addr};
				printf "%04x  %s \$%04x\n", $addr, ($t[0] == 0xd0 ? "bne" : "beq"), $addr + &mksigned($t[1]) + 2;
				if (($t[0] == 0xd0 && !$z) ||
				    ($t[0] == 0xf0 && $z)) {
					# condition true, take branch
					#$addr += &mksigned($t[1]) + 1; 
					$addr++;
					#printf "--BRANCH TAKEN to %04x\n", $addr+1;
				} else {
					# condition false, continue
					$addr++;  # skip over branch arg
				}
			} elsif ($t[0] == 0x20) {  # JSR
#printf "--JSR to %04x\n", $target;
				if ($target == 0xa913 && $addr != 0x90d7) {
					# JSR $A913
					# 90d7 is sole occurrence of call w no data
					$notemode = 1;
					#printf "-- Notes found at %04x\n", $addr;
			    		print "\n";
				} elsif ($target == 0x96c5) {
					# aa09,x = 2
					$counter[1] = 2;
				} elsif ($target == 0x96cb) {
					# aa09,x = 4
					$counter[1] = 4;
				} elsif ($target == 0xa842) {
					# util dur
					printf "%04x         UTL rest (%d)\n", $addr, $accum;
				} else {
			    		print "$disasm{$addr}\n" if $disasm{$addr};
				}
				$addr += 2;  #skip rest of JSR
			} elsif ($t[0] == 0x4c) {  # JMP
			    	#print "$disasm{$addr}\n" if $disasm{$addr};
				printf "%04x  jmp \$%04x\n", $addr, $target;
				if ($addr + 4 >= $end) {
					print "at end of voice, skipping jump\n";
					$addr += 2;
				} else {
					#printf "jumping to %04x\n", $target;
					#$addr = $target - 1;
					$addr += 2;
				}
			} elsif (($t[0] == 0x9d ||
				  $t[0] == 0xde) &&
				 $target >= 0xaa08 &&
				 $target <= 0xaa0b) {
				# STA/DEC $aa0[8-b],x - set/dec counters
				if ($t[0] == 0x9d) {   # STA
					$counter[$t[1] - 8] = $accum;
				} else {   # DEC
					$z = (--$counter[$t[1] - 8] == 0);
				}
				printf "%04x  counter[%d]=%d z=%d\n", $addr, $t[1]-8, $counter[$t[1] - 8], $z;
				$addr += 2;
			} elsif ($t[0] == 0x9d || $t[0] == 0x8d) {
			    if ($target == 0xd418) {
				printf "%04x  set volume=%d\n", $addr, $accum;
			    } elsif ($target == 0xd405) {
				printf "%04x  set attack=%d decay=%d\n", $addr, ($accum >> 4), $accum & 0xf;
			    } elsif ($target == 0xd406) {
				printf "%04x  set sustain=%d release=%d\n", $addr, ($accum >> 4), $accum & 0xf;
			    } elsif ($target == 0xa9dc) {
				printf "%04x  set waveform to %s\n", $addr, ($accum == 0x20 ? "sawtooth" : "noise");
			    } elsif ($target == 0xaa1f) {
				printf "%04x  set ticks per note dur (%s) to %d\n", $addr, ($t[0] == 0x9d ? "current voice" : "v1"), $accum;
			    } elsif ($target == 0xaa26) {
				printf "%04x  set ticks per note dur (v2) to %d\n", $addr, $accum;
			    } elsif ($target == 0xaa2d) {
				printf "%04x  set ticks per note dur (v3) to %d\n", $addr, $accum;
			    } else {
			        #print "$disasm{$addr}\n" if $disasm{$addr};
				printf "%04x  set \$%04x%s to %d\n", $addr, $target, ($t[0] == 0x9d ? ',X' : ""), $accum;
			    }
			    $addr += 2;  #skip arg
			} elsif ($t[0] == 0xff) {
			    printf "%04x  NOP (\$FF)\n", $addr;
			} else {
			    print "$disasm{$addr}\n" if $disasm{$addr};
			    printf "UNHANDLED OPCODE: %02x\n", $t[0];
			}
		}
	}
	print "\n";
}

sub notename {
	use integer;
	my $notenum = shift;
	my $octave = int($notenum / 12);
	my $notename = $notenames[$notenum % 12];
	return ($notename, $octave);
}

sub getmem {
	my ($addr, $size, $fmt) = @_;
	
	$fmt ||= "C*";  #default to bytes
	return unpack $fmt, substr($buf, $addr - 0x9000, $size);
}

sub mksigned {
	if ($_[0] >= 0x80) {
		return $_[0] - 0x100;
        } else {
		return $_[0];
        }
}
