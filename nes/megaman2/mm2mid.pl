#!/usr/bin/perl
#
# mm2mid - megaman2.nsf to midi generator
# chris bongaarts <bong0004@tc.umn.edu>  1 April 2002
#

use MIDI;

$fname = "megaman2.nsf";

@songtitles = ('Flashman', 'Woodman', 'Crashman', 'Heatman',
		'Airman', 'Metalman', 'Quickman', 'Bubbleman',
		'Wily Fortress 1', 'Wily Fortress 2', 'Stage Intro',
		'Boss Battle', 'Stage Select', 'Intro 2', 'Intro 1',
		'Game Over', 'Continue', 'Wily Stage Intro',
		'Alert', 'Epilogue', 'Credits', 'Boss Victory',
		'Wily Victory', 'Powerup');

open NSF, "< $fname" or die "couldn't open $fname: $!\n";

read NSF, $buf, 0x80;
($format, $version, $totsongs, $startsong, $loadaddr, $initaddr, $playaddr,
 $songname, $artistname, $copyright, $ntsctempo, $bankinit, $paltempo,
 $palntscflags, $reserved)
    = unpack "a5C3v3Z32Z32Z32va8vCa5", $buf;

printf "Format: %s\n", $format;
printf "Version: %d\n", $version;
printf "Total songs: %d\n", $totsongs;
printf "Starting song: %d\n", $startsong;
print  "\n";
printf "Load address: %04X\n", $loadaddr;
printf "Init address: %04X\n", $initaddr;
printf "Play address: %04X\n", $playaddr;
print "\n";
printf "Song name: %s\n", $songname;
printf "Artist name: %s\n", $artistname;
printf "Copyright holder: %s\n", $copyright;
print "\n";
printf "NTSC tempo: %04X\n", $ntsctempo;
printf "PAL  tempo: %04X\n", $paltempo;
$palntscflagdescr = $palntscflags & 2 ? "Dual PAL/NTSC"
	 	  : $palntscflags & 1 ? "PAL" : "NTSC";
printf "NTSC/PAL flags: %02X (%s)\n", $palntscflags, $palntscflagdescr;
print "\n";
printf "Initial bank settings:" . (" %02X" x 8) . "\n", unpack "C8", $bankinit;
printf "Reserved:" . (" %02X" x 5) . "\n\n", unpack "C5", $reserved;

read NSF, $buf, 16380;
close NSF;

@songptrs  = unpack "v*", substr $buf, 0xa50, 2 * 0x18;
@durtbl    = unpack "C*", substr $buf, 0x975, 8;
@dotdurtbl = unpack "C*", substr $buf, 0x97d, 8;

# finally, a place for a closure.  lisp class was fun AND useful.
my $readsong = sub { my ($format, $start, $len) = @_;
                     return unpack $format,
                       substr($buf, $start - 0x8000, $len);
                   };
 
