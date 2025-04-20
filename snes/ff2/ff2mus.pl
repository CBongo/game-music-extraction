#!/usr/bin/perl

# TODO:
#
# * volume/pan/tempo gradients
# * tweak volume/octave for patches (log volume?)
# * song 1D - zero length notes
#

use MIDI;
use Data::Dumper;

open ROM, "< ff2.smc"
  or die "open failed: $!\n";

$base = s2o('04/C000');

$tempo_factor = 55_296_000;  # timer0 period 4.5ms * 96/2 ticks * 256

@songtitle = ("(dummy)", "Overture", "Elder", "short fanfare (non US)",
              "Chocobo", "Mambo de Chocobo", "Underworld", "Zeromus",
              "Victory", "Town", "Rydia's Theme", "Battle with Fiends",
              "Mountain", "Overworld", "Big Whale", "Sadness",
              "Take a Nap", "Golbez' Theme", "Edward's Theme",
              "Rosa's Theme", "Baron", "Prelude", "Evil Golbez",
              "Tower of Babil", "Airship", "Fiends' Theme", "Boss",
              "Giant of Babil", "Illusionary World", "Ring of Bombs",
              "Lunar Cave", "Surprise", "Dwarf Castle", "Palom and Porom",
              "Calbrena", "Run!!", "Cid's Theme", "Cave", "Dance",
              "Battle", "Damcyan", "Fanfare", "Sorrow", "Chocobo Forest",
              "Red Wings", "Suspicion", "Fabul",
              "Cecil becomes a Paladin", "Big Chocobo", "Moon",
              "Toroia Castle", "Mysidia", "Castle - Red Wings 2",
	      "Ending", "Epilogue", "Credits", "(dummy)", "(dummy)",
	      "(dummy)", "(dummy)", "(dummy)", "(dummy)", "??",
	      "Gongs & Foghorn?", "Door open?", "Door open?",
	      "Earthquake", "Fall Down", "Leviathan Rises", "(dummy)");

%musicop = (0xd2 => "Tempo", 0xd3 => "nop", 0xd4 => "Echo Volume",
            0xd5 => "Echo Settings", 0xd6 => "Portamento Settings",
            0xd7 => "Tremolo Settings", 0xd8 => "Vibrato Settings",
            0xd9 => "Pan Sweep Settings", 0xda => "Set Octave",
            0xdb => "Set Patch", 0xdc => "Set Envelope", 
            0xdd => "Set Gain (Exp Dec Time)", 
            0xde => "Set Staccato (note dur ratio)",
            0xdf => "Set Noise Clock", 0xe0 => "Start Repeat", 
            0xe1 => "Inc Octave", 0xe2 => "Dec Octave", 0xe3 => "nop", 
            0xe4 => "nop", 0xe5 => "nop", 0xe6 => "Portamento Off", 
            0xe7 => "Tremolo Off", 0xe8 => "Vibrato Off", 
            0xe9 => "Pan Sweep Off", 0xea => "Enable Echo", 
            0xeb => "Disable Echo", 0xec => "Enable Noise", 
            0xed => "Disable Noise", 0xee => "Enable Pitchmod", 
            0xef => "Disable Pitchmod", 0xf0 => "End Repeat", 
            0xf1 => "Halt", 0xf2 => "Voice Volume", 0xf3 => "Voice Balance",
            0xf4 => "Goto", 0xf5 => "Selective Repeat",
            0xf6 => "Goto 0760+X", 0xf7 => "Halt", 0xf8 => "Halt",
            0xf9 => "Halt", 0xfa => "Halt", 0xfb => "Halt",
            0xfc => "Halt", 0xfd => "Halt", 0xfe => "Halt", 0xff => "Halt");

@notes = ("C ", "C#", "D ", "D#", "E ", "F ",
          "F#", "G ", "G#", "A ", "A#", "B ");

@patchmap  = (    0,  48,  46,   0,   0,  19,  56,  73, 13,  34, 47, 43,
		-40, -36, -38, -35, -49, -42, 113, -69, 85, 117, -72); 

@transpose = (  0, +1, +1,  0,  +1, +2,   0, +1,  0,  -2, -1, -1,
		0,  0,  0,  0,   0,  0,   0,  0,  0,  +1,  0);

$numsongs = 0x46;
if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x $numsongs;
}

@ptr{qw(songtbl samptbl songinst srcdir ff40)}
   = map {$base + $_} &read3ptrs($base, 5);

@songptrs = map {$base + $_} &read3ptrs($ptr{songtbl}, $numsongs);
@sampptrs = map {$base + $_} &read3ptrs($ptr{samptbl}, 0x17);

# get SPC data - music ops lengths, pitch table, duration table
seek ROM, s2o('04/96D0'), 0;
read ROM, $buf, 46;
@musoplen = unpack "C46", $buf;

read ROM, $buf, 26;
@pitchtbl = unpack "v13", $buf;

