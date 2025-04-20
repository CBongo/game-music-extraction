#!/usr/bin/perl

# 25 Feb 2001  cab@tc.umn.edu

use MIDI;
use Data::Dumper;

open ROM, "< chrono.smc"
  or die "open failed: $!\n";

$base = s2o('C7/0000');
$spcbase = $base + 0x24c3 + 2 - 0x200;

$tempo_factor = 64_512_000;  # timer0 period 5.25ms * 96/2 ticks * 256

@songtitle = ("00", "Memories of Green", "Wind Scene", "Corridors of Time",
              "Rhythm of Wind, Sky, and Earth", "Ruined World",
              "Guardia Millenial Fair", "Far Off Promise",
              "Secret of the Forest", "Zeal Palace", "Remains of Factory",
              "Ayla's Theme", "Courage and Pride", "Lavos' Theme",
              "Robo's Theme", "Morning Sunlight", "Manoria Cathedral",
              "11", "FX-Leene's Bell", "Wings That Cross Time",
              "Schala's Theme", "Delightful Spekkio", "A Shot of Crisis",
              "Kingdom Trial", "Chrono Trigger (a)", "FX-19", "FX-1a",
              "Fanfare 1", "Fanfare 2", "At the Bottom of Night",
              "Peaceful Day", "A Strange Happening", "FX-Drips",
              "FX-21", "FX-22", "The Hidden Truth",
              "A Prayer to the Road That Leads", "Huh",
              "The Day the World Revived", "Robo Gang Johnny",
              "Battle with Magus", "Boss Battle 1", "Kaeru's Theme",
              "Goodnight", "Bike Chase",
              "People Who Threw Away the Will to Live",
              "Mystery of the Past", "Underground Sewer", "Presentiment",
              "Undersea Palace", "Last Battle", "Dome 16's Ruin",
              "FX-Heartbeat (Slow)", "FX-35", "Burn Bobonga", "FX-37",
              "Primitive Mountain", "World Revolution", "FX-3a",
              "Sealed Door", "Silent Light", "Fanfare 3",
              "The Brink of Time", "To Far Away Times",
              "Confusing Melody", "FX-41", "Gonzalez' Song", "FX-43",
              "Black Dream", "Battle 1", "Tyran Castle", "FX-47",
              "Magus' Castle", "First Festival of Stars", "FX-4a", "FX-4b",
              "FX-4c", "Epilogue - To Good Friends", "Boss Battle 2",
              "Lose", "Determination", "Battle 2", "Singing Mountain");

@patchmap  = (0,-39,-64,-62,-54,-35,-40,-42,  # 00-07
              -46,47,-75,-48,-45,-62,-63,-76,  # 08-0F
              -64,0,66,0,0,45,19,73,  # 10-17
              4,33,32,54,5,21,0,-69,  # 18-1F
              -51,10,0,0,56,-38,0,-49,  # 20-27
              0,0,0,13,48,11,0,7,  # 28-2F
              0,0,75,0,17,0,0,0, # 30-37
              73,0,52,0,0,0,0,0); # 38-3F
@patchlo   = (80, 81); # 00-01

@transpose = (0,0,0,0,0,0,0,0,  # 00-07
              0,+1,0,0,0,0,0,0,  # 08-1F
              0,0,-1,+2,0,0,0,+1,  # 10-17
              +1,-2,-2,0,0,0,0,0,  # 18-1F
              0,+1,0,0,0,0,0,0,  # 20-27
              0,0,0,+1,+1,0,0,-1,  # 28-2F
              0,0,+1,0,+1,0,0,0, # 30-37
              0,0,0,0,0,0,0,0); # 38-3F
@transplo  = (0,0); # 00-01

seek ROM, $base + 0x0ae9, 0;
read ROM, $buf, 1;
$numsongs = ord $buf;

if (@ARGV && $ARGV[0] eq '-b') {
  shift;
  $doalt = 1;
}
if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x $numsongs;
}

@songptrs = &read3ptrs($base + 0x0d18, $numsongs);

# get SPC data - music ops lengths, pitch table, duration table
seek ROM, $spcbase + 0x15cc, 0;
read ROM, $buf, 60;
@musoplen = unpack "C60", $buf;

seek ROM, $spcbase + 0x1d6d, 0;
read ROM, $buf, 26;
@pitchtbl = unpack "v13", $buf;

seek ROM, $spcbase + 0x1db3, 0;
read ROM, $buf, 14;
@durtbl = unpack "C14", $buf;

