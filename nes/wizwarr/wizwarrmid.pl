#!/usr/bin/perl
#
# wizwarrmid.pl - wizwarr.nes to midi generator
# chris bongaarts 15 may 2023
#

use MIDI;

$fname = "wizwarr.nes";

@songtitles = ();  #nTBD

@notenametbl = qw( A A# B C C# D D# E F F# G G# );

open NES, "< $fname" or die "couldn't open $fname: $!\n";

read NES, $buf, 0x10;  # read iNES header
($format, $PRGsize, $CHRsize, $flags1, $flags2, $flags3)
    = unpack "a4C4xxC", $buf;
$mapper = ($flags1 & 0xf0 >> 4) | ($flags2 & 0xf0);

read NES, $buf, 16384 * $PRGsize;  # read in PRG ROM.  skip CHR ROM...
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
 
@track_offsets = &$readsong("v*", 0xec6f, 0x80);

for (my $song=0; $song < 0x10; $song++) {
  $sbase = $song * 8 + 0x80;

  my $fn = sprintf "%02x.txt", $sbase + 6;
  open TXT, "> txt/$fn" or warn "open failed for output: $!\n";
  select TXT;

  my ($opus, $ctrack, $cevents, $totaltime, @e);
  my @tracknames = ('Square 1', 'Square 2', 'Triangle', 'Noise');
  $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 16});
  $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  $cevents = [];
  # tempo is usec per quarter note; we defined qtr to be N ticks,
  # with tick = 1/60 sec, so tempo is 1M/60 * N
  push @{$cevents}, ['set_tempo', 0,
  	 int(1_000_000 * 16 / 60)];
  # shorthand array to access track events
  @e = ();
  for (my $v = 0; $v < 4; $v++) {
    @{$e[$v]} = [];
    push @{$e[$v]}, ['track_name', 0, $tracknames[$v]];
    push @{$e[$v]}, ['control_change', 0, $v, 0, 0]; # bank 0
    #push @{$e[$v]}, ['patch_change', 0, $v, $patchmap[0]];
  }
  $totaltime = 0;

  printf "Song %02x:\n", $sbase + 6;
  printf "  Square 1:  %04x\n", $track_offsets[$song * 4];
  &parse_track($track_offsets[$song * 4], 0, $e[0], $cevents);
  printf "  Square 2:  %04x\n", $track_offsets[$song * 4 + 1];
  &parse_track($track_offsets[$song * 4 + 1], 1, $e[1], $cevents);
  printf "  Triangle:  %04x\n", $track_offsets[$song * 4 + 2];
  &parse_track($track_offsets[$song * 4 + 2], 2, $e[2], $cevents);
  printf "  Noise:     %04x\n", $track_offsets[$song * 4 + 3];
  &parse_track($track_offsets[$song * 4 + 3], 3, $e[3], $cevents);
  print "\n";
  
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  for ($v = 0; $v < 4; $v++) {
    push @{$opus->tracks_r},
     	  new MIDI::Track ({'events_r' 
  	  		     => MIDI::Score::score_r_to_events_r($e[$v])});
    }
  close TXT;
  $opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%02x.mid", $sbase + 6);
}
exit;
      	 