seek ROM, s2o('04/9738'), 0;
read ROM, $buf, 15;
@durtbl = unpack "C15", $buf;


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
                 &read2ptrs($ptr{songinst} + $i * 0x20, 0x10);
  my (@vstart) = unpack "v8", $song;

  my $maxtime = 0;
  my $maxvoice = -1;

VOICE:
  for ($v = 0; $v < 8; $v++) {
    next if $vstart[$v] < 0x100;

    my $track = new MIDI::Track;

    my $totaltime = 0;
    my $master_rpt = 1;
    
    my $tempo = 255;
    my $balance = 0x80;
    my $velocity = 64;
    my $octave = 4;
    my $durmult = 0;
    my $perckey = 0;   # key to use for percussion
    my $t = 0;  # transpose octaves
    my $portdelay = 0;
    my $porttime = 0;
    my $portdist = 0;
    my $portcount = 0;
    my $rptidx = 0;
    my (@rptcnt, @rptpos, @rptcur);
    my $e = []; # track events
    push @$e, ['track_name', 0, "Voice $v"];
    push @$e, ['control_change', 0, $v, 0, 0]; # bank 0

    for ($p = $vstart[$v] - 0x2000; $p < $len; $p++) {
      #last if grep {$_ == $p + 0x2000 && $_ ne $vstart[$v]} @vstart;

      my $cmd = ord(substr($song, $p, 1));
      if ($cmd < 0xd2) {
        # note
        my ($note, $dur) = (int($cmd/15), $cmd % 15);
        $dur = $durtbl[$dur] << 1;

        if ($note < 12) {
	  my $mnote = 12 * ($octave + $t) + $note;
	  my $channel = $v;
	  if ($perckey) {
	    # substitute appropriate percussion key on chan 10
	    $channel = 9;  # 10 zero based
	    $mnote = $perckey;
	  }

          if ($portdist) {
            $mnote += $portdist;
            my $startwheel = ($portdist < 0 ? 0x1fff : -0x2000);
            my $stepinc = -$startwheel / $porttime;
#printf STDERR "%d port: pd=%d opd=%d start=%d end=%d step=%d\n", $v, $portdist, $oldpdist, $startwheel, $endwheel, $stepinc;
            for ($portcount = 0;
                 $portcount + $portdelay <= $dur && $portcount <= $porttime;
                 $portcount++) {
                push @$e, ['pitch_wheel_change',
                           $totaltime + ($portcount ? $portdelay : 0)
                                 + $portcount * 2,
                           $channel, int($startwheel + $portcount * $stepinc)];
            }
          }
          push @$e, ['note', $totaltime,
                     $durmult > 0 ? int($dur * $durmult) : $dur - 1,
                     $channel, $mnote, $velocity];
        } elsif ($note == 12) {
	  # rest
        } else {
	  # tie
	  my ($last_ev) = grep {$_->[0] eq 'note'}
				 reverse @$e;
	  $last_ev->[2] += $dur;
          if ($portdist) {
            # pick up where we left off
            my $startwheel = ($portdist < 0 ? 0x1fff : -0x2000);
            my $stepinc = -$startwheel / $porttime;
#printf STDERR "%d port: pd=%d opd=%d start=%d end=%d step=%d\n", $v, $portdist, $oldpdist, $startwheel, $endwheel, $stepinc;
            # TODO: take portdelay into account for < dur comparison
            for (my $tdelta = 0; $tdelta <= $dur && $portcount <= $porttime;
                 $tdelta++, $portcount++) {
                push @$e, ['pitch_wheel_change',
                           $totaltime + $portdelay + $portcount * 2,
                           $channel, int($startwheel + $portcount * $stepinc)];
            }
          }
        }
        $totaltime += $dur;
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xd2];
	my ($op1, $op2, $op3) = map {ord} split //, substr($song, $p+1, 3);

        if ($cmd == 0xd2) {
	  # set/fade tempo
          my $rate = $op1 + 0x100 * $op2;
          if ($rate) {
            my ($tdelta, $cb);
            my $bdelta = $op3 - $tempo;
            my $stepinc = $bdelta / $rate;
	    for ($tdelta = 1, $cb = $tempo + $stepinc;
                   $tdelta < $rate; $tdelta++, $cb += $stepinc) {
              push @{$cevents}, ['set_tempo', $totaltime + $tdelta * 2, 
                                 int($tempo_factor / $cb)];
            }
          } else {
            push @{$cevents},
	      ['set_tempo', $totaltime, int($tempo_factor / $op3)];
          }
          $tempo = $op3;
        } elsif ($cmd == 0xf2) {
	  # volume
	  $velocity = $op3 >> 1;
	} elsif ($cmd == 0xf3) {
	  # balance/pan
          my $rate = $op1 + 0x100 * $op2;
          if ($rate) {
            my ($tdelta, $cb);
            my $bdelta = $op3 - $balance;
            my $stepinc = $bdelta / $rate;
            
	    for ($tdelta = 1, $cb = $balance + $stepinc;
                   $tdelta < $rate; $tdelta++, $cb += $stepinc) {
                  # TODO: skip if same result as last time
                  push @$e, ['control_change', $totaltime + $tdelta * 2,
                           $channel, 10, int($cb) >> 1];
            }
          } else {
	    push @$e, ['control_change', $totaltime, $v, 10, $op3 >> 1];
          }
          $balance = $op3;
        } elsif ($cmd == 0xdb) {
	  # patch change
	  my $inst = $inst[$op1 - 0x40];
	  if ($patchmap[$inst] < 0) {
	    # percussion
	    $perckey = -$patchmap[$inst];
          } else {
	    push @$e, ['patch_change', $totaltime, $v, $patchmap[$inst]];
	    $perckey = 0;
	    $t = $transpose[$inst];
	  }
	} elsif ($cmd == 0xda) {
	  # set octave
	  $octave = $op1;
        } elsif ($cmd == 0xe1) {
	  # inc octave
	  $octave++;
	} elsif ($cmd == 0xe2) {
	  # dec octave
	  $octave--;
        } elsif ($cmd == 0xd6) {
          # portamento
          $portdelay = $op1 + 1;
          $porttime = $op2;
          $portdist = unpack "c", pack("C", $op3);
          # RPN pitch wheel range
          push @$e, ['control_change', $totaltime, $channel, 0x65, 0];
          push @$e, ['control_change', $totaltime, $channel, 0x64, 0];
          # data entry for pwheel range
          push @$e, ['control_change', $totaltime, $channel,
                     6, abs $portdist];
        } elsif ($cmd == 0xe6) {
          # end portamento
          $portdist = 0;
        } elsif ($cmd == 0xde) {
          # dur %
          if ($op1 >= 100) {
            $durmult = 0;
          } else {
            $durmult = $op1/100;
          }
	} elsif ($cmd == 0xe0) {
	  # begin repeat
#printf STDERR "%04x: 0xe0 - $rpt\n", $p + 0x2000;
	  $rptcnt[++$rpt] = $op1;
	  $rptpos[$rpt] = $p + 1;
	  $rptcur[$rpt] = 0;
	} elsif ($cmd == 0xf0) {
	  # end repeat
#printf STDERR "%04x: 0xf0 - $rpt, $rptcnt[$rpt]\n", $p + 0x2000;
	  if (--$rptcnt[$rpt] < 0) {
	    # all done
	    $rpt--;
	  } else {
	    $p = $rptpos[$rpt];
	    next;
	  }
	} elsif ($cmd == 0xf4) {
#printf STDERR "%04x: 0xf4\n", $p + 0x2000;
	  $master_rpt--;
#print STDERR "** $v: rpt = $master_rpt, tot = $totaltime, max=$maxtime\n";
	  if ($master_rpt >= 0 || $totaltime <= ($maxtime - 4 * 96)) {
	    # go round again
	    $p = ($op1 + $op2 * 256) - 0x2000 - 1;
	    next;
	  } else {
	    last;
	  }
	} elsif ($cmd == 0xf5) {
#printf STDERR "%04x: 0xf5 - $rpt, $rptcur[$rpt]\n", $p + 0x2000;
	  if (++$rptcur[$rpt] == $op1) {
	    $p = ($op2 + $op3 * 256) - 0x2000 - 1;
	    $rpt--;
	    next;
	  }
        } elsif ($cmd == 0xf1 || $cmd >= 0xf7) {
	  # halt;
	  last;
	}
        $p += $oplen;  # loop takes care of cmd
      }
    }  # for p
#print STDERR "$v: tot = $totaltime, max=$maxtime\n";
    if ($totaltime > $maxtime && $v > $maxvoice) {
      $maxtime = $totaltime;
      $maxvoice = $v;
      if ($v > 0) {
        # redo everything
        $ctrack->events_r([]);   # clear conductor track
        $opus->tracks_r([$ctrack]);  # discard all but ctrack
        $v = 0;
        redo VOICE;
      }
    }
    $track->events_r(MIDI::Score::score_r_to_events_r($e));
    push @{$opus->tracks_r}, $track;
  }
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
  return $a + 0x100 * $b + 0x10000 * $c;
}

sub s2o {
  my ($snes) = @_;
  $snes =~ s/^(..)\/(....)$/$1$2/;   # trim bank separator if need be

  my ($bank, $offset) = map { hex } (unpack "A2A4", $snes);
  return $bank * 0x8000 + ($offset - 0x8000) + 0x200; 
}

sub o2s {
  my ($offs) = @_;

  my ($bank, $offset);
  $offs -= 0x200;  # trim smc header
  $bank = int($offs / 0x8000);
  $offset = $offs % 0x8000 + 0x8000;
  return sprintf "%02X/%04X", $bank, $offset;
}

