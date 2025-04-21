#!/usr/bin/perl
#
# RDS2MIDI - convert Racing Destruction Set code to MIDI format
# Christopher Bongaarts - 10 Feb 2016
#

use Data::Dumper;
use MIDI;

open IN, "< rdsloader-90.prg" or die;

read IN, $buf, 2;   # load address
read IN, $buf, 0x800;  # slurp in data

@vstart = getmem(0x9020, 6, "v3");
push @vstart, 0x96c5;  # end of v3 data

my $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 4});
my $ctrack = new MIDI::Track;
push @{$opus->tracks_r}, $ctrack;
my $cevents = [
	['time_signature', 0, 12, 3, 9, 8],
	['key_signature', 0, -2, 1],
    ];

my $velocity = 100;

my %patchmap = qw(piano 0 tom 117);

# shorthand array to access track events
my @e;
for ($v = 0; $v < 3; $v++) {
  @{$e[$v]} = [];
  push @{$e[$v]}, ['track_name', 0, "Voice $v"];
}

foreach $v (0..2) {
    decompile_voice($v, $vstart[$v], $vstart[$v+1]);
}

$ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
for ($v = 0; $v < 3; $v++) {
  push @{$opus->tracks_r},
      new MIDI::Track ({'events_r'
                          => MIDI::Score::score_r_to_events_r($e[$v])});
}
#$opus->dump({dump_tracks => 1});
$opus->write_to_file(sprintf "rdstheme.mid");
exit;

sub decompile_voice {
    my ($v, $start, $end) = @_;
	
    #printf "Voice %d (%04x)\n", $v, $start;
    my $notemode = 0;
    my $totaltime = 0;
    my $ticks = 0;
    for ($addr = $start; $addr < $end; $addr++) {
	if ($notemode) {
	    my $mnote = getmem($addr, 1);
	    if ($mnote & 0x80) {
		# done, back to ML
		$notemode = 0;
	    } else {
		# note - get 2nd byte for dur
		my $b2 = getmem($addr+1, 1);
		my $dur = $b2 & 0xf;
		my $noteoff = $b2 >> 4;
		if ($mnote == 0x7f) {  # tie
#print "TIE before: ", Dumper($e[$v]),"\n";
        	    my ($last_ev) = grep {$_->[0] eq 'note'} reverse @{$e[$v]};
        	    # make prev note last right up till now
        	    #$last_ev->[2] = $totaltime - $last_ev->[1];
          	    $last_ev->[2] += $dur;  # already did notedur
#print "TIE  after: ", Dumper($e[$v]),"\n";
		} else {
        	    push @{$e[$v]}, ['note', $totaltime, $noteoff, $v,
				     $mnote+12, $velocity];
		}
		#printf "%04x  %02x %02x  %s (%d/%d)\n",
			#$addr, $b1, $b2, $notetext, $noteoff, $len;
		$addr++;
		$totaltime += $dur;
	    }
	} else { # ml (cmd)mode
	    my (@t) = getmem($addr, 3);
	    my $target = ($t[2] << 8) + $t[1];
	    #printf "%04x %02x %02x %02x\n", $addr, @t;

	    if ($t[0] == 0xa9) {  # LDA #$xx
		$accum = $t[1];
		#printf "%04x  accum=%02x\n", $addr, $accum;
		$addr++;
	    } elsif ($t[0] == 0xd0 || $t[0] == 0xf0) {
		#printf "%04x  %s \$%04x\n", $addr, ($t[0] == 0xd0 ? "bne" : "beq"), $addr + &mksigned($t[1]) + 2;
		if (($t[0] == 0xd0 && !$z) ||
		    ($t[0] == 0xf0 && $z)) {
			# condition true, take branch
			$addr += &mksigned($t[1]) + 1; 
			#$addr++;
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
		} elsif ($target == 0x96c5) {
			# aa09,x = 2
			$counter[1] = 2;
		} elsif ($target == 0x96cb) {
			# aa09,x = 4
			$counter[1] = 4;
		} elsif ($target == 0xa842) {
			# util dur  in jiffies (1/60s)
			#printf "%04x         UTL rest (%d)\n", $addr, $accum;
			$totaltime += int($accum/$ticks);
			push @{$cevents},
			    ['time_signature', $totaltime, 12, 3, 3, 8];
		} else {
	    		print "$disasm{$addr}\n" if $disasm{$addr};
		}
		$addr += 2;  #skip rest of JSR
	    } elsif ($t[0] == 0x4c) {  # JMP
	    	#print "$disasm{$addr}\n" if $disasm{$addr};
		#printf "%04x  jmp \$%04x\n", $addr, $target;
		if ($addr + 4 >= $end) {
			#print "at end of voice, skipping jump\n";
			$addr += 2;
		} else {
			#printf "jumping to %04x\n", $target;
			$addr = $target - 1;
			#$addr += 2;
		}
	    } elsif (($t[0] == 0x9d || $t[0] == 0xde) &&
			 $target >= 0xaa08 &&
			 $target <= 0xaa0b) {
		# STA/DEC $aa0[8-b],x - set/dec counters
		if ($t[0] == 0x9d) {   # STA
			$counter[$t[1] - 8] = $accum;
		} else {   # DEC
			$z = (--$counter[$t[1] - 8] == 0);
		}
		#printf "%04x  counter[%d]=%d z=%d\n", $addr, $t[1]-8, $counter[$t[1] - 8], $z;
		$addr += 2;
	    } elsif ($t[0] == 0x9d || $t[0] == 0x8d) {
		    if ($target == 0xd418) {
			#printf "%04x  set volume=%d\n", $addr, $accum;
		    } elsif ($target == 0xd405) {
			#printf "%04x  set attack=%d decay=%d\n", $addr, ($accum >> 4), $accum & 0xf;
		    } elsif ($target == 0xd406) {
			#printf "%04x  set sustain=%d release=%d\n", $addr, ($accum >> 4), $accum & 0xf;
		    } elsif ($target == 0xa9dc) {
			#printf "%04x  set waveform to %s\n", $addr, ($accum == 0x20 ? "sawtooth" : "noise");
			&patch(\@e, $v, $totaltime, ($accum == 0x20 ? "piano" : "tom"));
		    } elsif ($target == 0xaa1f) {
			#printf "%04x  set ticks per note dur (%s) to %d\n", $addr, ($t[0] == 0x9d ? "current voice" : "v1"), $accum;
			$ticks = $accum;
			push @{$cevents},
			    ['set_tempo', $totaltime, $ticks * 66667];
		    } elsif ($target == 0xaa26) {
			#printf "%04x  set ticks per note dur (v2) to %d\n", $addr, $accum;
		    } elsif ($target == 0xaa2d) {
			#printf "%04x  set ticks per note dur (v3) to %d\n", $addr, $accum;
		    } else {
		        #print "$disasm{$addr}\n" if $disasm{$addr};
			#printf "%04x  set \$%04x%s to %d\n", $addr, $target, ($t[0] == 0x9d ? ',X' : ""), $accum;
		    }
		    $addr += 2;  #skip arg
	    } elsif ($t[0] == 0xff) {
		    #printf "%04x  NOP (\$FF)\n", $addr;
	    } else {
		    print "$disasm{$addr}\n" if $disasm{$addr};
		    printf "UNHANDLED OPCODE: %02x\n", $t[0];
	    }
	}
    }
    #print "\n";
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

sub patch {
  my ($e, $v, $t, $p) = @_;
  push @{$e->[$v]}, ['control_change', $t, $v, 0, 0]; # bank 0
  push @{$e->[$v]}, ['patch_change', $t, $v, $patchmap{$p}];
}