sub parse_track {
  my ($addr, $v, $e, $cevents) = @_;
  my $done = 0;
  my $curdur = -1; # 0x7e0,X
  my $repeats = 0; # 0x7a2,X
  my $repaddr = 0; # 0x7a0,X
  my $totaltime = 0;

  my ($initvol, $initsw, $inithi) = &$readsong("C3", $addr, 3);
  &print_addr_ops($addr, $initvol, $initsw, $inithi);
  $addr +=3;
  printf "Init regs VOL=%02x SWEEP=%02x HI=%02x\n", $initvol, $initsw, $inithi;
  #push @{$e}, ['control_change', 0, $v, 0, 0]; # bank 0
  #push @{$e}, ['patch_change', 0, $v, $patchmap[0]];

  my $vbase = $addr;
  my $loops = 0;
  for (my $p = $addr; !$done; ) {
    my $vcmd = &$readsong("C", $p, 1);
    my (@ops) = ($vcmd);
    my $p1 = $p;  # initial p for line label

    if ($vcmd >= 0x80) {
      #note/rest
      my $dur = $curdur;
      $vcmd &= 0x7f;
      if ($dur < 0) {
	  $dur = &$readsong("C", ++$p, 1);
	  push @ops, $dur;
      }
      &print_addr_ops($p1, @ops);
      if ($vcmd) {
	  my $notenum = ($vcmd + 2) % 12;
	  my $octave = int(($vcmd + 2)/ 12) + 2;
	  print "Note ", $notenametbl[$notenum], $octave, " dur $dur\n";
       	  if ($v != 3) {  # not noise
		  my $transpose = 35;  # base traspose to midi notenum
		  $transpose -= 12 if $v == 2;  # make triangle an octave lower
		  push @{$e}, ['note', $totaltime, $dur, $v, $vcmd + $transpose, 100];
	  } else {
       	    my $noteval = 0;
	    if ($cmdnote == 0x30) {  # snare
	      $noteval = 38;
            } elsif ($cmdnote == 0x20) {  # kick
              $noteval = 35;
            } elsif ($cmdnote & 0x10) {  # closed hihat
              $noteval = 42;
            }  # everything else is a rest
       	      push @{$e}, ['note', $totaltime, $dur, 9, $noteval, 100]
       	    	if $noteval;
	  }
      } else {  # vcmd = 0
	  print "Rest dur $dur\n";
      }
      $p++;
      $totaltime += $dur;
    } elsif ($vcmd < 0x10) {
      # vcmd
      if ($vcmd == 0) {
	 # halt
	 &print_addr_ops($p1, @ops);
	 print "Halt\n";
	 last;
      } elsif ($vcmd == 1) {
	 # goto
	 my ($lo, $hi) = &$readsong("C2", ++$p, 2);
	 push @ops, $lo, $hi;
	 &print_addr_ops($p1, @ops);
	 printf "Goto %04x\n", $hi * 256 + $lo;
	 $p = $hi * 256 + $lo;
	 $vbase = $p;
	 $done = 1; # if $p > $p1 - 3; #bail on tight loops
     } elsif ($vcmd == 2) {
	 # sfx only - clear flag bit 1
	 &print_addr_ops($p++, @ops);
     } elsif ($vcmd == 3) {
	 # save offset and continue?
	 &print_addr_ops($p++, @ops);
     } elsif ($vcmd == 4) {
	 # set vol, sweep, hi direct
	 my ($vol, $sw, $hi) = &$readsong("C3", ++$p, 3);
	 push @ops, $vol, $sw, $hi;
	 &print_addr_ops($p1, @ops);
	 $p += 3;
	 printf "Set VOL %02x SWEEP %02x HI %02x\n", $vol, $sw, $hi;
         #push @{$e}, ['patch_change', 0, $v, $patchmap[0]];
     } elsif ($vcmd == 5) {
	 # gosub
	 $repeats = &$readsong("C", ++$p, 1);
         $repaddr = $p + 3;
	 my ($lo, $hi) = &$readsong("C2", ++$p, 2);
	 push @ops, $repeats, $lo, $hi;
	 &print_addr_ops($p1, @ops);
	 printf "Gosub/repeat %d times at %04x (return %04x)\n",
	   $repeats, $hi * 256 + $lo, $repaddr;
	 $p = $hi * 256 + $lo;
	 $vbase = $p;
      } elsif ($vcmd == 6) {
	 # return/repeat
	 &print_addr_ops($p, @ops);
         printf "Return/repeat, count = %d\n", --$repeats;
	 if ($repeats == 0) {
           $p = $repaddr;
	   $vbase = $p;
	 } else {
           $p = $vbase; # y=0 - back to beginning of block
	 }
      } elsif ($vcmd == 7) {
	 # set fixed duration
	 $curdur = &$readsong("C", ++$p, 1);
	 push @ops, $curdur;
	 &print_addr_ops($p1, @ops);
	 printf "Set duration %d\n", $curdur;
	 $p++;
      } elsif ($vcmd == 8) {
	 # set inline dur/sfx mode
	 &print_addr_ops($p++, @ops);
	 print "Set inline dur/sfx mode\n";
	 $curdur = -1;
      } elsif ($vcmd == 9) {
	 # set tempo
	 my $tempo = &$readsong("C", ++$p, 1);
	 push @ops, $tempo;
	 &print_addr_ops($p1, @ops);
	 printf "Set tempo %d\n", $tempo;
	 #push @{$cevents}, ['set_tempo', 0,
	 #        int(1_000_000 * 16 / 60)];
	 $p++;
      } elsif ($vcmd == 10) {
	 # set vol reg
	 my $vol = &$readsong("C", ++$p, 1);
	 push @ops, $vol;
	 &print_addr_ops($p1, @ops);
	 printf "Set VOL reg to %02x\n", $vol;
         #push @{$e}, ['patch_change', 0, $v, $patchmap[0]];
	 $p++;
      } else {
	 # pointer would run off end of table, good luck...
	 &print_addr_ops($p, @ops);
	 print "???? UNKONWN OPCODE ????\n";
	 last;  # better just give up here
      }
    } else {
      # direct freq - should be sfx only
      my $vol = ($vcmd & 0xf0) >> 4;
      my $hi = &$readsong("C", ++$p, 1);
      my $lo = &$readsong("C", ++$p, 1);
      push @ops, $hi, $lo;
      &print_addr_ops($p1, @ops);
      printf "Direct freq %03x vol %d\n", $hi * 256 + $lo, $vol;
      $p++;
    }
    #$done = $done || grep {$p = $_ && $p != $addr} @track_offsets;
    last if ++$loops >5000;  # just a failsafe
  }
  print "\n";
}

sub print_addr_ops {
  my $addr = shift;
  my (@ops) = @_;

  printf "  %04x: " . join(" ", ("%02x") x scalar(@ops))
                    . join(" ", ("  ")   x (4-scalar(@ops)))
		    . "  ",
             $addr, @ops;
}

