#!/usr/bin/perl

# cab - Mar 15, 2026

use MIDI;

$tempo_factor = 24_606_720; # timer B is 4.005ms; 60M is std MIDI divisor
                            # voice is processed every 256/tempo ticks
                            # quarter note is 24 ticks

# song commands are 0x80-0xa3
$songcount = 35;

# titles starting at song command 0x80
@songtitles = ("Departure for Space (start 1)", "Sand Storm (stage 1)",
        "Crystal Labyrinth (stage 9)", "Boss on Parade 3 (stage 10a)",
        "Easter Stone (stage 5)", "Aqua Illusion (stage 2)",
        "In the Wind (stage 3a)", "Game Over", "King of Kings",
        "Invitation (weapon select)", "Mechanical Base (stage 10b1)",
        "Cosmo Plant (stage 8)", "Prelude of Legend (title)",
        "Boss on Parade 1 (stage 10a)", "Boss on Parade 2 (stage 10a)",
        "A Long Time Ago (stage 3 hidden)", "Unused 1",
        "Challenger 1985 (Gradius 1 BGM 1)", "Power of Anger (Salamander BGM 1)",
        "Poison of Snake (Salamander BGM 2)", "Unused 2", "Dark Force (final boss)",
        "Aircraft Carrier (Gradius 1 BGM 2)", "High Speed Dimension (stage 4)",
        "Try to Star (start 2)", "Underground (stage 3b)",
        "Escape to the Freedom (stage 10b3)", "Dead End Cell (stage 6)",
        "Final Shot (stage 10b2)", "Fire Scramble (stage 7)",
        "Return to the Star (ending)", "Congratulations (beginner ending)",
        "Departure for space (introless)", "Unused 3", "Big explosion sfx"
        );
# @songtitles = ("Powerup", "(1)Desert", "(2)Bubbles", "(3)Deja Vu",
# 			   "(4)The Moai", "(7)High Speed", "(5)Inferno", "(6)Garden",
# 			   "(6)Garden-copy", "(8)Factory", "(9)Organic",
# 			   "(1)Boss 1", "(3)Boss 3", "Death", "(2)Boss 2",
# 			   "(9)Boss 9 - Brain", "Weaponry", "Launch",
# 			   "King of Kings (high score)", "Title", "Continue",
# 			   "(6)Boss 6 - Laser Platform", "(4)Boss 4 - Big Laser",
# 			   "(8)Boss 8 - Spiderbot", "Powerup 2", "(7)Boss 7 - Missles",
# 			   "Secret Stage", "Credits", "(5)Boss 5 - Crystal Star");


@optext = ("Set callback7 table", "Set Tempo", "Patch Change (FM)",
           "Set level adjustment", "Set vstate 12", "Bulk Duration Set",
           "Set voice fine tuning", "Set Set-Keycode flag",
           "Global transpose", "Per-voice transpose",
           "Global level adj", "Per-voice level adj",
           "Ramp Repeat", "Set LFO params", "Set vstate 36", "Set pan flags",
           "Goto", "Repeat F1",
           "Begin Repeat F2", "End Repeat F2",
           "Begin Repeat F4", "End Repeat F4",
           "Call Subroutine", "Return from subroutine",
           "Repeat F8",
           "nop", "nop", "nop", "nop", "nop", "nop", 
           "Halt");

@oplen = (1,1,1,1,1,2,1,1,   # e0-e7
         1,1,1,1,5,4,1,1,   # e8-ef
         2,3,1,0,1,0,3,0,   # f0-f7
         3,0,0,0,0,0,0);    # f8-fe

$max_oplen = 5;  # for formatting disassembly

@notes = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B");

# @patchmap = ( 38, 56, 7, 0, 52, 55, 0, 0, # 00-07
#               68, 33, 0, 80, 0, 122, 0, 62, # 08-0F
#               0, 0, 0, 50, -36, -40, -42, -46, # 10-17
#               -49, -45, -48, -50, 0, 0, 0, 0, # 18-1F
#               0, 0, 0);               # 20-22

%patchmap = ( 
  0x6b => 34,  # elec bass picked
    );

