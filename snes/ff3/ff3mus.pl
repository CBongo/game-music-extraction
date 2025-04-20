#!/usr/bin/perl

# 25 Feb 2001  cab@tc.umn.edu

use MIDI;
use Data::Dumper;

open ROM, "< /export/tylenol/emulators/snes/f_fan3.fig"
  or die "open failed: $!\n";

$base = s2o('C5/0000');
$spcbase = $base + 0x70e + 2 - 0x200;

#$tempo_factor = 56_075_000;  # 60M is standard MIDI divisor
$tempo_factor = 59_904_000; # timer0 period 4.875ms * 96/2 ticks * 256

@songtitle = ("00", "Prelude", "Opening Theme 1", "Opening Theme 2",
              "Opening Theme 3", "Awakening", "Terra", "Shadow",
              "Stragus", "Gau", "Edgar and Sabin", "Coin Song",
              "Cyan", "Locke", "Forever Rachel", "Relm",
              "Setzer", "Epitaph", "Celes", "Techno de Chocobo",
              "Decisive Battle", "Johnny C Bad", "Kefka", "Narshe",
              "Phantom Forest", "Wild West", "Save Them",
              "Emporer Gestahl", "Troops March On", "Under Martial Law",
              "1E", "Metamorphosis", "Train Starting",
              "Another World of Beasts", "Grand Finale 2", "Mt Koltz",
              "Battle", "Short Fanfare", "Wedding 1",
              "Aria de Mezzo Carattere", "Serpent Trench",
              "Slam Shuffle (Zozo)",
              "Kids Run Through The City Corner", "Double Question Marks",
              "2C", "Gogo", "Returners", "Fanfare", "Umaro", "Mog",
              "Unforgiven", "Fierce Battle", "The Day After",
              "Blackjack", "Catastrophe", "Magic House",
              "Sleep", "39", "3A", "Dancing Mad 1", "3C", "Spinach Rag",
              "Rest in Peace", "3F",
              "40", "Overture 1", "Overture 2", "Overture 3", "Wedding 2",
              "Wedding 3", "Wedding 4", "Devils Lab",
              "48", "49", "4A", "New Continent", "Searching for Friends",
              "Fanatics", "Last Dungeon", "Dark World",
              "Dancing Mad 5", "51", "Dancing Mad 4",
              "Ending 1", "Ending 2");

%musicop = (0xc4 => "Set Volume", 0xc5 => "Volume Fade",
            0xc6 => "Set Balance", 0xc7 => "Balance Fade",
            0xc8 => "Portamento", 0xc9 => "Vibrato", 0xca => "Vibrato Off",
            0xcb => "Tremolo", 0xcc => "Tremolo Off", 0xcd => "Pan Sweep",
            0xce => "Pan Sweep Off", 0xcf => "Set Noise Clock",
	    0xd0 => "Enable Noise", 0xd1 => "Disable Noise",
	    0xd2 => "Enable Pitchmod", 0xd3 => "Disable Pitchmod",
	    0xd4 => "Enable Echo", 0xd5 => "Disable Echo",
	    0xd6 => "Set Octave", 0xd7 => "Inc Octave", 0xd8 => "Dec Octave",
	    0xd9 => "Set Transpose", 0xda => "Change Transpose",
	    0xdb => "Detune", 0xdc => "Patch Change",
            0xdd => "Set ADSR Attack", 0xde => "Set ADSR Decay",
            0xdf => "Set ADSR Sustain", 0xe0 => "Set ADSR Release",
            0xe1 => "Set Default ADSR", 0xe2 => "Begin Repeat",
            0xe3 => "End Repeat", 0xe4 => "Begin Slur", 0xe5 => "End Slur",
            0xe6 => "Begin Roll", 0xe7 => "End Roll", 0xe8 => "Utility Rest",
	    0xe9 => "Play SFX (E9)", 0xea => "Play SFX (EA)",
            0xeb => "Halt", 0xec => "Halt", 0xed => "Halt", 0xee => "Halt",
	    0xef => "Halt", 0xf0 => "Set Tempo", 0xf1 => "Tempo Fade",
	    0xf2 => "Set Echo Volume", 0xf3 => "Fade Echo Volume",
	    0xf4 => "Set Volume Multiplier", 0xf5 => "Conditional Loop",
	    0xf6 => "Goto", 0xf7 => "Set/Fade Echo Feedback",
	    0xf8 => "Set/Fade Filter", 0xf9 => "Advance Cue Point",
	    0xfa => "Zero Cue Point", 0xfb => "Ignore Volume Multiplier",
            0xfc => "Branch if \$DD", 0xfd => "Halt", 0xfe => "Halt",
            0xff => "Halt");

