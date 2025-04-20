#!/usr/bin/perl

# 25 Feb 2001  cab@tc.umn.edu

use MIDI;
use Data::Dumper;

open ROM, "< seiken3e.smc"
  or die "open failed: $!\n";

$base = s2o('C5/0000');
$spcbase = $base + 0x8bd + 2 - 0x200;


@songtitle = ("00", "Whiz Kid", "Left-Handed Wolf", "Raven", "Witchmakers",
              "Evening Star", "Female Turbulence", "Three of Darkside",
              "Ordinary People", "Ancient Dolphin", "Political Pressure",
              "Few Paths Forbidden", "Little Sweet Cafe", "Swivel",
              "Harvest November", "Walls and Steels", "Powell",
              "Damn Damn Drum", "Don't Hunt the Fairy", "Another Winter",
              "Different Road", "Weird Counterpoint", "Electric Talk",
              "Hope Isolation Pray", "Intolerance", "Fable", "Innocent Sea",
              "Delicate Affection", "Meridian Child", "Nuclear Fusion",
              "Sacrifice, Part 1", "Strange Medicine", "Obsession",
              "Rolling Cradle", "High Tension Wire", "Faith Total Machine",
              "Secret of Mana", "Black Soup", "Frenzy", "Innocent Water",
              "Can You Fly, Sister", "Splash Hop", "Closed Garden",
              "Where Angels Fear to Tread", "Farewell Song", "Positive",
              "And Other", "Long Goodbye", "Axe Bring Storm",
              "Last Audience", "(fx) Wind", "Oh I'm a Flamelet",
              "(fx) Heartbeat", "(fx) Ghost Ship", "(fx) Drumroll",
              "(fx) Cannon", "(fx) Flammie", "Person's Die",
              "Angel's Fear", "Religion Thunder", "Sacrifice, Part 2",
              "(fx) Sleeping", "3e", "Legend", "Decision Bell",
              "(fx) Level Up", "Not Awaken", "Sacrifice, Part 3",
              "(fx) Pihyara Flute", "(fx) Wind Drum", "Breezin",
              "Return to Forever", "48", "(fx) Gate", "(fx) Mana Seed",
              "Reincarnation");

@patchmap  = (    0,  -40,  -36,  -44,    0,    0,    4,   73, # 10-17
                  0,  108,   12,   24,    0,  116,   32,   33, # 18-1F
                -54,   43,    0,   52,   48,   14,   86,    0, # 20-27
                  0,  -38,  -40,   63,    0,    0,    0,    0, # 28-2F
                  0,    0,    0,    0);                        # 30-33

@transpose = (  0, 0, 0, 0, +1, 0, +2, +1,  # 10-17
                0, 0, -1, 0, 0, -1, 0, -3,  # 18-1F
                0, -1, 0, 0, +1, 0, 0, 0,  # 20-27
                0, 0, 0, +1, 0, 0, 0, 0,  # 28-2F
                0, 0, 0, 0);             # 30-33


seek ROM, $base + 0x2064, 0;
read ROM, $buf, 1;
$numsongs = ord $buf;

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x $numsongs;
}  

@songptrs = &read3ptrs($base + 0x2065, $numsongs);

# get SPC data - music ops lengths, pitch table, duration table
seek ROM, $spcbase + 0x1765, 0;
read ROM, $buf, 60;
@musoplen = unpack "C60", $buf;

seek ROM, $spcbase + 0x17af, 0;
read ROM, $buf, 26;
@pitchtbl = unpack "v13", $buf;

