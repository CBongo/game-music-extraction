#!/usr/bin/perl
#
# megaman_mid.pl - megaman.nes to midi generator
# chris bongaarts 23 may 2023
#

use MIDI;

$fname = "megaman.nes";

@songtitles = ("00",
		"01-staage select bgm",
		"02-selection made bgm",
		"03",
		"04",
		"05-cutman level bgm",
		"06_07-fireman level bgm",
		"06_07-fireman level bgm",
		"08-bombman level bgm",
		"09-elecman level bgm",
		"0a-gutsman level bgm",
		"0b",
		"0c-dr wily level bgm 1",
		"0d",
		"0e-credits",
		"0f",
		"10-dr wily level bgm 2",
		"11",
		"12",
		"13",
		"14-megaman shoot sfx",
		"15-enemy shoot sfx",
		"16",
		"17",
		"18",
		"19-megaman jump sfx",
		"1a",
		"1b",
		"1c-title start sfx",
		"1d",
		"1e",
		"1f-selection change sfx",
		"20-megaman dropin subscreen close sfx",
		"21-clear points clink sfx",
		"22-subscreen appear sfx",
		"23",
		"24",
		"25",
		"26",
		"27",
		"28",
		"29",
		"2a",
		"2b",
		"2c",
		"2d",
		"2e",
		"2f",
		"30",
		"31",
		"32"
	);  #nTBD

@notenames = qw( A A# B C C# D D# E F F# G G# );

open NES, "< $fname" or die "couldn't open $fname: $!\n";

read NES, $buf, 0x10;  # read iNES header
($format, $PRGsize, $CHRsize, $flags1, $flags2, $flags3)
    = unpack "a4C4xxC", $buf;
$mapper = ($flags1 & 0xf0 >> 4) | ($flags2 & 0xf0);

#sound stuff is in PRG bank 4
seek NES, 0x4000 * 4, 1;  # whence = 1 means relative to currentn pos
read NES, $buf, 0x4000;
close NES;

#printf "Format: %s\n", $format;
#printf "PRG size: %d (\$%x)\n", $PRGsize, $PRGsize * 16384;
#printf "CHR size: %d (\$%x)\n", $CHRsize, $CHRsize * 8192;
#printf "ROM Control 1/2: %02x %02x\n", $flags1, $flags2;
#printf " Mapper: %02x\n", $mapper;
#printf " Mirroring: %s\n", ($flags1 & 1) ? "Vertical" : "Horizontal";

# finally, a place for a closure.  lisp class was fun AND useful.
my $readsong = sub { my ($format, $start, $len) = @_;
                     return unpack $format,
                       substr($buf, $start - 0x8000, $len);
                   };
 
@track_offsets = &$readsong("v*", 0x9a60, 0x33 * 2);

# up to 11 are bgm, rest are sfx
for (my $song=0; $song < 0x11; $song++) {
  my $fn = $songtitles[$song] . ".txt";
  open TXT, "> txt/$fn" or warn "open failed for output: $!\n";
  select TXT;

  my ($opus, $ctrack, $cevents, $totaltime, @e);
  my @tracknames = ('Square 1', 'Square 2', 'Triangle', 'Noise');
  my (@voffsets) = &$readsong("v5", $track_offsets[$song] + 1, 10);
  $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 16});
  $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  $cevents = [];
  # tempo is usec per quarter note; we defined qtr to be N ticks,
  # with tick = 1/60 sec, so tempo is 1M/60 * N
  push @{$cevents}, ['set_tempo', 0,
  	 int(1_000_000 * 24 / 60)];
  # shorthand array to access track events
  @e = ();
  $preset_count = 0;

  printf "Song %02x (%04x=%02x): %s\n",
    $song,
    $track_offsets[$song], 
    &$readsong("C", $track_offsets[$song], 1),
    $songtitles[$song];
  for (my $v=0; $v < 4; $v++) {
    printf "  %8s:  %04x\n", $tracknames[$v], $voffsets[$v];
    next unless $voffsets[$v];
    @{$e[$v]} = [];
    push @{$e[$v]}, ['track_name', 0, $tracknames[$v]];
    push @{$e[$v]}, ['control_change', 0, $v, 0, 0]; # bank 0
    #push @{$e[$v]}, ['patch_change', 0, $v, $patchmap[0]];
    &parse_track($voffsets[$v], $v, $e[$v], $cevents);
    push @{$opus->tracks_r},
     	  new MIDI::Track ({'events_r' 
  	  		     => MIDI::Score::score_r_to_events_r($e[$v])});
    print "\n";
  }
  printf "  %8s:  %04x\n", "Presets", $voffsets[4];
  for (my $preset=0; $preset < $preset_count; $preset++) {
    printf "  Preset %d:  %02x %02x %02x %02x\n", $preset, &$readsong("C4", $voffsets[4] + 4 * $preset, 4);
  }
  close TXT;
  
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  $opus->dump({dump_tracks => 1});
  $opus->write_to_file("mid/" . $songtitles[$song] . ".mid");
}
exit;
      	 