# @transp   = ( -2, 0, 0, 0, 0, 0, 0, 0, # 00-07
#               0, -1, 0, 0, 0, 0, 0, 0, # 08-0F
#               0, 0, 0, 0, 0, 0, 0, 0, # 10-17
#               0, 0, 0, 0, 0, 0, 0, 0, # 18-1F
#               0, 0, 0);               # 20-22

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex($_) - 0x80] = 1;
  }
} else {
  @dosong = (1) x $songcount;
}  

open ROM, "< rom/945_r05.d9" or die;
read ROM, $buf, 0x10000;  # slurp in the whole 64k
close ROM;

# open OUT, ">&STDERR";
# &hexdump(0, $buf);
# close OUT;

# finally, a place for a closure.  lisp class was fun AND useful.
$readsong = sub { my ($format, $start, $len) = @_;
                       return unpack $format,
                         substr($buf, $start, $len);
                     };

# tables from code
#@oplen  = unpack "C*", substr($romdata, 0x0d7a - 0x400, 0x1f);
#@durpct = unpack "C*", substr($romdata, 0x10e5 - 0x400, 8);
#@voltbl = unpack "C*", substr($romdata, 0x10ed - 0x400, 0x10);
#@pantbl = unpack "C*", substr($romdata, 0x10aa - 0x400, 0x16);
# durtbl is actually at 183a but is effectively 1-based;
# zeroth element is not used
@durtbl = unpack "C*", substr($buf, 0x1839, 0xe);
#print STDERR "Durtbl: " . join(", ", map {sprintf "%02x", $_} @durtbl) . "\n";