seek ROM, $spcbase + 0x17a1, 0;
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

  # grab instrument table
  seek ROM, $songptrs[$i], 0;
  read ROM, $buf, 0x43;  # catch up to $20 samples plus FF terminator & size
  my (@inst, @instvol, $len);
  for ($p = 0; $p < length $buf; $p+=2) {
    my ($inst, $instvol) = unpack "C2", substr($buf, $p, 2);
    if ($inst == 0xff) {
      $len = unpack "v", substr($buf, $p + 1, 2);
      last;
    }
    push @inst, $inst;
    push @instvol, $instvol;
  }

  seek ROM, $songptrs[$i] + @inst * 2 + 3, 0;
  # read all song data
  read ROM, $song, $len;

  my (@vstart) = unpack "v8", substr($song, 0, 0x10);
  my (%percinst);
  for ($p = 0x10; $p < $len; $p += 5) {
    my ($key, $instr, $note, $vol, $pan) = unpack "C5", substr($song, $p, 5);
    last if $key == 0xff;
    $percinst{$key} = { 'instr' => $instr, 'note' => $note, 'vol' => $vol,
                        'pan' => $pan };
  }

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
    my $percmode = 0;
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
    my $loop = 0;
    my $rpt = 0;
    my (@rptcnt, @rptpos, @rptcur, @rptoct);
    my $e = []; # track events
    push @$e, ['track_name', 0, "Voice $v"];
    push @$e, ['control_change', 0, $channel, 0, 0]; # bank 0

    for ($p = $vstart[$v] - 0x2000; $p < $len; $p++) {
      #last if grep {$_ == $p + 0x1c00 && $_ ne $vstart[$v]} @vstart;

      my $cmd = ord(substr($song, $p, 1));
      if ($cmd < 0xc4) {
        # note
        my ($dur, $note) = (int($cmd/14), $cmd % 14);
        if ($dur == 13) {
          $dur = ord(substr($song, ++$p, 1));
          printf OUT " %02X", $dur;
        } else {
          $dur = $durtbl[$dur];
          print OUT "   ";
        }
        $dur = ($dur + 1) << 1;

        if ($note < 12) {
          my $notedur = $dur;
          my $mnote = 12 * ($octave + $t) + $note + $transpose;
          if ($percmode) {
#printf STDERR "%02x: %02x %02x %02x %02x\n", $note, $percinst{$note}{instr}-0x10, $patchmap[$percinst{$note}{instr}-0x10], $percinst{$note}{vol}, $percinst{$note}{pan};
            my $newperckey = -$patchmap[$percinst{$note}{instr}-0x10];
            if ($newperckey != $perckey) {
              $channel = 9;  # 0-based 10
              $perckey = $newperckey;
              #$velocity = $percinst{$note}{vol};
              #$balance = $percinst{$note}{pan};
  	      #push @$e, ['control_change', $totaltime, $channel, 10, $balance >> 1];
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
          if ($oldpwheel && $portdtime != $totaltime) {
            push @$e, ['pitch_wheel_change', $totaltime, $channel, 0];
            $oldpwheel = 0;
            $portdist = 0;
          }
          push @$e, ['note', $totaltime, $notedur, $channel, $mnote, $velocity];
        } elsif ($note == 13) {
          # tie
          my ($last_ev) = grep {$_->[0] eq 'note'}
				 reverse @$e;
	  $last_ev->[2] += $dur;
        } else {
          # rest
        }
        $totaltime += $dur;
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xc4];
        $oplen = 1 if $oplen == 0xff || $oplen == 0;
        $oplen--;

	my ($op1, $op2, $op3, $op4) = unpack "C4", substr($song, $p+1, 4);

        if ($cmd == 0xd1) {
	  # tempo
          $tempo = $op1;
          push @{$cevents},
	     ['set_tempo', $totaltime, $tempo * 125 * 48];
        } elsif ($cmd == 0x00) {
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
       } elsif ($cmd == 0xe0 || $cmd == 0xe2) {
	  # volume
	  $velocity = $op1 & 0x7f;
          #$volume = $op1 & 0x7F;
	  #push @$e, ['control_change', $totaltime, $channel, 7, $volume];
          #$maxvol = $volume if $volume > $maxvol;
          #$minvol = $volume if $volume < $minvol;
        } elsif ($cmd == 0xe4) {
          # volume fade
          #$velocity = $op2 & 0x7f;
          my ($tdelta, $cb);
          my $bdelta = ($op2 & 0x7F) - $volume;
          my $stepinc = $bdelta / $op1;
          #print "c5 $op1 $op2 bd $bdelta, si $stepinc\n";
	  for ($tdelta = 1, $cb = $volume + $stepinc;
                 $tdelta < $op1; $tdelta++, $cb += $stepinc) {
            #print $totaltime + $tdelta, ",",
                # int($balance + $stepinc * $tdelta), "\n";
                #next unless $tdelta % 8 == 0;
                push @$e, ['control_change', $totaltime + $tdelta * 2,
                         $channel, 7, int($cb)];
          }
          $volume = $op2;
          $maxvol = $volume if $volume > $maxvol;
          $minvol = $volume if $volume < $minvol;
	} elsif ($cmd == 0xe7) {
	  # balance/pan
          $balance = $op1;
	  push @$e, ['control_change', $totaltime, $channel, 10, $balance >> 1];
	} elsif ($cmd == 0xe8) {
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
        } elsif ($cmd == 0xde) {
	  # patch change
	  my $inst = $patchmap[$op1 - 0x10];
          my $oldchan = $channel;
	  if ($inst < 0) {
	    # percussion
	    $perckey = -$inst;
            $channel = 9;  # 0-based 10
          } else {
	    push @$e, ['patch_change', $totaltime, $v, $inst];
	    $perckey = 0;
            $channel = $v;
	    $t = $transpose[$op1 - 0x10];
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
        } elsif ($cmd == 0x00) {
          # set transpose
          $transpose = $op1;
        } elsif ($cmd == 0x00) {
          # change transpose
          $transpose += ($op1 & 0x80) ? 
                ((($op1 & 0x7f) ^ 0xff) & 0xff) + 1 : $op1;
	} elsif ($cmd == 0xc6) {
	  # set octave
	  $octave = $op1;
        } elsif ($cmd == 0xc4 || $cmd == 0xf6 || $cmd == 0xfe || $cmd == 0xff) {
	  # inc octave
	  $octave++;
	} elsif ($cmd == 0xc5) {
	  # dec octave
	  $octave--;
        } elsif ($cmd == 0xf8) {
          # begin slur
          $slurring = 1;
        } elsif ($cmd == 0xf9) {
          # end slur
          $slurring = 0;
        } elsif ($cmd == 0xee) {
          # perc mode on
          $percmode = 1;
        } elsif ($cmd == 0xef) {
          # perc mode off
          $percmode = 0;
        } elsif ($cmd == 0xe5) {
          # portamento (max portdist = 36)
          my $porttime = $op1 - 1;
          $portdist = unpack("c", pack("C", $op2)) + $portdist; # signed
          unless ($pwheelrange) {
            $pwheelrange = 1;
            # RPN pitch wheel range - once per voice
            push @$e, ['control_change', $totaltime, $channel, 0x65, 0];
            push @$e, ['control_change', $totaltime, $channel, 0x64, 0];
            # data entry for pwheel range to max - 36 1/2steps
            push @$e, ['control_change', $totaltime, $channel, 6, 36];
          }
          my $startwheel = $oldpwheel;
          my $endwheel = ($portdist < 0 ? -0x2000 : 0x1fff)
                           * abs($portdist) / 36;
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
	} elsif ($cmd == 0xd2 || $cmd == 0xd3 || $cmd == 0xd4) {
	  # begin repeat
#print STDERR "$v BEGIN rpt=$rpt\n";
	  $rptcnt[++$rpt] = $op1;
	  $rptpos[$rpt] = $p + 1;
	  $rptcur[$rpt] = 0;
          $rptoct[$rpt] = $octave;
	} elsif ($cmd == 0xd5) {
#printf STDERR " END  rpt=$rpt p=%04x\n", $p + 0x2000;
	  if (--$rptcnt[$rpt] <= 0) {
	    # all done
#print STDERR "$v  END2 rpt=$rpt\n";
	    $rpt--;
	  } else {
	    $p = $rptpos[$rpt];
            $octave = $rptoct[$rpt];
	    next;
	  }
        } elsif ($cmd == 0xd7) {
          # mark loop point
          $loop = $p;
#printf STDERR "$v loop=%04x\n", $loop;
	} elsif ($cmd == 0xd0) {
          # goto
          if ($loop) {
            $master_rpt--;
	    if ($master_rpt > 0 || $totaltime <= ($maxtime - 4 * 96)) {
	      # go round again
              $p = $loop;
	      next;
	    } else {
	      last;
	    }
          } else {
           last;
          }
	} elsif ($cmd == 0x00) {
	  if (++$rptcur[$rpt] == $op1) {
	    $p = (($op2 + $op3 * 256) + $vaddroffset - 0x1c00 - 1) & 0xffff;
	    $rpt--;
	    next;
	  }
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
  $opus->write_to_file(sprintf "mid/%02X-%s.mid", $i, $songtitle[$i]);
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