@notes = ("C ", "C#", "D ", "D#", "E ", "F ",
          "F#", "G ", "G#", "A ", "A#", "B ");

@patchmap  = (  0, 24, 33, 75, 105, 42, 52, 73,  # 00-07
                60, 80, 68, 17, 1, 48, 56, -42,  # 08-0F
                104, -46, -49, 121, -40, -39, 47, 117,  # 10-17
                32, 45, 58, 46, 34, 27, 30, 78,  # 18-1F
                11, -38, -35, -56, 14, 19, -78, 54,  # 20-27
                54, 54, -39, -37, 43, -74, 116, -69,  # 28-2F
                115, 1, 25, 109, 77, 64, 64, -53,  # 30-37
                52, 52, 52, 19, -65, -66, 13, 0,  # 38-3F
                0, 0, 0, 0, 0);          # 40-44

@transpose = (  0, 0, -3, +1, -1, -1, 0, +1,  # 00-07
                0, 0, 0, 0, 0, 0, -1, 0,  # 08-0F
                0, 0, 0, 0, 0, 0, 0, 0,  # 10-17
                -2, +1, -2, 0, -2, -1, 0, +1,  # 18-1F
                0, 0, 0, 0, -1, 0, 0, -1,  # 20-27
                -1, -1, 0, 0, -3, 0, +3, 0,  # 28-2F
                +3, +1, 0, 0, +1, -1, 0, 0,  # 30-37
                -1, -2, +1, 0, 0, 0, 0, 0,  # 38-3F
                0, 0, 0, 0, 0);          # 40-44

@patchlo  = ( 0, 6, 59, 26, 127, 98, 33, 122,  # 00-07
              0, 0, 0, 0, 0, 0, 0, 0,  # 08-0F
              0, 0, 0, 0, 0, 0, 0, 0,  # 10-17
              0, 0, 0, 0, 0, 0, 0, 0); # 18-1F

@transplo = ( 0, 0, 0, 0, +3, 0, +1, 0,  # 00-07
              0, 0, 0, 0, 0, 0, 0, 0,  # 08-0F
              0, 0, 0, 0, 0, 0, 0, 0,  # 10-17
              0, 0, 0, 0, 0, 0, 0, 0); # 18-1F



$whichsong = hex(shift);
if ($whichsong) {
  $firstsong = $whichsong;
  $lastsong  = $whichsong;
} else {
  $firstsong = 0;
  $lastsong  = 0x54;
}

@songptrs = &read3ptrs($base + 0x3e96, 0x55);
@sampptrs = &read3ptrs($base + 0x3c5f, 0x45);
#print STDERR join("\n", map {o2s($_)} @sampptrs),"\n";

# get SPC data - music ops lengths, pitch table, duration table
seek ROM, $spcbase + 0x18f9, 0;
read ROM, $buf, 60;
@musoplen = unpack "C60", $buf;

seek ROM, $spcbase + 0x178f, 0;
read ROM, $buf, 26;
@pitchtbl = unpack "v13", $buf;

