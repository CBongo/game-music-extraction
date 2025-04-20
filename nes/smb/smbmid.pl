#!/usr/bin/perl
#
# smbmid - smb.nes to midi generator
# chris bongaarts <bong0004@tc.umn.edu>  29 Jan 2003
#

use MIDI;

$fname = "smb.nes";

@songtitles = ('Die', 'Game Over', 'End', 'Level Complete', 'Game Over',
	       'Next Level Complete', 'Hurry Up', '',
	       'Main Theme A', 'Water World', 'Underground', 'Castle',
	       'Star', 'Main Theme');

open NES, "< $fname" or die "couldn't open $fname: $!\n";

read NES, $buf, 0x10;  # read iNES header
($format, $PRGsize, $CHRsize, $flags1, $flags2, $flags3)
    = unpack "a4C4xxC", $buf;
$mapper = ($flags1 & 0xf0 >> 4) | ($flags2 & 0xf0);

read NES, $buf, 16384 * $PRGsize;  # read in PRG ROM.  skip CHR ROM...
close NES;

printf "Format: %s\n", $format;
printf "PRG size: %d (\$%x)\n", $PRGsize, $PRGsize * 16384;
printf "CHR size: %d (\$%x)\n", $CHRsize, $CHRsize * 8192;
printf "ROM Control 1/2: %02x %02x\n", $flags1, $flags2;
printf " Mapper: %02x\n", $mapper;
printf " Mirroring: %s\n", ($flags1 & 1) ? "Vertical" : "Horizontal";

# finally, a place for a closure.  lisp class was fun AND useful.
my $readsong = sub { my ($format, $start, $len) = @_;
                     return unpack $format,
                       substr($buf, $start - 0x8000, $len);
                   };
 
@songoffs  = &$readsong("C*", 0xf90d, 0x31);
my $songcount;
foreach $s (@songoffs) {
  $songcount++;
  #next if @{$songhdr{$s}};  # there be dups here, but we need em
  @{$songhdr{$s}} = &$readsong("CvC3", 0xf90d + $s, 6);
  # mark voices that use noise voice
  push @{$songhdr{$s}}, 1 if $songcount == 0xa || $songcount > 0xc;
}

#@notetbl   = &$readsong("n*", 0xff00, 0x66);
@notetbl = (80, 98,  0, 52, 53, 54, 55, 56,
	    58, 59, 60, 61, 62, 63, 64, 65,
	    66, 67, 68, 69, 70, 71, 72, 73,
	    74, 75, 76, 77, 78, 79, 80, 82,
	    81, 83, 88, 85, 86, 87, 89, 91,
	    92, 94, 96, 100, 103, 43, 48, 50,
	    51, 57, 84, 41);
@notenametbl = ('Ab5', 'D7',  'rest', 'E3',  'F3',  'F#3', 'G3',  'Ab3',
		'Bb3', 'B3',  'C4',   'C#4', 'D4',  'Eb4', 'E4',  'F4',
		'F#4', 'G4',  'Ab4',  'A4',  'Bb4', 'B4',  'C5',  'C#5',
		'D5',  'Eb5', 'E5',   'F5',  'F#5', 'G5',  'Ab5', 'Bb5',
		'A5',  'B5',  'E6',   'C#6', 'D6',  'Eb6', 'F6',  'G6',
		'Ab6', 'Bb6', 'C7',   'E7',  'G7',  'G2',  'C3',  'D3',
		'Eb3', 'A3',  'C6',   'F2');
		
@durtbl    = &$readsong("C*", 0xff66, 8 * 6);

#print "Song data:\n";
my ($opus, $ctrack, $cevents, $totaltime, @e);
my @tracknames = ('Square 2', 'Square 1', 'Triangle', 'Noise');