for ($song = 0, $numdone = 0; $song < @dosong; $song++) {
  next unless $dosong[$song];
  $display_song = $song + 0x80;

  printf STDERR "%02x ", $display_song;
  print STDERR "\n" if ++$numdone % 16 == 0;

  $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 24});
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [];

  #open OUT, sprintf("> txt/%02X - %s", $display_song, $songtitles[$song]);
  printf OUT "Song %02x - %s:\n", $display_song, $songtitles[$song];

  # get song voice pointers from table at 0x3db6
  # 10 voices per song 
  @song_vptrs = &$readsong("v*", 0x3db6 + 10*2*$song, 10 * 2);
  #&hexdump(0x3db6 + 10*2*$song, substr($buf, 0x3db6 + 10*2*$song, 20), 20);
  
  my $vx = 0;
  print OUT "Voice ptrs: " . join(", ", map {sprintf "%d:%04x", $vx++, $_} @song_vptrs) . "\n\n";

  my $rptcount = 0;
  my $maxtime = 0;
  my $transpose = 0;
  my $tempo_voice = -1;
  my $tempo = 120;

    for (my $v = 0; $v < 10; $v++) {
#print STDERR "v=$v\n";
      print OUT "Voice $v:\n";

      my $dur;
      my $durpct;
      my $totaltime = 0;
      my $master_rpt = 1;

      my $velocity = 100;
      my $volume = 0;
      my $balance = 64;
      my $channel = $v;
      $channel++ if $channel == 9;  # skip percussion channel
      my $perckey = 0;
      my $t = 0;  # transpose (from patch settings)
      my $substart = 0;
      my $subret = 0;
      my $subcount = 0;
      my $rptaddr  = 0;
      my $rptcount = 0;
      my $vtranspose = 0;
      my $ramprptcount = 0;
      my $ramptranspose = 0;
      my $rampvolume = 0;

      my $track = new MIDI::Track;
      my $e = [];  # track events
      push @$e, ['track_name', 0, "Voice $v"];
      push @$e, ['control_change', 0, $channel, 0, 0]; # bank 0

      for (my $p = $song_vptrs[$v]; $p != $song_vptrs[$v+1]; $p++) {
        last unless $p;
        # sanity check
        if ($p < 0x5b11 || $p > 0xec92) {
          print OUT "  %04x: Invalid pointer\n", $p;
          last;
        }
        #last unless ($v > 0 || $subret || $p < $vtbl[$v+1]);
        #last if ($v > 0) && ($totaltime >= $maxtime
        # 					 || $totaltime >= $vtbltime[$vtblnum]);

        my $opcount = 0;
        my $cmd = &$readsong("C", $p, 1);
#printf STDERR " p=%04x  cmd=%02x\n", $p, $cmd;
        printf OUT "  %04x:  %02x", $p, $cmd;

        if ($cmd < 0x10) {
          # set octave
          $octave = $cmd & 7;
          printf OUT "   " x $max_oplen . "Set octave %d", $octave ;
        } elsif ($cmd < 0xe0) {
          # note
          print OUT "   " x $max_oplen;
          $oplen = 0;
          if ($util_dur) {
            $dur = $util_dur;
            $oplen = $util_dur_oplen;
            $util_dur = 0;
            $util_dur_oplen = 0;
          } else {
            $dur = $durtbl[($cmd & 0xf0) >> 4];
          }
          my $notedur = $dur;
          
          $notenum = $cmd & 0xf;
          if ($notenum == 0) {
            printf OUT "Rest         Dur %02x", $dur;   
          } else {
            my $mnote = 12 * $octave + $notenum + $transpose + $vtranspose + $ramptranspose - 2;
            $mnote = 0 if $mnote < 0;
            # add 13 to convert from midi to ym2151 key code
            $mnote += 13;

            printf OUT "Note %-3s     Dur %02x  (mnote %02x oct %d tsp %d vtsp %d)",
                $notes[$mnote % 12] . int($mnote / 12), $dur, $mnote, $octave, $transpose, $vtranspose;
            push @$e, ['note', $totaltime, $notedur, $channel, $mnote, $velocity];
          }
          $totaltime += $dur;
          $p += $oplen;
        } else {
          # command
          my $optext = $optext[$cmd - 0xe0];
          my $oplen = $oplen[$cmd - 0xe0];
          if ($cmd == 0xe5) {
            # variable length command
            while(1) {
              my ($opn) = &$readsong("C", $p+$oplen, 1);
              last if ($opn & 0xf0) == 0 or ($opn & 0xf) == 0;
              $oplen++;
            }
          }
          my (@op) = &$readsong("C*", $p+1, $oplen) if $oplen > 0;
          print OUT map {sprintf " %02x", $_} @op;
          print OUT "   " x ($max_oplen-$oplen);
          print OUT $optext;

          if ($cmd == 0xe0) {
            # set callback7 table
          } elsif ($cmd == 0xe1) {
            # tempo
            if ($tempo_voice < 0 || $tempo_voice == $v) {
                $tempo_voice = $v;
                $tempo = $op[0];
                push @{$cevents},
	                ['set_tempo', $totaltime, int($tempo_factor / $tempo)];
            }
          } elsif ($cmd == 0xe2) {
            # set patch
            my $inst = $patchmap{$op[0]};
            $t = $transp{$op[0]};
	          push @$e, ['patch_change', $totaltime, $v, $inst];
          } elsif ($cmd == 0xe3) {
            # set level adjustment
            #$volume = $op[0];
          } elsif ($cmd == 0xe4) {
            # set vstate 12
          } elsif ($cmd == 0xe5) {
            # utility duration
            $note = shift @op;
            # start with note duration
            $util_dur = $durtbl[($note & 0xf0) >> 4];
            # add in durations in remaining operands
            foreach $op (@op) {
              $util_dur += $durtbl[($op & 0xf0) >> 4] if $op &0xf0;
              $util_dur += $durtbl[$op & 0xf] if $op & 0xf;
            }
            $util_dur_oplen = $oplen - 1;
            $oplen = 0;  # next loop will interpret op1 as note
          } elsif ($cmd == 0xe6) {
            # set voice fine tuning
          } elsif ($cmd == 0xe7) {
            # set Set-Keycode flag
          } elsif ($cmd == 0xe8) {
            # global transpose
            $transpose = &get_transpose($op[0]);
          } elsif ($cmd == 0xe9) {
            # per-voice transpose
            $vtranspose = &get_transpose($op[0]);
          } elsif ($cmd == 0xea) {
            # global level adj
          } elsif ($cmd == 0xeb) {
            # per-voice level adj
          } elsif ($cmd == 0xec) {
            # repeat ramping note and/or level
            if ($ramprptcount == 0) {
              $ramprptcount = $op[0];
            }
            if (--$ramprptcount > 0) {
              $ramptranspose += &get_transpose($op[1]);
              my $lvladj = $op[2] & 0x7f;
              $lvladj = -$lvladj if $op[2] & 0x80;
              $rampvolume += $lvladj;
              my $dest = 0x100 * $op[4] + $op[3];
              $p = $dest - $oplen - 1;
            } else {
              $ramprptcount = 0;
              $ramptranspose = 0;
              $rampvolume = 0;
            }
          } elsif ($cmd == 0xed) {
            # set LFO params
          } elsif ($cmd == 0xee) {
            # set vstate 36
          } elsif ($cmd == 0xef) {
            # set pan flags
          } elsif ($cmd == 0xf0) {
            # goto
            if ($f1_outstanding || $f8_outstanding) {
              # actually do the goto in this case
              $f1_outstanding = 0;
              $f8_outstanding = 0;
              my $dest = 0x100 * $op[1] + $op[0];
              $p = $dest - $oplen - 1; # added at end
            } else {
              # halt
              last;
            }
          } elsif ($cmd == 0xf1) {
            # repeat F1
            if ($rptcount1) {
              if (--$rptcount1 == 0) {
                # done.
                $p = $rptaddr1 - $oplen - 1;
                $rptaddr1 = 0;
              }
            } else {
              # begin repeat
              $rptcount1 = $op[0] - 1;
              $rptaddr1 = 0x100 * $op[2] + $op[1];
              $f1_outstanding = 1;
            }
          } elsif ($cmd == 0xf2) {
            # begin repeat
            $rptcount2 = $op[0];
            $rptaddr2 = $p + $oplen;
          } elsif ($cmd == 0xf3) {
            if (--$rptcount2 == 0) {
              # done.
              $rptaddr2 = 0;
            } else {
              $p = $rptaddr2 - $oplen;
            }
          } elsif ($cmd == 0xf4) {
            # begin repeat
            $rptcount4 = $op[0];
            $rptaddr4 = $p + $oplen;
          } elsif ($cmd == 0xf5) {
            if (--$rptcount4 == 0) {
              # done.
              $rptaddr4 = 0;
            } else {
              $p = $rptaddr4 - $oplen;
            }
          } elsif ($cmd == 0xf6) {
            # gosub
            $subret = $p + $oplen;
            $subcount = $op[0];
            $substart = 0x100 * $op[2] + $op[1];
            $p = $substart - $oplen - 1;
          } elsif ($cmd == 0xf7) {
            # return from subroutine
            if (--$subcount == 0) {
              # done with subroutine
              $substart = 0;
              $p = $subret - $oplen;
            } else {
              # repeat subroutine
              $p = $substart - $oplen - 1;
            }
          } elsif ($cmd == 0xf8) {
            # repeat F1
            if ($rptcount8) {
              if (--$rptcount8 == 0) {
                # done.
                $p = $rptaddr8 - $oplen - 1;
                $rptaddr8 = 0;
              }
            } else {
              # begin repeat
              $rptcount8 = $op[0] - 1;
              $rptaddr8 = 0x100 * $op[2] + $op[1];
              $f8_outstanding = 1;
            }
          } elsif ($cmd == 0xff) {
            # halt
            last;
          }
          $p += $oplen;
        }
        
        print OUT "\n";
      } # for p
      print OUT "\n";
      # set track events from work array
      if (@$e) {
        $track->events_r(MIDI::Score::score_r_to_events_r($e));
        push @{$opus->tracks_r}, $track;
      }
    } # for v
  #&hexdump($song[$song]{apudest}, $sdata);
  close OUT;
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents)) if @$cevents;
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%02X - %s.mid", $song + 0x80, $songtitles[$song]);
}

print STDERR "\n";


sub get_transpose {
  my ($val) = @_;
  my $t = $val & 0x7f;
  my $octave = ($val & 0x70) >> 4;
  my $t = $octave * 12 + ($val & 0xf);
  $t = -$t if $val & 0x80;
  return $t;
}

sub hexdump {
  my ($addr, $data, $line) = @_;
  $line ||= 16;

  for (my $p = 0; $p < length $data; $p += $line) {
    printf OUT "    %04x: ", $addr + $p;
    if ($line == 16) {
      print OUT join(' ', map {sprintf "%02x", ord }
			 split //, substr($data, $p, 8)),
  	  '  ', join(' ', map {sprintf "%02x", ord }
			 split //, substr($data, $p + 8, 8)), "\n";
    } else {
      print OUT join(' ', map {sprintf "%02x", ord }
			 split //, substr($data, $p, $line)), "\n";
    }
  }
}