seek ROM, $spcbase + 0x17d1, 0;
read ROM, $buf, 14;
@durtbl = unpack "C14", $buf;
#print STDERR "len:\n", join("\n",
#        map {sprintf "%02x:%02x", ($loop++) + 0xc4, $_} @musoplen), "\n";
#print STDERR "dur: ", join(",", map {sprintf "%02x", $_} @durtbl), "\n";

for ($i = $firstsong; $i <= $lastsong; $i++) {
  my ($opus);

  printf STDERR "%02X ", $i;
  print STDERR "\n" if $i % 16 == 15;

  $opus = new MIDI::Opus ('format' => 1);
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [];

  # do song i
  my ($len) = &read2ptrs($songptrs[$i], 1);
  # read all song data
  read ROM, $song, $len;
  # grab instrument table
  my (@inst) = map { $_ == 0 ? () : ($_) }
                 &read2ptrs($base + 0x3f95 + $i * 0x20, 0x10);
  my ($vaddroffset, $emptyvoice)  =  unpack "v2", $song;
#printf STDERR "voff=%04x, ev=%04x\n", $vaddroffset, $emptyvoice;
  $vaddroffset = (0x11c24 - $vaddroffset) & 0xffff;
#printf STDERR "voff2=%04x\n", $vaddroffset;
  my (@vstart) = unpack "v8", substr($song, 4, 0x10);
  my (@vstart2) = unpack "v8", substr($song, 0x14, 0x10);
#print STDERR "vs1: ", join(",", map {sprintf "%04x", $_} @vstart), "\n"; 
#print STDERR "vs2: ", join(",", map {sprintf "%04x", $_} @vstart2), "\n"; 
  @vstart = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart;
  @vstart2 = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart2;
#print STDERR "vs1b: ", join(",", map {sprintf "%04x", $_} @vstart), "\n"; 
#print STDERR "vs2b: ", join(",", map {sprintf "%04x", $_} @vstart2), "\n"; 

  my $maxtime = 0;
  my $maxvoice = -1;
  my $maxvol = 0;
  my $minvol = 0xff;

VOICE:
  for ($v = 0; $v < 8; $v++) {
    next if $vstart[$v] < 0x100;

    my $track = new MIDI::Track;
 
    my $totaltime = 0;
    my $master_rpt = 2;

    my $velocity = 100;
    my $volume = 0;
    my $balance  = 128;
    my $tempo    = 120;
    my $channel  = $v;
    my $octave = 4;
    my $perckey = 0;   # key to use for percussion
    my $t = 0;  # transpose octaves (via patch)
    my $transpose = 0;  # actual transpose command
    my $util_dur = 0;
    my $slurring = 0;
    my $rolling = 0;
    my $portdist = 0;
    my $oldpwheel = 0;
    my $portdtime = -1;  # last command time that a portamento cmd occurred
    my $pwheelrange = 0;
    my $rptidx = 0;
    my (@rptcnt, @rptpos, @rptcur);
    my $e = []; # track events
    push @$e, ['track_name', 0, "Voice " . ($v + 1)];
    push @$e, ['control_change', 0, $channel, 0, 0]; # bank 0
  
    for ($p = $vstart[$v] - 0x1c00; $p < $len; $p++) {
      #last if grep {$_ == $p + 0x1c00 && $_ ne $vstart[$v]} @vstart;

      my $cmd = ord(substr($song, $p, 1));
      if ($cmd < 0xc4) {
        # note
        my ($note, $dur) = (int($cmd/14), $cmd % 14);
        if ($util_dur > 0) {
          $dur = $util_dur;
          $util_dur = 0;
        } else {
          $dur = $durtbl[$dur];
        }
        $dur <<= 1;

        my $mnote = 12 * ($octave + $t) + $note + $transpose;
        if ($perckey) {
          # substitute appropriate percussion key on chan 10
          # fake keys for cuica
          $mnote = $perckey;
          if ($perckey == 78) {
            $perckey = 79;
          } elsif ($perckey == 79) {
            $perckey = 78;
          }
        }
        if ($note < 12) {
          my $notedur = (($slurring || $rolling) || $dur < 4) ?
                 $dur : $dur - 4;
          if ($oldpwheel && $portdtime != $totaltime) {
            push @$e, ['pitch_wheel_change', $totaltime, $channel, 0];
            $oldpwheel = 0;
            $portdist = 0;
          }
          push @$e, ['note', $totaltime, $notedur, $channel, $mnote, $velocity];
          if ($slurring == 1) {
            # legato pedal on
            push @$e, ['control_change', $totaltime, $channel, 68, 127];
            $slurring = 2;
          }
        } elsif ($note == 12) {
          # tie
          my ($last_ev) = grep {$_->[0] eq 'note'}
				 reverse @$e;
	  $last_ev->[2] += $dur;
        } else {
          # rest
          $rolling = 0;
          if ($slurring == 2) {
            # legato pedal off
            push @$e, ['control_change', $totaltime, $channel, 68, 0];
          }
          $slurring = 0;
        }
        $totaltime += $dur;
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xc4];

	my ($op1, $op2, $op3) = map {ord} split //, substr($song, $p+1, 3);

        if ($cmd == 0xf0) {
	  # tempo
          $tempo = $op1;
          push @{$cevents},
	     ['set_tempo', $totaltime, int($tempo_factor / $tempo)];
        } elsif ($cmd == 0xf1) {
          # tempo fade
          my ($tdelta, $cb);
          my $bdelta = $op2 - $tempo;
          my $stepinc = $bdelta / $op1;
	  for ($tdelta = 1, $cb = $tempo + $stepinc;
                 $tdelta < $op1; $tdelta++, $cb += $stepinc) {
            #print $totaltime + $tdelta, ",", 
                #       int($balance + $stepinc * $tdelta), "\n";
                #next unless $tdelta % 8 == 0;
             push @{$cevents}, ['set_tempo', $totaltime + $tdelta * 2, 
                        int($tempo_factor / $cb)];
          }
          $tempo = $op2;
       } elsif ($cmd == 0xc4) {
	  # volume
	  $velocity = $op1 & 0x7f;
          #$volume = $op1 & 0x7F;
	  #push @$e, ['control_change', $totaltime, $channel, 7, $volume];
          #$maxvol = $volume if $volume > $maxvol;
          #$minvol = $volume if $volume < $minvol;
        } elsif ($cmd == 0xc5) {
          # volume fade
          $velocity = $op2 & 0x7f;
          my ($tdelta, $cb);
          my $bdelta = ($op2 & 0x7F) - $volume;
          my $stepinc = $bdelta / $op1;
          #print "c5 $op1 $op2 bd $bdelta, si $stepinc\n";
	  for ($tdelta = 1, $cb = $volume + $stepinc;
                 $tdelta < $op1; $tdelta++, $cb += $stepinc) {
            #print $totaltime + $tdelta, ",",
                # int($balance + $stepinc * $tdelta), "\n";
                #next unless $tdelta % 8 == 0;
          #      push @$e, ['control_change', $totaltime + $tdelta * 2,
          #               $channel, 7, int($cb)];
          }
          #$volume = $op2;
          $maxvol = $volume if $volume > $maxvol;
          $minvol = $volume if $volume < $minvol;
	} elsif ($cmd == 0xc6) {
	  # balance/pan
          $balance = $op1 << 1 & 0xFF;
	  push @$e, ['control_change', $totaltime, $channel, 10, $balance >> 1];
	} elsif ($cmd == 0xc7) {
	  # balance/pan fade - $op1 = duration, $op2 = target
          my ($tdelta, $cb);
          my $bdelta = ($op2 << 1 & 0xFF) - $balance;
          my $stepinc = $bdelta / $op1;
          #print "c7 $op1 $op2 bd $bdelta, si $stepinc\n";
	  for ($tdelta = 1, $cb = $balance + $stepinc;
                 $tdelta < $op1; $tdelta++, $cb += $stepinc) {
            #print $totaltime + $tdelta, ",",
                # int($balance + $stepinc * $tdelta), "\n";
                #next unless $tdelta % 8 == 0;
                push @$e, ['control_change', $totaltime + $tdelta * 2,
                         $channel, 10, int($cb) >> 1];
          }
          $balance = $op2;
        } elsif ($cmd == 0xdc) {
	  # patch change
	  my $inst = $op1 >= 0x20 ? $patchmap[$inst[$op1 - 0x20]]
                                  : $patchlo[$op1];
          my $oldchan = $channel;
	  if ($inst < 0) {
	    # percussion
	    $perckey = -$inst;
            $channel = 9;  # 0-based 10
          } else {
	    push @$e, ['patch_change', $totaltime, $v, $inst];
	    $perckey = 0;
            $channel = $v;
	    $t = $op1 >= 0x20 ? $transpose[$inst[$op1 - 0x20]] 
                              : $transplo[$op1];
	  }
          if ($channel != $oldchan) {
            # move control changes from old channel to new channel
            my $i;
            for ($i = $#$e; $i >=0; $i--) {
              last if $e->[$i][0] eq 'note';
            }
            my $switchtime = $i > 0 ? $e->[$i][1] + $e->[$i][2] : 0;
            for ($i++; $i < @$e; $i++) {
              next unless $e->[$i][0] eq 'control_change';
              next unless $e->[$i][1] > $switchtime;
              $e->[$i][2] = $channel;
            }
          }
        } elsif ($cmd == 0xd9) {
          # set transpose
          $transpose = $op1;
        } elsif ($cmd == 0xda) {
          # change transpose
          $transpose += ($op1 & 0x80) ? 
                ((($op1 & 0x7f) ^ 0xff) & 0xff) + 1 : $op1;
	} elsif ($cmd == 0xd6) {
	  # set octave
	  $octave = $op1;
        } elsif ($cmd == 0xd7) {
	  # inc octave
	  $octave++;
	} elsif ($cmd == 0xd8) {
	  # dec octave
	  $octave--;
        } elsif ($cmd == 0xe4) {
          # begin slur
          $slurring = 1;
        } elsif ($cmd == 0xe5) {
          # end slur
          if ($slurring == 2) {
            # legato pedal off
            push @$e, ['control_change', $totaltime, $channel, 68, 0];
          }
          $slurring = 0;
        } elsif ($cmd == 0xe6) {
          # begin roll
          $rolling = 1;
        } elsif ($cmd == 0xe7) {
          # end roll
          $rolling = 1;
        } elsif ($cmd == 0xe8) {
          # utility duration
          $util_dur = $op1;
        } elsif ($cmd == 0xc8) {
          # portamento (max portdist = 99)
          my $porttime = $op1 + 1;
          $portdist = unpack("c", pack("C", $op2)) + $portdist; # signed
          unless ($pwheelrange) {
            $pwheelrange = 1;
            # RPN pitch wheel range - once per voice
            push @$e, ['control_change', $totaltime, $channel, 0x65, 0];
            push @$e, ['control_change', $totaltime, $channel, 0x64, 0];
            # data entry for pwheel range to max - 99 1/2steps
            push @$e, ['control_change', $totaltime, $channel, 6, 99];
          }
          my $startwheel = $oldpwheel;
          my $endwheel = ($portdist < 0 ? -0x2000 : 0x1fff)
                           * abs($portdist) / 99;
#print STDERR "div=", 0x4e/abs($portdist), " endwheel=$endwheel\n";
          my $stepinc = ($endwheel - $startwheel) / $porttime;
#printf STDERR "%d port: pd=%d opd=%d start=%d end=%d step=%d\n", $v, $portdist, $oldpdist, $startwheel, $endwheel, $stepinc;
          for (my ($tdelta, $cb) = (1, $startwheel+$stepinc);
               $tdelta < $porttime; $tdelta++, $cb += $stepinc) {
#printf STDERR "port: td=%02X cb=%04X si=%04x\n", $tdelta, $cb, $stepinc;
            push @$e, ['pitch_wheel_change', $totaltime + $tdelta * 2,
                      $channel, int($cb)];
          }
          $oldpwheel = $endwheel;
          $portdtime = $totaltime;
	} elsif ($cmd == 0xe2) {
	  # begin repeat
	  $rptcnt[++$rpt] = $op1;
	  $rptpos[$rpt] = $p + 1;
	  $rptcur[$rpt] = 0;
	} elsif ($cmd == 0xe3) {
	  if (--$rptcnt[$rpt] < 0) {
	    # all done
	    $rpt--;
	  } else {
	    $p = $rptpos[$rpt];
	    next;
	  }
	} elsif ($cmd == 0xf6) {
          # goto
          my $dest = (($op1 + $op2 * 256) + $vaddroffset - 0x1c00) & 0xffff;
          if ($dest < $p) {  # going backwards?
	    $master_rpt--;
	    if ($master_rpt >= 0 || $totaltime <= ($maxtime - 4 * 96)) {
	      # go round again
              $p = $dest - 1;
	      next;
	    } else {
	      last;
	    }
          } else {
            $p = $dest - 1;
            next;
          }
	} elsif ($cmd == 0xf5) {
	  if (++$rptcur[$rpt] == $op1) {
	    $p = (($op2 + $op3 * 256) + $vaddroffset - 0x1c00 - 1) & 0xffff;
	    $rpt--;
	    next;
	  }
        } elsif (($cmd >= 0xeb && $cmd <= 0xef) || $cmd >= 0xfd) {
	  # halt;
	  last;
	}
        $p += $oplen;  # loop takes care of cmd
      }
    }  # for p
    #print STDERR "v$v tt=$totaltime mt=$maxtime\n";
    if ($totaltime > $maxtime && $v > $maxvoice) {
      $maxtime = $totaltime;
      $maxvoice = $v;
      if ($v > 0) {
        # redo everything
        $cevents = [];   # clear conductor track
        $opus->tracks_r([$ctrack]);  # discard all but ctrack
        $v = 0;
        redo VOICE;
      }
    }
    # set track events from work array
    $track->events_r(MIDI::Score::score_r_to_events_r($e));
    push @{$opus->tracks_r}, $track;
  } # for voice
