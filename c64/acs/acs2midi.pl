#!/usr/bin/perl
#
# ACS2MIDI - convert Adventure Construction Set code to MIDI format
# Christopher Bongaarts - 3 June 2002
#
# (after a long, hard-fought battle of reverse engineering
# lasting about a decade...)
#

use MIDI;

$infilename = "ACS1/M.PRG";

@songtitles = ('Skitter Down', 'Woop Woop', 'Blastoff', 'Blip', 'Razz',
	       'Tap', 'Kabloom', 'Blowie', 'Kablowie', 'Tweeter',
	       'Skitter Up', 'Zoop Zoop', 'Dusk', 'Endless Fantasy',
	       'Endless Spy-Mystery', 'Endless Sci-Fi', 'Endless Suite',
	       '1 Voice Fugue', '2 Voice Fugue', '3 Voice Fugue',
	       'Fugue Finale', 'Fanfare', 'Heroic Theme', 'Departure',
	       'Travelling', 'Battle', 'Death Blow', 'Dirge', 'Return',
	       'Closing');

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x 0x1e;
}

open M, "< $infilename" or die;
read M, $buf, 0x10000;
close M;

my ($loadaddr) = unpack "v", $buf;
my $code = substr $buf, 2;

# decrypt music code
substr($code, (0x8009-$loadaddr), 2) = pack "v", 0x444c;

for ($p = 0x8009; $p < 0x8dbb; $p += 2) {
  my $w1 = unpack "v", substr $code, ($p-$loadaddr), 2;
  my $w2 = unpack "v", substr $code, ($p-$loadaddr)+2, 2;
  my $xw = $w1 ^ $w2;
  substr($code, ($p-$loadaddr)+2, 2) = pack "v", $xw;
}

@songaddrs = unpack "v*", substr $code, (0x81e7 - $loadaddr), 2 * 0x1e;

for ($song = 0; $song < 0x1e; $song++) {
  next unless $dosong[$song];
  print STDERR "\n" if ++$numdone % 16 == 0;
  printf STDERR " %02x", $song;
	
  
  my $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 48});
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [];
  
  my $totaltime = 0;
  my $volume = 80;

  my @dur;
  my @durpct;
  my @state;
  my @tying;

  # shorthand array to access track events
  my @e;
  for ($v = 0; $v < 3; $v++) {
    @{$e[$v]} = [];
    push @{$e[$v]}, ['track_name', 0, "Voice $v"];
    push @{$e[$v]}, ['control_change', 0, $v, 0, 0]; # bank 0
    #push @{$e[$v]}, ['patch_change', 0, $v, $patchmap[0]];
  }
  
  my $p = $songaddrs[$song];
  my $nextevent = 1;
  while (1) {
    last if $p > 0x8ddf;  # emergency brake
    my ($cmd, $op1, $op2, $op3) = unpack "C*", substr $code, $p-$loadaddr, 4;
    #printf "p=%04x  cmd=%02x op1=%02x\n", $p, $cmd, $op1;
    $p++;
    if ($cmd & 0x80) {
      # note
      $p++;
      my $mnote = ($cmd & 0x7f) + 12;
      my $v = $op1 & 3;
      my $dur = (($op1 >> 2) & 0xf);
      my $no_release = $op1 & 0x40;
      my $cmds_done = $op1 & 0x80;
      my $notedur = $no_release ? $dur : $dur > 0 ? $dur - 1 : 0;
      $dur *= 12;
      $notedur *= 12;
      #printf "  NOTE dur=%02x ndur=%02x\n", $dur, $notedur;
      if ($tying[$v]) {
        my ($last_ev) = grep {$_->[0] eq 'note'} reverse @{$e[$v]};
        # make prev note last right up till now
       	$last_ev->[2] = $totaltime - $last_ev->[1];
       	if ($last_ev->[4] == $mnote) {
          $last_ev->[2] += $dur;  # already did notedur
       	} else {
       	  push @{$e[$v]}, ['note', $totaltime, $notedur, $v,
                           $mnote, $volume];
       	}
      } else {
        push @{$e[$v]}, ['note', $totaltime, $notedur, $v,
                         $mnote, $volume];
      }
      $tying[$v] = $no_release;
      $nextevent = $dur;
      $cmds_done ? next : redo;
    } elsif ($cmd == 0x66) {
      # volume
      $volume = ($op1 & 0xf) << 3;
      #printf "  VOLUME=%02x\n", $volume;
      $p++;
      redo;
    } elsif ($cmd == 0x1d) {
      # tempo
      #printf "  TEMPO=%02x\n", $op1;
      $p++;
      push @{$cevents},
                  ['set_tempo', $totaltime, int(($op1 + 1) * 1000000 / 15)];
      redo;
    } elsif ($cmd == 0x01) {
      #printf "  NEXTEV=%02x\n", $op1 * 12;
      $p++;
      $nextevent = $op1 * 12;
      next;
    } elsif ($cmd == 0x03) {
      # terminate song
      $nextevent = 0;
      last;
    } elsif ($cmd == 0x13) {
      # go to nice termination routine if $8242 <> 0
      #printf "  TERM_IF_8242 8242=%02x\n", $state[8];
      $p = 0x8276 if $state[8];
      redo;
    } elsif ($cmd == 0x07) {
      # reset SID
      redo;
    } elsif ($cmd == 0x0b) {
      # repeat/goto
      # HACK: don't repeat "endless" themes
      last if $p == 0x878b + 1;  # fantasy
      last if $p == 0x8cf2 + 1;  # suite, spy/mystery
      last if $p == 0x8860 + 1;  # scifi
      $p += 3;
      if (--$state[$op1]) {
      	$p = $op2 + $op3 * 0x100;
      }
      #printf "  REPEAT  state=%02x newp=%04x\n", $state[$op1], $p;
      redo;
    } else {
      my $reg = ($cmd >> 2) & 0x1f;
      if (($cmd & 3) == 0) {
      	#printf "  SHADOW.b %04x=%02x\n", 0xd400 + $reg, $op1;
      	$p++;
      }
      if (($cmd & 3) == 2) {
      	if ($reg >= 0x17) {
      	  #printf "  SHADOW.h %04x=%02x\n", 0xd417 + int(($reg - 0x17) / 2), $op1;
      	  $p++;
      	} else {
      	  #printf "  SHADOW.w %04x=%04x\n", 0xd400 + $reg, $op1 + $op2 * 0x100;
      	  $p += 2;
      	}
      }
      if (($cmd & 3) == 1) {
      	#printf "  STATE %04x=%02x\n", 0x823a + $reg, $op1;
      	$p++;
      	$state[($cmd >> 2) & 0x1f] = $op1;
      }
      redo;
    }
  } continue {
    $totaltime += $nextevent;
  }
  
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  for ($v = 0; $v < 3; $v++) {
    push @{$opus->tracks_r},
        new MIDI::Track ({'events_r'
                            => MIDI::Score::score_r_to_events_r($e[$v])});
  }
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%02X - %s.mid",
  			 $song, $songtitles[$song]);
}

print STDERR "\n";