#print "Song data:\n";
for ($cursong = 0; $cursong < @songptrs; $cursong++) {
  print STDERR "\n" if $cursong % 0x10 == 0x0 && $cursong > 0;
  printf STDERR "%02x ", $cursong;
  my $songaddr = $songptrs[$cursong];
  
  my ($flag, $sq1addr, $sq2addr, $triaddr, $noiseaddr, $digaddr)
    = unpack "Cv5", substr $buf, $songaddr - 0x8000, 5 * 2 + 1;

  open OUT, sprintf "> txt/%02x-%s", $cursong, $songtitles[$cursong];
  printf OUT "Song %02x @ %04x: ", $cursong, $songaddr;
  printf OUT "flag %02x  sq1 %04x sq2 %04x tri %04x noi %04x dig %04x\n",
    $flag, $sq1addr, $sq2addr, $triaddr, $noiseaddr, $digaddr;
  &do_voice_txt('Square 1', $sq1addr, $sq2addr || $triaddr || $noiseaddr);
  &do_voice_txt('Square 2', $sq2addr, $triaddr || $noiseaddr);
  &do_voice_txt('Triangle', $triaddr, $noiseaddr);
  &do_voice_txt('Noise',    $noiseaddr, 0);
  close OUT;
  
  my $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 96});
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [];
  
  # shorthand array to access track events
  my @e;
  my @vaddr = ($sq1addr, $sq2addr, $triaddr, $noiseaddr);
  my @tracknames = ('Square 1', 'Square 2', 'Triangle', 'Noise');
  
  for ($v = 0; $v < 4; $v++) {
    @{$e[$v]} = [];
    next unless $vaddr[$v];
    push @{$e[$v]}, ['track_name', 0, $tracknames[$v]];
    push @{$e[$v]}, ['control_change', 0, $v, 0, 0]; # bank 0
    #push @{$e[$v]}, ['patch_change', 0, $v, $patchmap[0]];
  }
  
  my $totaltime = 0;
  my $oddframe = 0;
  my @notebase = (0) x 4;
  my @currpt = (0) x 4;
  my $dotting = 0;
  my @tiecount = (0) x 4;
  my @tieflag = (0) x 4;
  my @tripflag = (0) x 4;
  my @masterrpt = (1) x 4;
  my @p = @vaddr;
  my @nexttime = (1) x 4;
  #$opus->dump({dump_tracks => 1});
  
  for (;; $totaltime++) {
     last unless grep { $_ > 0 } @p;  # quit when all voices halted
     $oddframe = !$oddframe;	# toggle oddframe flag
VOICE:
     for (my $v = 0; $v < 4; $v++) {
       next unless $p[$v]; # skip inactive voices
       $nexttime[$v]--;
       $nexttime[$v]-- if $tripflag[$v] && $oddframe;
       next if $nexttime[$v] > 0; # wait for next time to come
       $tripflag[$v] = 0;
NOTE: {
       my $cmd = &$readsong("C", $p[$v]++, 1);
       
       if ($cmd == 0) {  # tempo
         my $tempo = &$readsong("C", $p[$v]++, 1);
      	 push @{$cevents},
      	       ['set_tempo', $totaltime, int(400_000 * $tempo / 6 + 0.5)];
       } elsif ($cmd == 1) {	# VS+9, 1
	 my $op1 = &$readsong("C", $p[$v]++, 1);
       } elsif ($cmd == 2) {	# VS+C:6-7, 1
	 my $op1 = &$readsong("C", $p[$v]++, 1);
       } elsif ($cmd == 3) {	# VS+C:0-5, 1
	 my $op1 = &$readsong("C", $p[$v]++, 1);
       } elsif ($cmd == 4) {	# repeat
         my ($rptcount, $rptaddr) = &$readsong("Cv", $p[$v], 3);
#printf STDERR "rpt  in: v$v opct %02x curct %02x p[v] %04x\n", $rptcount, $currpt[$v], $p[$v];
         $p[$v] += 3;
         if ($rptcount > 0) {
           if ($currpt[$v] > 0) {
             if (--$currpt[$v] > 0) {
               $p[$v] = $rptaddr;
             }
           } else {
             $currpt[$v] = $rptcount;
             $p[$v] = $rptaddr;
           }
	 } else {
	   #repeat forever
	   if ($masterrpt[$v]-- > 0) {
	     $p[$v] = $rptaddr;
	   } else {
	     $p[$v] = 0;
	   }
	 }
#printf STDERR "rpt out: v$v opct %02x curct %02x p[v] %04x\n", $rptcount, $currpt[$v], $p[$v];
       } elsif ($cmd == 5) {	#octave/transpose, 1
         $notebase[$v] = &$readsong("C", $p[$v]++, 1);
       } elsif ($cmd == 6) {	#dotted note
         $dotting = 1;
       } elsif ($cmd == 7) {	# VS+D/E/F, 2
         my ($op1, $op2) = &$readsong("C2", $p[$v], 2);
         $p[$v] += 2;
       } elsif ($cmd == 8) {	# VS+6:0-5,VS+14-17, 1
         my ($op1) = &$readsong("C", $p[$v]++, 1);
       } elsif ($cmd == 9) {	# halt
         $p[$v] = 0;
       } elsif ($cmd >= 0x20 && $cmd <= 0x2f) {
       	 if ($tiecount[$v] == 0) { 
       	   $tieflag[$v] = 1;  # start this note
         }
       	 $tiecount[$v] += $cmd & 7;
       } elsif ($cmd >= 0x30 && $cmd <= 0x3f) {
         #&print_cmd('Set VS+5:7', 0);
         $tripflag[$v] = 1;
       } else {
         # note
         my $duridx = ($cmd & 0xe0) >> 5;
         my $dur = $dotting ? $dotdurtbl[$duridx] : $durtbl[$duridx];
         $dur *= 6;  # because we set 96 MIDI ticks/qtr; NSF is 16 t/q
         $dotting = 0;
         
         if ($tiecount[$v] > 0 && $tieflag[$v] == 0) {
           $tiecount[$v]--;
           my ($last_ev) = grep {$_->[0] eq 'note'} reverse @{$e[$v]};
  	   $last_ev->[2] += $dur;
         } else {
           $tieflag[$v] = 0;
           my $notenum = $cmd & 0x1f;
           if ($notenum > 0) {
      	     $notenum += $notebase[$v] + 24;
	     $notenum -= 12 if $v == 2;  # triangle sounds an 8va lower
      	     if ($v == 3) {  # noise channel
      	       push @{$e[$v]}, ['note', $totaltime, $dur, 9, 42, 100];
      	     } else {
      	       push @{$e[$v]}, ['note', $totaltime, $dur, $v, $notenum, 100];
      	     }
           }
         }
         $nexttime[$v] = $dur;
       }  # switch cmd
       last unless $p[$v];
       redo NOTE if $cmd < 0x40;  # keep going till we hit a note
     }  # note bareblock
     }  # for v
  } # for totaltime

  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  for ($v = 0; $v < 4; $v++) {
    push @{$opus->tracks_r},
    	new MIDI::Track ({'events_r' 
    			   => MIDI::Score::score_r_to_events_r($e[$v])});
  }
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%02x-%s.mid",
  				 $cursong, $songtitles[$cursong]);
}
print STDERR "\n";

