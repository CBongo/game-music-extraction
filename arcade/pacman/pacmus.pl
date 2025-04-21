#!/usr/bin/perl

#
# cab 2016-12-01
#

use MIDI;
use Data::Dumper;

# 6J ROM = 0x3000-0x3FFF
open ROM, "< pacman.6j"
  or die "open failed: $!\n";

# slurp in the whole 4K. heck, this script is probably longer.
read ROM, $rom, 4096;
close ROM;

sub readrom { substr $rom, &z2o($_[0]), ($_[1] || 1); };

#$tempo_factor = 55_296_000;  # timer0 period 4.5ms * 96/2 ticks * 256
$tempo_factor = 263_983; # vblank 60.61Hz = 16.5ms * 16 ticks

%musicop = (0xf0 => "Goto",
            0xf1 => "Wave select",
            0xf2 => "Octave",
            0xf3 => "Volume",
            0xf4 => "Type",
            0xff => "Halt");


@notes = ("C ", "C#", "D ", "D#", "E ", "F ",
          "F#", "G ", "G#", "A ", "A#", "B ");

#@patchmap  = (    0,  48,  46,   0,   0,  19,  56,  73, 13,  34, 47, 43,
#		-40, -36, -38, -35, -49, -42, 113, -69, 85, 117, -72); 

@transpose = (  0, +1, +1,  0,  +1, +2,   0, +1,  0,  -2, -1, -1,
		0,  0,  0,  0,   0,  0,   0,  0,  0,  +1,  0);

$numsongs = 2;

@songptrs = unpack "v" . ($numsongs * 3), 
	    &readrom(0x3bc8, $numsongs * 3 * 2);
&printarray("songptrs", "%04x", @songptrs);

@durtbl= unpack "C8", &readrom(0x3bb0, 8);
&printarray("durtbl", "%02x", @durtbl);
@pitchtbl = unpack "C16", &readrom(0x3bb8, 16);
&printarray("pitchtbl", "%02x", @pitchtbl);

for ($i = 0, $numdone=0; $i < $numsongs; $i++) {
  my ($opus);

  printf STDERR "%02X ", $i;

  $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 16});
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [
    ['set_tempo', 0, $tempo_factor]
  ];
 

  # do song i
  my (@vstart) = map {$songptrs[$i + $_ * $numsongs]} 0..2;
&printarray("vstart", "%04x", @vstart);

  printf "Song %02X - %s:\n", $i, $songtitle[$i];
  print "\n  Voice start addresses:\n   ";
  for ($j = 0; $j < 3; $j++) {
    printf " %d:%04X", $j+1, $vstart[$j];
  }
  print "\n";

VOICE:
  for ($v = 0; $v < 3; $v++) {
    next if $vstart[$v] == 0;
    next if ord(&readrom($vstart[$v])) == 0;

    my $track = new MIDI::Track;

    my $totaltime = 0;
    my $master_rpt = 1;
    
    my $velocity = 64;
    my $octave = 0;
    my $perckey = 0;   # key to use for percussion

    my $e = []; # track events
    push @$e, ['track_name', 0, "Voice " . ($v+1)];
    push @$e, ['control_change', 0, $v, 0, 0]; # bank 0

    print "\n" if $v > 0;
    print "    Voice ", $v+1, " data:\n";

    for ($p = $vstart[$v]; ; $p++) {
      printf "      %04X: ", $p;

      my $cmd = ord(&readrom($p));
      printf "%02X", $cmd;

      if ($cmd < 0xf0) {
        # note
        my ($note, $dur) = ($cmd & 0x1F, ($cmd >> 5));
        $dur = $durtbl[$dur];

        print "   " x 4;  # pad out

        if (($note & 0xf) > 0) {
          my $extra_oct = ($note & 0x10) ? " +8va" : "";
          printf "Note %2d (%02x) Dur %02X%s\n",
            $note & 0xf, $pitchtbl[$note & 0xf], $dur, $extra_oct;

	  my $moct = $octave + 3;
	  $moct++ if $extra_oct;
	  $moct -= 3 if $v == 0;  # first voice has 4 extra frq bits 
	  my $mnote = ($note & 0xF) + 12 * $moct;
	  my $channel = $v;

          push @$e, ['note', $totaltime,
                     $dur - 1,
                     $channel, $mnote, $velocity];
        #} elsif (($note & 0xf) == 0) {
        # printf "Rest         Dur %02X\n", $dur;
	} else {
	  # tie
          printf "Tie         Dur %02X\n", $dur;
	  my ($last_ev) = grep {$_->[0] eq 'note'}
				 reverse @$e;
	  $last_ev->[2] += $dur;
        }
        $totaltime += $dur;
      } else {
        # command
	my ($op1, $op2) = map {ord} split //, &readrom($p+1, 2);
        my $oplen = 0;
	if ($cmd == 0xf0) {
	  $oplen = 2;   #goto
	} elsif ($cmd < 0xf5) {
	  $oplen = 1;
	}
        print map {sprintf " %02X",ord($_)}
                &readrom($p+1, $oplen) if $oplen;
        print "   " x (4-$oplen);  # pad out

        print "$musicop{$cmd}\n";


        if ($cmd == 0xf3) {
	  # volume
	  $velocity = $op1 << 3;
	  $p++;
        } elsif ($cmd == 0xf1) {
	  # patch change
	  my $inst = $op1;
	  $p++;
	  if ($patchmap[$inst] < 0) {
	    # percussion
	    $perckey = -$patchmap[$inst];
          } else {
	    push @$e, ['patch_change', $totaltime, $v, $patchmap[$inst]];
	    $perckey = 0;
	  }
	} elsif ($cmd == 0xff) {
	  # halt
	  last;
        } elsif ($cmd == 0xf0) {
	  # goto
	  $p = ($op2 << 8) + $op1 - 1;
	  $p += 2;
	  # treat as halt for now
          last;
        } elsif ($cmd == 0xf2) {
	  # octave
	  $octave = $op1;
	  $p++;
	} elsif ($cmd == 0xf4) {
	  # type
	  $p++;
        }
      }
    }  # for p
    $track->events_r(MIDI::Score::score_r_to_events_r($e));
    push @{$opus->tracks_r}, $track;
  }
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  $opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "pacsong%d.mid", $i);
}

print STDERR "\n";
## the end

sub z2o {
  my ($zaddr) = @_;
  return $zaddr - 0x3000;
}

sub o2z {
  my ($offs) = @_;

  return $offs + 0x3000;
}

sub printarray {
  my ($label, $fmt, @v) = @_;
  print "$label: ", join(",", map {sprintf $fmt, $_} @v), "\n";
}
  