SONG:
for ($cursong = 0; $cursong < @songoffs; $cursong++) {
  print STDERR "\n" if $cursong % 0x10 == 0x0 && $cursong > 0;
  printf STDERR "%02x ", $cursong;
  #next if $done{$songoffs[$cursong]};
  #$done{$songoffs[$cursong]} = 1;
  
  my ($duroffs, $songaddr, $trioffs, $sq1offs, $noiseoffs, $hasnoise)
    = @{$songhdr{$songoffs[$cursong]}};
  $duroffs = 8 if $cursong == 6;  # code overrides this one
  
  my ($sq1addr, $sq2addr, $triaddr, $noiseaddr)
    = ($sq1offs ? $songaddr+$sq1offs : 0, $songaddr,
    	$songaddr+$trioffs, $hasnoise ? $songaddr+$noiseoffs : 0);

  open OUT, sprintf "> txt/%02x-%s", $cursong, $songtitles[$cursong];
  printf OUT "Song %02x @ %04x: ", $cursong, $songaddr;
  printf OUT "duroff %02x  sq2 %04x sq1 %04x tri %04x noi %04x\n",
    $duroffs, $sq2addr, $sq1addr, $triaddr, $noiseaddr;
  &do_voice_txt($sq2addr, $sq1addr, $triaddr, $noiseaddr,
  	        ${$songhdr{$songoffs[$cursong+1]}}[1]);
  close OUT;

  if ($cursong < 0x11) {  
    $opus = new MIDI::Opus ({'format' => 1, 'ticks' => $durtbl[$duroffs + 4]});
    $ctrack = new MIDI::Track;
    push @{$opus->tracks_r}, $ctrack;
    $cevents = [];
    # tempo is usec per quarter note; we defined qtr to be N ticks,
    # with tick = 1/60 sec, so tempo is 1M/60 * N
    push @{$cevents}, ['set_tempo', 0,
    	 int(1_000_000 * $durtbl[$duroffs + 4] / 60)];
    # shorthand array to access track events
    @e = ();
  }
  
  my @vaddr = ($sq2addr, $sq1addr, $triaddr, $noiseaddr);
  
  if ($cursong < 0x11) {
    for ($v = 0; $v < 4; $v++) {
      @{$e[$v]} = [];
      next unless $vaddr[$v];
      push @{$e[$v]}, ['track_name', 0, $tracknames[$v]];
      push @{$e[$v]}, ['control_change', 0, $v, 0, 0]; # bank 0
      #push @{$e[$v]}, ['patch_change', 0, $v, $patchmap[0]];
    }
    $totaltime = 0;
  }
  
  my $lasttime = 0;
  my @curdur = (0) x 4;
  my @p = @vaddr;
  my @nexttime = (1) x 4;
  #$opus->dump({dump_tracks => 1});
  
  for (;; $totaltime++) {
     last if $lasttime;
VOICE:
     for (my $v = 0; $v < 4; $v++) {
       next unless $p[$v]; # skip inactive voices
       $nexttime[$v]--;
       next if $nexttime[$v] > 0; # wait for next time to come
NOTE: {
       my $cmd = &$readsong("C", $p[$v]++, 1);
       
       if ($cmd == 0) {
       	if ($v == 0) {
       	  # sq2: halt or do next thing
       	  if ($cursong == 0x30 || $cursong < 0x10) {
       	    # the end
       	    $lasttime = 1;
       	    last VOICE;
       	  } else {
       	    # chain to next song
       	    next SONG;
       	  }
       	}
       	if ($v == 2) {
       	  # tri: halt
       	  $p[$v] = 0;
       	}
       	if ($v == 3) {
       	  $p[$v] = $vaddr[$v];   # repeat from start
       	  redo NOTE;
       	}
       } elsif ($v == 1 || $v == 3) {	# sq1 or noise note
	 my ($cmdnote, $cmddur) = ($cmd & 0x3e,
      				  (($cmd & 0x1) << 2) |
      				  (($cmd & 0xc0) >> 6)); 
      	 my $dur = $durtbl[$cmddur + $duroffs];
      	 
       	 if ($v == 1) {  # sq1
       	   my $noteval = $notetbl[$cmdnote >> 1];
       	   push @{$e[$v]}, ['note', $totaltime, $dur, $v, $noteval, 100]
       	   	if $noteval;
       	 } elsif ($v == 3) {  # noise
       	   my $noteval = 0;
	   if ($cmdnote == 0x30) {  # snare
	     $noteval = 38;
           } elsif ($cmdnote == 0x20) {  # kick
             $noteval = 35;
           } elsif ($cmdnote & 0x10) {  # closed hihat
             $noteval = 42;
           }  # everything else is a rest
       	   push @{$e[$v]}, ['note', $totaltime, $dur, 9, $noteval, 100]
       	   	if $noteval;
       	 }
       	 $nexttime[$v] = $dur;
       } elsif ($cmd >= 0x80) {
       	 # set duration on sq2/tri
       	 $curdur[$v] = $durtbl[($cmd & 7) + $duroffs];
         redo NOTE;  # keep going till we hit a note       	 
       } else {
         # sq2/tri note
         my $dur = $curdur[$v];
       	 my $noteval = $notetbl[$cmd >> 1];
       	 $noteval -= 12 if $noteval && $v == 2;  # triangle sounds 8va lower
       	 push @{$e[$v]}, ['note', $totaltime, $dur, $v, $noteval, 100]
       	   if $noteval;
         $nexttime[$v] = $dur;
       }  # switch cmd
       last unless $p[$v];
     }  # note bareblock
     }  # for v
  } # for totaltime

  if ($cursong == 0x30 || $cursong < 0x10) {
    $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
    for ($v = 0; $v < 4; $v++) {
      push @{$opus->tracks_r},
       	  new MIDI::Track ({'events_r' 
    	  		     => MIDI::Score::score_r_to_events_r($e[$v])});
    }
    #$opus->dump({dump_tracks => 1});
    $opus->write_to_file(sprintf "mid/%02x-%s.mid",
  				   $cursong, $songtitles[$cursong]);
  }
}
print STDERR "\n";