#the end

sub do_voice_txt {
  my ($vlabel, $vstart, $vnext) = @_;
  
  print OUT "$vlabel:\n";
  return unless $vstart;
  $vnext = $songptrs[$cursong+1] unless $vnext;
  
  my $notebase = 0;
  my $dotting;
  for ($v = $vstart; ; print OUT "\n") {
    printf OUT "  %04x", $v;
    $dotting = 0;
    $opcount = 0;
 redoop:
    my $vop = &get_vbyte;
   
    if ($vop == 0) {
      &print_cmd('Tempo', 1);
    } elsif ($vop == 1) {
      &print_cmd('VS+9', 1);
    } elsif ($vop == 2) {
      &print_cmd('Square Duty Cycle', 1);
    } elsif ($vop == 3) {
      &print_cmd('Vol/Env/LinCtr', 1);
    } elsif ($vop == 4) {
      my (@op) = &print_cmd('Repeat', 3);
      printf OUT " to %04x", $op[1] + 0x100 * $op[2];
      if ($op[0] > 0) {
      	printf OUT " count %d", $op[0];
      } else {
      	printf OUT " infinite";
      	last;
      }
    } elsif ($vop == 5) {
      ($notebase) = &print_cmd('Octave/Transpose', 1);
    } elsif ($vop == 6) {
      $dotting = 1;
      goto redoop;
    } elsif ($vop == 7) {
      &print_cmd('VS+D/E/F', 2);
    } elsif ($vop == 8) {
      &print_cmd('VS+6:0-5,VS+14-17', 1);
    } elsif ($vop == 9) {
      &print_cmd('Halt', 0);
      last;
    } elsif ($vop >= 0x20 && $vop <= 0x2f) {
      &print_cmd(sprintf("Tie next %d notes", $vop & 7), 0);
    } elsif ($vop >= 0x30 && $vop <= 0x3f) {
      &print_cmd('Triplet', 0);
    } else {
      # note
      my $duridx = ($vop & 0xe0) >> 5;
      my $dur = $dotting ? $dotdurtbl[$duridx] : $durtbl[$duridx];
      my $notenum = $vop & 0x1f;
      print OUT "   " x (5-$opcount);
      if ($notenum > 0) {
      	$notenum += $notebase;
      	my $notename = ('C', 'Db', 'D', 'Eb', 'E', 'F',
      			'Gb', 'G', 'Ab', 'A', 'Bb', 'B')[$notenum % 12];
      	my $octave = int($notenum / 12) + 1;
      	
      	printf OUT "%-5s", $notename . $octave;
      } else {
      	printf OUT "%-5s", 'Rest';
      }
      printf OUT " Dur %02x", $dur;
    }
  }
  print OUT "\n";
}

sub print_cmd {
  my ($descr, $operands) = @_;
  my (@operands);
  while ($operands-- > 0) {
    push @operands, &get_vbyte;
  }
  print OUT ("   " x (5-$opcount)), $descr;
  return @operands;
}

sub get_vbyte {
  my $vbyte = unpack "C", substr $buf, $v++ - 0x8000, 1;
  printf OUT " %02x", $vbyte;
  $opcount++;
  return $vbyte;
}