#print STDERR "vol min=$minvol max=$maxvol\n";
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%02X - %s.mid", $i, $songtitle[$i]);
}

close ROM;
print STDERR "\n";
## the end


sub read2ptrs {
  # read $count 2-byte little-endian pointers from offset $start
  my ($start, $count) = @_;
  my ($buf);
  seek ROM, $start, 0;
  read ROM, $buf, $count * 2;
  return unpack "v$count", $buf;
}

sub read3ptrs {
  # read $count 3-byte little-endian pointers from offset $start
  my ($start, $count) = @_;
  my ($buf);
  seek ROM, $start, 0;
  read ROM, $buf, $count * 3;
  return map { &ptr2sv($_) } unpack ("a3" x $count, $buf); 
}

sub ptr2sv {
  my ($a,$b,$c) = map { ord } split //, $_[0];
  return $a + 0x100 * $b + 0x10000 * ($c & 0x3f) + 0x200;
}

sub s2o {
  # hirom
  my ($snes) = @_;
  $snes =~ s/^(..)\/?(....)$/$1$2/;   # trim bank separator if need be

  my ($bank, $offset) = map { hex } (unpack "A2A4", $snes);
  return ($bank & 0x3f) * 0x10000 + $offset + 0x200; 
}

sub o2s {
  my ($offs) = @_;

  my ($bank, $offset);
  $offs -= 0x200;  # trim smc header
  $bank = int($offs / 0x10000) | 0xc0;
  $offset = $offs & 0xFFFF;
  return sprintf "%02X/%04X", $bank, $offset;
}

