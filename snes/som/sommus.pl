#!/usr/bin/perl

# 27 Mar 2001  cab@tc.umn.edu

use MIDI;
use Data::Dumper;

open ROM, "< som1.smc"
  or die "open failed: $!\n";

$base = s2o('C3/0000');
$spcbase = $base + 0x748 + 2 - 0x200;

#$tempo_factor = 56_075_000;  # 60M is standard MIDI divisor
$tempo_factor = 55_296_000;  # timer0 period 4.5ms * 96/2 ticks * 256

@songtitle = ("Secret of the Arid Sands", "Flight Bound for the Unknown",
              "Star of Darkness", "Prophecy", "Danger", "Far Thunder",
              "Where the Wind Ends", "Close Your Eyelids",
              "Spirit of the Night", "The Fairy Child",
              "What the Forest Taught Me", "Eternal Recurrance", "Oracle",
              "Tell a Strange Tale", "The Boy Aims for Wild Fields",
              "Rose and Ghost", "Did You See the Sea",
              "Color of the Summer Sky", "Main Menu", "The Legend",
              "Orphan of the Storm", "Eight Ringing Bells",
              "Dancing Beasts", "Boss Beaten",
              "18", "19", "Cannon Flight", "Ceremony",
              "Always Together", "A Prayer and a Whisper", "1e",
              "Happenings on a Moonlit Night", "A Curious Happening",
              "Got an Item", "Midge Mallet",
              "23", "A Wish", "Monarch on the Shore", "Steel and Traps",
              "Pure Night", "28", "Kind Memories",
              "The Holy Intruder", "In the Darkness Depths", "Angels Fear",
              "2d", "key", "2f", "30",
              "Give Love its Rightful Time",
              "The Second Truth from the Left", "33", "I Wont Forget",
              "Ally Joins", "To Reach Tomorrow", "One of Them Is Hope",
              "A Conclusion", "Meridian Dance", "3a", "3b", "3c", "3d", "3e",
              "3f");

@notes = ("C ", "C#", "D ", "D#", "E ", "F ",
          "F#", "G ", "G#", "A ", "A#", "B ");

# 1F is the mana beast roar sample
@patchmap  = (  0, 25, 39, 5, 61, 52, -38, 14,  # 00-07
                33, 63, 37, 55, 73, 21, 92, 18,  # 08-0F
                0, 4, 23, 48, 30, -42, -46, -36,  # 10-17
                -35, -40, -37, -39, -49, 13, -37, 0,  # 18-1F
                0, 8);                      #20-21

@transpose = (  0, -2, -2, 0, 0, 0, 0, 0,  # 00-07
                -2, 0, -2, 0, 0, 0, 0, 0,  # 08-0F
                0, 0, 0, 0, -1, 0, 0, 0,  # 10-17
                0, 0, 0, 0, 0, 0, 0, 0,  # 18-1F
                0, 0);                      # 20-21

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x 0x40;
}  

@songptrs = &read3ptrs($base + 0x3d39, 0x40);
@sampptrs = &read3ptrs($base + 0x3df9, 0x21);

# get SPC data - music ops lengths, pitch table, duration table
seek ROM, $spcbase + 0x1484, 0;
read ROM, $buf, 46;
@musoplen = unpack "C46", $buf;

seek ROM, $spcbase + 0x14f2, 0;
read ROM, $buf, 26;
@pitchtbl = unpack "v13", $buf;

seek ROM, $spcbase + 0x152c, 0;
read ROM, $buf, 15;
@durtbl = unpack "C15", $buf;