sub parse_track {
  my ($addr, $v, $e, $cevents) = @_;
  my $done = 0;
  my $totaltime = 0;
  my $dotting = 0;
  my $transpose = 0;

  while (!$done) {
    my $vbase = $addr;
    my $vcmd = &$readsong("C", $addr++, 1);
    my (@ops) = ($vcmd);

    if ($vcmd == 0) {
      my $arg = &$readsong("C", $addr++, 1);
      push @ops, $arg;
      &print_addr_ops($vbase, @ops);
      printf "Set Tempo (504) to %02x\n", $arg;
    } elsif ($vcmd == 1) {
      my $arg = &$readsong("C", $addr++, 1);
      push @ops, $arg;
      &print_addr_ops($vbase, @ops);
      printf "Set pitch mod (509) to %02x\n", $arg;
    } elsif ($vcmd == 2) {
      my $arg = &$readsong("C", $addr++, 1);
      push @ops, $arg;
      &print_addr_ops($vbase, @ops);
      my $dp = (12.5, 25, 50, -25)[$arg >> 6];
      printf "Set pulse duty cycle (50c.6/7) to %02x (%d%%)\n", $arg, $dp;
    } elsif ($vcmd == 3) {
      my $arg = &$readsong("C", $addr++, 1);
      push @ops, $arg;
      &print_addr_ops($vbase, @ops);
      if ($v == 2) {
        printf "Set triangle linear counter (50c) to %02x\n", $arg;
      } else {
        printf "Set pulse control/vol (50c.0-5) to %02x\n", $arg;
      }
    } elsif ($vcmd == 4) {
      my ($count, $destlo, $desthi) = &$readsong("C3", $addr, 3);
      $addr += 3;
      push @ops, $count, $destlo, $desthi; 
      &print_addr_ops($vbase, @ops);
      if ($count > 0) {
	if ($repeatcount == 0) {
	  $repeatcount = $count;
	} else {
	  $repeatcount--;
	}
        printf "Repeat %d/%d times to %04x\n", $count - $repeatcount, $count, $desthi * 256 + $destlo;
	if ($repeatcount > 0) {
	  $addr = $desthi * 256 + $destlo;
	}
      } else {
        printf "Goto %04x\n", $desthi * 256 + $destlo;
	$done = 1;
      } 
    } elsif ($vcmd == 5) {
      my $arg = &$readsong("C", $addr++, 1);
      push @ops, $arg;
      &print_addr_ops($vbase, @ops);
      printf "Set pitch table 507/8 to %02x (%04x)\n", $arg, 0x9991 + $arg * 2;
      $transpose = $arg;
    } elsif ($vcmd == 6) {
      &print_addr_ops($vbase, @ops);
      printf "Dotted note prefix\n";
      $dotting = 1;
    } elsif ($vcmd == 7) {
      my ($arg1, $arg2) = &$readsong("C2", $addr, 2);
      $addr += 2;
      push @ops, $arg1, $arg2; 
      &print_addr_ops($vbase, @ops);
      if ($arg1 & 0x7f) {
        printf "Set 50d to %02x, 50e to 0, 50f to %02x\n",
           $arg1, ($arg1 < 0x80) ? ($arg2 & 0xf0) : ($arg2 & 0xf0 | 0xf);
      } else {
        printf "Set 50d to %02x, 50f to %02x\n", $arg1, $arg2;
      }
    } elsif ($vcmd == 8) {
      my $arg = &$readsong("C", $addr++, 1);
      push @ops, $arg;
      &print_addr_ops($vbase, @ops);
      $preset_count = $arg + 1 unless $preset_count > $arg + 1;
      printf "Set 506 bits 0-4 to %02x and copy presets to 514-7\n", $arg;
    } elsif ($vcmd == 9) {
      &print_addr_ops($vbase, @ops);
      print "Halt\n";
      $done = 1;
    } elsif ($vcmd < 0x10) {
      &print_addr_ops($vbase, @ops);
      printf "*** BAD COMMAND %02x\n", $vcmd;
    } elsif (($vcmd & 0xf0) == 0x20) {
      &print_addr_ops($vbase, @ops);
      printf "Tie next %d notes (506.5-7)\n", $vcmd & 0x7;
    } elsif (($vcmd & 0xf0) == 0x30) {
      &print_addr_ops($vbase, @ops);
      print "Set bit 7 of 505 (double time?)\n";
    } else {
      &print_addr_ops($vbase, @ops);
      
      # std durations
      my (@tbl_9981) = (0, 0, 2, 4, 8, 16, 32, 64);
      # dotted durations
      my (@tbl_9989) = (0, 0, 3, 6, 12, 24, 48, 96);

      my $duridx = ($vcmd & 0xe0) >> 5;
      my $dur = $dotting ? $tbl_9989[$duridx] : $tbl_9981[$duridx];
      my $notenum = ($vcmd & 0x1f);
      $notenum += $transpose + 24 if $notenum; # A440 = notenum 45 -> midi 69
      $notenum -= 12 if $notenum && $v == 2;  # triangle sounds 8va lower
      my $octave = int($notenum / 12);
      my $notename = $notenames[$notenum % 12];
      #printf "Set 502/3 to %d x 504, Decr 506 bits 5-7 if not 0 else \n",
      printf "%s %-4s dur %d\n", 
        $notenum ? "Note" : "Rest",
	$notenum ? $notename . $octave : "",
        $dur;
      push @{$e}, ['note', $totaltime, $dur, $v == 3 ? 9 : $v, $v == 3 ? 42 : $notenum, 100]
        if $notenum;
      $dotting = 0;  #one-shot prefix
      $totaltime += $dur;
    }
  }
}

sub print_addr_ops {
  my $addr = shift;
  my (@ops) = @_;

  printf "  %04x: " . join(" ", ("%02x") x scalar(@ops))
                    . join(" ", ("  ")   x (4-scalar(@ops)))
		    . "  ",
             $addr, @ops;
}