#the end

sub do_voice_txt {
  my ($sq2addr, $sq1addr, $triaddr, $noiseaddr, $vnext) = @_;
  $vnext = 0xfba4 unless $vnext;  # hardcode "next" addr after last song
  my $dur = 0;
  my $vop = -1;
  for ($v = $sq2addr; $v < 0x10000; print OUT "\n") {
    if ($v == $sq2addr) {
      print OUT "square 2:\n";
      $mode = 'sq2';
    }
    if ($v == $sq1addr) {
      print OUT "square 1:\n";
      $mode = 'sq1';
    }
    if ($v == $triaddr) {
      print OUT "triangle:\n";
      $mode = 'tri';
    }
    if ($v == $noiseaddr) {
      print OUT "noise:\n";
      $mode = 'noi';
    }
    last if $v == $vnext || ($mode eq 'tri' && $vop == 0);

    printf OUT "  %04x", $v;

    $opcount = 0;
 redoop:
    $vop = &get_vbyte;
   
    if ($vop == 0) {
      if ($mode eq 'sq2' || $mode eq 'tri') {
        &print_cmd('Halt', 0);
      } elsif ($mode eq 'sq1') {
      	&print_cmd('Set sweep down', 0);
      } elsif ($mode eq 'noi') {
      	&print_cmd('DC al Capo');
      }
    } elsif ($mode eq 'sq1') {
      # note
      my ($vopnote, $vopdur) = (($vop & 0x3e) >> 1,
      				(($vop & 0x1) << 2) |
      				(($vop & 0xc0) >> 6)); 
      my $noteval = $notetbl[$vopnote];
      print OUT "   " x (5-$opcount);
      printf OUT "note %s (%02x=%04x) dur %02x (%02x)",
      		 $notenametbl[$vopnote], $vopnote, $noteval,
      		 $durtbl[$vopdur + $duroffs], $vopdur;
    } elsif ($mode eq 'noi') {
      # perc note
      my ($vopnote, $vopdur) = (($vop & 0x3e), 
      				(($vop & 0x1) << 2) |
      				(($vop & 0xc0) >> 6));
      print OUT "   " x (5-$opcount);
      my $notename;
      if ($vopnote == 0x30) {
      	$notename = 'snare';
      } elsif ($vopnote == 0x20) {
      	$notename = 'kick';
      } elsif ($vopnote & 0x10) {
      	$notename = 'closed hihat';
      } else {
      	$notename = 'rest';
      }
      printf OUT "%s dur %02x (%02x)", $notename,
      	$durtbl[$vopdur + $duroffs], $vopdur;
    } elsif ($vop >= 0x80) {
      &print_cmd('Set Duration', 0);
      $dur = $durtbl[($vop & 7) + $duroffs];
      printf OUT " %02x (%02x)",  $dur, ($vop & 7) + $duroffs;
    } else {
      # note
      my $noteval = $notetbl[$vop >> 1];
      my $notename = $notenametbl[$vop >> 1];
      # triangle sounds an octave lower
      if ($mode eq 'tri' && $noteval ne 'rest') {
      	$notename =~ s/^(.*)(\d)$/$1.($2-1)/e;
      }
      print OUT "   " x (5-$opcount);
      printf OUT "note %s (%02x=%04x)",
      		 $notename, $vop, $noteval;
    }
  }
  print OUT "\n";
}

sub print_cmd {
  my ($descr, $operands) = @_;
  my (@operands);
  while ($operands-- > 0) {
    push @operands, &get_vbyte;
  }
  print OUT ("   " x (5-$opcount)), $descr;
  return @operands;
}

sub get_vbyte {
  my $vbyte = unpack "C", substr $buf, $v++ - 0x8000, 1;
  printf OUT " %02x", $vbyte;
  $opcount++;
  return $vbyte;
}