for ($i = 0, $numdone=0; $i <= 0x40; $i++) {
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
                 &read2ptrs(&s2o('C3/3F22') + $i * 0x20, 0x10);
  my ($vaddroffset, $emptyvoice)
          =  (unpack("v", $song), unpack("v", substr($song, 0x12, 2)));
  $vaddroffset = (0x11a14 - $vaddroffset) & 0xffff;
  my (@vstart) = unpack "v8", substr($song, 2, 0x10);
  @vstart = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart;

  my $maxtime = 0;
  my $maxvoice = -1;
  my $maxvol = 0;
  my $minvol = 0xff;
  my $mastervol = 0xff;
  my $mvolignore = -1;

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
    my $patch = 0;
    my $perckey = 0;   # key to use for percussion
    my $t = 0;  # transpose octaves (via patch)
    my $transpose = 0;  # actual transpose command
    my $crptcnt = 0; # conditional goto counter
    my $rpt = 0;
    my (@rptcnt, @rptpos, @rptcur);
    my $e = []; # track events
    push @$e, ['track_name', 0, "Voice " . ($v + 1)];
    push @$e, ['control_change', 0, $channel, 0, 0]; # bank 0

    for ($p = $vstart[$v] - 0x1a00; $p < $len; $p++) {
      #last if grep {$_ == $p + 0x1a00 && $_ ne $vstart[$v]} @vstart;

      my $cmd = ord(substr($song, $p, 1));
      if ($cmd < 0xd2) {
        # note
        my ($note, $dur) = (int($cmd/15), $cmd % 15);
        $dur = $durtbl[$dur] << 1;

        my $mnote = 12 * ($octave + $t) + $note + $transpose;
        if ($perckey) {
          # substitute appropriate percussion key on chan 10
          # fake keys for cuica
          $mnote = $perckey;
          #if ($perckey == 78) {
          #  $perckey = 79;
          #} elsif ($perckey == 79) {
          #  $perckey = 78;
          #}
        }
        if ($note < 12) {
          my $notedur = ($dur < 4) ? $dur : $dur - 4;
          push @$e, ['note', $totaltime, $notedur, $channel, $mnote, $velocity];
        } elsif ($note == 12) {
          # tie
          my ($last_ev) = grep {$_->[0] eq 'note'} reverse @$e;
	  $last_ev->[2] += $dur;
        } else {
          # rest
        }
        $totaltime += $dur;
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xd2];

	my ($op1, $op2, $op3) = map {ord} split //, substr($song, $p+1, 3);

        if ($cmd == 0xf3) {
	  # tempo
          $tempo = $op1;
          push @{$cevents},
	     ['set_tempo', $totaltime, int($tempo_factor / $tempo)];
        } elsif ($cmd == 0xf4) {
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
       } elsif ($cmd == 0xd2) {
	  # volume
          my $mvolfactor = ($patch == $mvolignore ? 1 : $mastervol / 256);
	  $velocity = (int($op1 * $mvolfactor * 0.75) >> 1) + 0x20;
          #$volume = $op1 >> 1;
	  #push @$e, ['control_change', $totaltime, $channel, 7, $volume];
          $maxvol = $velocity if $velocity > $maxvol;
          $minvol = $velocity if $velocity < $minvol;
        } elsif ($cmd == 0xd3) {
          # volume fade
          my $mvolfactor = ($patch == $mvolignore ? 1 : $mastervol / 256);
          $velocity = (int($op2 * $mvolfactor * 0.75) >> 1) + 0x20;
          #my ($tdelta, $cb);
          #my $bdelta = ($op2 >> 1) - $volume;
          #my $stepinc = $bdelta / $op1;
          ##print "c5 $op1 $op2 bd $bdelta, si $stepinc\n";
	  #for ($tdelta = 1, $cb = $volume + $stepinc;
          #       $tdelta < $op1; $tdelta++, $cb += $stepinc) {
          #  #print $totaltime + $tdelta, ",",
          #      # int($balance + $stepinc * $tdelta), "\n";
          #      #next unless $tdelta % 8 == 0;
          #      push @$e, ['control_change', $totaltime + $tdelta * 2,
          #               $channel, 7, int($cb)];
          #}
          #$volume = $op2 >> 1;
          $maxvol = $velocity if $velocity > $maxvol;
          $minvol = $velocity if $velocity < $minvol;
	} elsif ($cmd == 0xd4) {
	  # balance/pan
          $balance = $op1;
	  push @$e, ['control_change', $totaltime, $channel, 10, $balance >> 1];
	} elsif ($cmd == 0xd5) {
	  # balance/pan fade - $op1 = duration, $op2 = target
          my ($tdelta, $cb);
          my $bdelta = ($op2) - $balance;
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
        } elsif ($cmd == 0xea) {
	  # patch change
          $patch = $op1;
	  my $inst = $patchmap[$inst[$op1 - 0x20]];
          my $oldchan = $channel;
	  if ($inst < 0) {
	    # percussion
	    $perckey = -$inst;
            $channel = 9;  # 0-based 10
          } else {
	    push @$e, ['patch_change', $totaltime, $v, $inst];
	    $perckey = 0;
            $channel = $v;
	    $t = $transpose[$inst[$op1 - 0x20]]; 
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
        } elsif ($cmd == 0xe7) {
          # set transpose
          $transpose = $op1;
        } elsif ($cmd == 0xe8) {
          # change transpose
          $transpose += ($op1 & 0x80) ? -($op1 & 0x7f) : $op1;
	} elsif ($cmd == 0xe4) {
	  # set octave
	  $octave = $op1;
        } elsif ($cmd == 0xe5) {
	  # inc octave
	  $octave++;
	} elsif ($cmd == 0xe6) {
	  # dec octave
	  $octave--;
        } elsif ($cmd == 0xf8) {
          # set master volume
          $mastervol = $op1;
        } elsif ($cmd == 0xfb) {
          # ignore master volume for this patch
          $mvolignore = $op1;
	} elsif ($cmd == 0xf0) {
	  # begin repeat
	  $rptcnt[++$rpt] = $op1;
	  $rptpos[$rpt] = $p + 1;
	  $rptcur[$rpt] = 0;
	} elsif ($cmd == 0xf1) {
	  if (--$rptcnt[$rpt] < 0) {
	    # all done
	    $rpt--;
	  } else {
	    $p = $rptpos[$rpt];
	    next;
	  }
	} elsif ($cmd == 0xfa) {
          # goto
          my $dest = (($op1 + $op2 * 256) + $vaddroffset - 0x1a00) & 0xffff;
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
        } elsif ($cmd == 0xfc) {
          $crptcnt = 0;
	} elsif ($cmd == 0xf9) {
	  if (++$crptcnt == $op1) {
	    $p = (($op2 + $op3 * 256) + $vaddroffset - 0x1a00 - 1) & 0xffff;
	    next;
	  }
        } elsif ($cmd == 0xf2 || $cmd >= 0xfe) {
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
print STDERR " vol min=$minvol max=$maxvol\n";
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