for ($i = 0, $numdone=0; $i <= $numsongs; $i++) {
  next unless $dosong[$i];
  my ($opus);

  printf STDERR "%02X ", $i;
  print STDERR "\n" if ++$numdone % 16 == 0;

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
                 &read2ptrs($base + 0x0e11 + $i * 0x20, 0x10);

  # perc mode maps
  seek ROM, $base + 0x1871 + $i * 0x24, 0;
  read ROM, $buf, 0x24;
  my (@percinst);
  for ($p = 0; $p < 0x24; $p += 3) {
    my ($inst, $note, $vol) = unpack "C3", substr($buf, $p, 3);
    push @percinst, { 'instr' => $inst, 'note' => $note, 'vol' => $vol};
  }

  my ($vaddroffset, $emptyvoice)  =  unpack "v2", $song;
#printf STDERR "voff=%04x, ev=%04x\n", $vaddroffset, $emptyvoice;
  $vaddroffset = (0x12024 - $vaddroffset) & 0xffff;
#printf STDERR "voff2=%04x\n", $vaddroffset;
  my (@vstart) = unpack "v8", substr($song, 4, 0x10);
  my (@vstart2) = unpack "v8", substr($song, 0x14, 0x10);
  next if $doalt && (substr($song, 4, 0x10) eq substr($song, 0x14, 0x10));
#print STDERR "vs1: ", join(",", map {sprintf "%04x", $_} @vstart), "\n"; 
#print STDERR "vs2: ", join(",", map {sprintf "%04x", $_} @vstart2), "\n"; 
  @vstart = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart;
  @vstart = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart2
    if $doalt;
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
    my $vmult = 255/256;
    my $balance  = 128;
    my $tempo    = 120;
    my $channel  = $v;
    my $octave = 4;
    my $perckey = 0;   # key to use for percussion
    my $percmode = 0;
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
    push @$e, ['track_name', 0, "Voice $v"];
    push @$e, ['control_change', 0, $channel, 0, 0]; # bank 0
  
    for ($p = $vstart[$v] - 0x2000; $p < $len; $p++) {
      #last if grep {$_ == $p + 0x2000 && $_ ne $vstart[$v]} @vstart;

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
        if ($percmode) {
          my $percinst = $percinst[$note]{'instr'};
          my $newperckey = -($percinst >= 0x20 ? $patchmap[$inst[$percinst-0x20]]
                                               : $patchlo[$percinst]);
          if ($newperckey == 64 && $percinst[$note]{'note'} > 0x3a) {
            $newperckey = 63;
          }
          if ($newperckey == 48) {
            $newperckey = 50 if $percinst[$note]{'note'} > 0x45;
            $newperckey = 47 if $percinst[$note]{'note'} < 0x45;
          }
#print STDERR "note=$note pi=$percinst i=$inst[$percinst-0x20] npk=$newperckey pk=$perckey\n";
          if ($newperckey != $perckey) {
             $channel = 9;
             $perckey = $newperckey;
             $velocity = &multvol($percinst[$note]{'vol'}, $vmult);
          }
        }
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
	     ['set_tempo', $totaltime, &calctempo($tempo)];
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
                        &calctempo($cb)];
          }
          $tempo = $op2;
       } elsif ($cmd == 0xc4) {
	  # volume
	  $velocity = &multvol($op1 & 0x7f, $vmult);
          #$volume = $op1 & 0x7F;
	  #push @$e, ['control_change', $totaltime, $channel, 7, $volume];
          #$maxvol = $volume if $volume > $maxvol;
          #$minvol = $volume if $volume < $minvol;
        } elsif ($cmd == 0xc5) {
          # volume fade
          $velocity = &multvol($op2 & 0x7f, $vmult);
          my ($tdelta, $cb);
          my $bdelta = ($op2 & 0x7F) - $volume;
          my $stepinc = $bdelta / $op1;
          #print "c5 $op1 $op2 bd $bdelta, si $stepinc\n";
	  for ($tdelta = 1, $cb = $volume + $stepinc;
                 $tdelta < $op1; $tdelta++, $cb += $stepinc) {
            #print $totaltime + $tdelta, ",",
                # int($balance + $stepinc * $tdelta), "\n";
                #next unless $tdelta % 8 == 0;
                #push @$e, ['control_change', $totaltime + $tdelta * 2,
                #         $channel, 7, int($cb)];
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
        } elsif ($cmd == 0xfb) {
          # perc mode on
          $percmode = 1;
        } elsif ($cmd == 0xfc) {
          # perc mode off
          $percmode = 0;
          $channel = $v;
        } elsif ($cmd == 0xfd) {
          # volume multiplier
          $vmult = $op1 / 256;
        } elsif ($cmd == 0xc8) {
          # portamento (max portdist = 0x4e)
          my $porttime = $op1 + 1;
          $portdist = unpack("c", pack("C", $op2)) + $portdist; # signed
          unless ($pwheelrange) {
            $pwheelrange = 1;
            # RPN pitch wheel range - once per voice
            push @$e, ['control_change', $totaltime, $channel, 0x65, 0];
            push @$e, ['control_change', $totaltime, $channel, 0x64, 0];
            # data entry for pwheel range to max - $4e 1/2steps
            push @$e, ['control_change', $totaltime, $channel, 6, 0x4e];
          }
          my $startwheel = $oldpwheel;
          my $endwheel = ($portdist < 0 ? -0x2000 : 0x1fff)
                           * abs($portdist) / 0x4e;
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
          my $dest = (($op1 + $op2 * 256) + $vaddroffset - 0x2000) & 0xffff;
          if ($dest < $p) {  # going backwards?
	    $master_rpt--;
	    if ($master_rpt > 0 || $totaltime <= ($maxtime - 4 * 96)) {
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
	    $p = (($op2 + $op3 * 256) + $vaddroffset - 0x2000 - 1) & 0xffff;
	    $rpt--;
	    next;
	  }
        } elsif (($cmd >= 0xeb && $cmd <= 0xef) || $cmd >= 0xfe) {
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
  $opus->write_to_file(sprintf(($doalt ? "mid/%02Xx-%s.mid" : "mid/%02X-%s.mid"), $i, $songtitle[$i]));
}

close ROM;
print STDERR "\n";
## the end


sub multvol {
  my ($vol, $mult) = @_;
  my $newvol = int($vol * (0.5 + $mult));
  $newvol = 0x7f if $newvol > 0x7f;
  return $newvol;
}

sub calctempo {
  my ($tempo) = @_;
  $tempo = int($tempo * 0x14 / 0x100) + $tempo;
  return int($tempo_factor / $tempo);
}

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

