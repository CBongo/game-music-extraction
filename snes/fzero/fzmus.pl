#!/usr/bin/perl

# cab - 25 August, 2001

use MIDI;

$tempo_factor = 24_000_000;  # 60M is std MIDI divisor

@songtitles = ("00", "Lights", "Ready", "You Lose", "Title", "Ranking", "06",
				"Ending", "Mute City", "Big Blue", "Sand Ocean", "Silence",
				"Port Town", "Red Canyon", "White Land 1", "White Land 2",
				"Fire Field", "Death Wind");

@optext = ("Set patch", "Pan", "Pan fade", "e3", "e4",
           "Master volume", "Master vol fade", "Tempo", "Tempo fade",
           "Transpose (global)", "Transpose (voice)", "eb", "ec",
           "Voice volume", "Voice volume fade", "Call Subroutine",
           "f0", "f1", "f2", "f3", "f4", "Set echo vbits/volume", 
           "Disable echo", "Set echo delay/feedback/filter",
           "Echo volume fade", "Portamento", "Set perc base patch");

@notes = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B");

@patchmap = ( 0, 0, 0, 56, 0, 0, 0, 0, # 00-07
              39, 0, 0, 80, 0, 0, 0, 71, # 08-0F
              73, -35, 0, 0, -38, -42, 0, 62, # 10-17
              37, 0, 17, 0, 117, 40, 0, 0, # 18-1F
              0, 0, 0);               # 20-22

@transp   = ( 0, 0, 0, 0, 0, 0, 0, 0, # 00-07
              0, 0, 0, 0, 0, 0, 0, 0, # 08-0F
              0, 0, 0, 0, 0, 0, 0, 0, # 10-17
              0, 0, 0, 0, 0, 0, 0, 0, # 18-1F
              0, 0, 0);               # 20-22

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x 0x12;
}  

open ROM, "< fzero.smc" or die;

# spc data start addr
$spcdata = &readrom(0x018000);
#open OUT, "> txt/memdump";
#&hexdump(0, $spcdata);
#close OUT;
close ROM;

# tables from spc code
@oplen     = unpack "C*", substr($spcdata, 0x0f1f, 0x1b);
@durpcttbl = unpack "C*", substr($spcdata, 0x500, 8);
@voltbl    = unpack "C*", substr($spcdata, 0x508, 0x10);
@pantbl    = unpack "C*", substr($spcdata, 0x116e, 0x15);

SONG:
for ($song = 0, $numdone = 0; $song < @dosong; $song++) {
  next unless $dosong[$song];

  printf STDERR "%02x ", $song;
  print STDERR "\n" if ++$numdone % 16 == 0;

  my (@out);
  open OUT, sprintf("> txt/%02X - %s", $song, $songtitles[$song]);
  printf OUT "Song %02x - %s:\n", $song, $songtitles[$song];

  my $songbase = unpack "v", substr($spcdata, 0x1fd6 + $song * 2, 2);

  # finally, a place for a closure.  lisp class was fun AND useful.
  my $readsong = sub { my ($format, $start, $len) = @_;
                       return unpack $format,
                         substr($spcdata, $start, $len);
                     };
  
  #&hexdump($song[$song]{apudest}, $sdata);

  printf OUT "Song start: %04x\n", $songbase;

  my (@vtbls, @vused);
  my $msptr = $songbase;
  my $rptcount = 0;
  for (;;) {
    my $vtbladdr = &$readsong("v", $msptr, 2);
    $msptr += 2;
    if ($vtbladdr < 0x100) {
      printf OUT "VTBL cmd: %04x\n", $vtbladdr;
      if ($vtbladdr) {
        # repeat; 0xff is forever
        my $vtblarg = &$readsong("v", $msptr, 2);
        printf OUT "VTBL loop arg: %04x\n", $vtblarg;
        $msptr += 2;
      } else {
        # done.
        last;
      }
    } else {
      my @vtbl     = &$readsong("v8", $vtbladdr, 16);
      push @vtbls, [@vtbl];
      printf OUT "VTBL: %04x\n", $vtbladdr;
      for (my $i = 0; $i < 8; $i++) {
        printf OUT "  %d:%04x", $i, $vtbl[$i];
        $vused[$i] = 1 if $vtbl[$i];
      }
      print OUT "\n";
    }
  }


  $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 48});
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [];
  
  my $totaltime = 0;
  my $transpose = 0;  # global
  my $mvolume = 0xc0;
  my $percbase = 0;
  my $tempo = 0x20;
  push @{$cevents},
       ['set_tempo', 0, int($tempo_factor / $tempo)];

  my @dur;
  my @durpct;
  my @volume = (0xff) x 8;
  my @velocity;
  my @pan = ($pantbl[0xa]) x 8;
  my @channel = (0,1,2,3,4,5,6,7);
  my @perckey;
  my @t;  # transpose (from patch settings)
  my @transpose;
  my @substart;
  my @subret;
  my @subcount;
  my @rptaddr;
  my @rptcount;

  # shorthand array to access track events
  my @e;
  for ($v = 0; $v < 8; $v++) {
    @{$e[$v]} = [];
    next unless $vused[$v];
    push @{$e[$v]}, ['track_name', 0, "Voice $v"];
    push @{$e[$v]}, ['control_change', 0, $v, 0, 0]; # bank 0
    #push @{$e[$v]}, ['patch_change', 0, $v, $patchmap[0]];
    $t[$v] = $transp[0];
  }

  VTBL:
  for ($vtblnum = 0; $vtblnum < @vtbls; $vtblnum++) {
    my @vtbl = @{$vtbls[$vtblnum]};
    my @p = @vtbl;
    my @nexttime = (1) x 8;
    
    for (;; $totaltime++) {
      VOICE:
      for (my $v = 0; $v < 8; $v++) {
        next unless $p[$v];  # skip inactive voice
        next if --$nexttime[$v] > 0;  # wait for next time to come
#printf STDERR "time $totaltime v$v p=%04x\n", $p[$v];
        push @{$out[$v]}, sprintf "      %04X: ", $p[$v];
        my $out = \$out[$v][-1];
        
        my $cmd = &$readsong("C", $p[$v]++, 1);
        my $opcount = 0;
        $$out .= sprintf "%02X ", $cmd;
        $cmd = 0x100 if $cmd == 0;  # hack for end repeat

        if ($cmd < 0x80) {
          $dur[$v] = $cmd;
          my $op1 = &$readsong("C", $p[$v]++, 1);
          $$out .= sprintf "%02X ", $op1;
          $opcount++;
          if ($op1 < 0x80) {
            $durpct[$v] = $durpcttbl[($op1 >> 4) & 0x7];
            $velocity[$v] = $voltbl[$op1 & 0xf] >> 1;
            $cmd = &$readsong("C", $p[$v]++, 1);
            $$out .= sprintf "%02X ", $cmd;
            $opcount++;
          } else {
            $cmd = $op1;
          }
        }
        if ($cmd < 0xe0) {
          # note
          $$out .= "   " x (4-$opcount) . "  ";
          my $notedesc;
          
          my $notedur = int($dur[v] * $durpct[v] / 256);
          $notedur = $dur[v] - 2 if $notedur > $dur[v] - 2;
          $notedur = 1 if $notedur < 1;
          
          if ($cmd == 0xc8) {
          	# tie
            my ($last_ev) = grep {$_->[0] eq 'note'} reverse @{$e[$v]};
  	        $last_ev->[2] += $dur[$v];  # use full dur; already did notedur
  	        $notedesc = "Tie";
          } elsif ($cmd == 0xc9) {
          	# rest
          	$notedesc = "Rest";
          } elsif ($cmd < 0xca) {	
          	# note
            $cmd &= 0x7f;
            my ($octave, $note) = (int($cmd/0xc), $cmd % 0xc);
            my $mnote = 12 * ($octave + $t[$v]+2) + $note 
            					+ $transpose + $transpose[$v];
            push @{$e[$v]}, ['note', $totaltime, $notedur, $channel[$v],
            	 $mnote, $velocity[$v]];
            $notedesc = sprintf "Note %-3s (%02X)",
            				 $notes[$note] . ($octave + 2), $mnote;
          } else {
            # percussion note
            my $mnote = -$patchmap[$cmd-0xca+$percbase];
  #printf STDERR "perc cmd=%02x percbase=%02x mnote=%d\n", $cmd, $percbase, $mnote;
            #my $mnote = 38;
            push @{$e[$v]}, ['note', $totaltime, $notedur, 9, $mnote,
            	 $velocity[$v]];
            $notedesc = sprintf "Perc %02X (%02X)",
            				 $cmd, $cmd - 0xca + $percbase;
          }
          $nexttime[$v] = $dur[$v];
          $$out .= sprintf "%-15s Dur %02X (%02X)",
          					$notedesc, $dur[$v], $notedur;
        } else {
          # command
          my $oplen = $cmd == 0x100 ? 0 : $oplen[$cmd - 0xe0];
          my (@op) = &$readsong("C*", $p[$v], $oplen);
          $p[$v] += $oplen;
          $$out .= join("", map { sprintf "%02X ", $_ } @op);
		  $$out .= "   " x (4-$oplen) . "  "
		  				 . ($cmd == 0x100 ? "End repeat/return"
		  				 	 : $optext[$cmd - 0xe0]);
		  
          if ($cmd == 0xe0) {
            # set patch
            if ($op[0] >= 0x80) {
              $op[0] = $op[0] - 0xca + $percbase;
            }
            my $inst = $patchmap[$op[0]];
            $t[$v] = $transp[$op[0]];
	    	push @{$e[$v]}, ['patch_change', $totaltime, $v, $inst];
		  } elsif ($cmd == 0xe1) {
	  		# pan
	  		my $pan = $op[0] & 0x1f;
	 		my ($surroundL, $surroundR) = ($op[0] & 0x80, $op[0] & 0x40);
          	$pan[$v] = $pantbl[$pan];
	  		push @{$e[$v]}, ['control_change',
	  						 $totaltime, $channel[$v], 10, $pan[$v]];
		  } elsif ($cmd == 0xe2) {
	  		# pan fade - $op0 = duration, $op1 = target
          	my ($tdelta, $cb);
          	my $bdelta = ($pantbl[$op[1]]) - $pan[$v];
          	my $stepinc = $bdelta / $op[0];
	  		for ($tdelta = 1, $cb = $pan[$v] + $stepinc;
                 	$tdelta < $op[0]; $tdelta++, $cb += $stepinc) {
              push @{$e[$v]}, ['control_change', $totaltime + $tdelta * 2,
                         $channel[$v], 10, int($cb)];
          	}
          	$pan[$v] = $op[1];
          } elsif ($cmd == 0xe5) {
          	# master volume
          	$mvolume = $op[0];
          	push @{$e[$v]}, ['control_change', $totaltime, $channel[$v], 7,
          			int ($volume[$v] * $mvolume / 512)];
          } elsif ($cmd == 0xe6) {
          	# master volume fade
          	$mvolume = $op[1];
          	push @{$e[$v]}, ['control_change', $totaltime, $channel[$v], 7,
          			int ($volume[$v] * $mvolume / 512)];
          } elsif ($cmd == 0xe7) {
            # tempo
            $tempo = $op[0];
            push @{$cevents},
	          ['set_tempo', $totaltime, int($tempo_factor / $tempo)];
          } elsif ($cmd == 0xe8) {
            # tempo fade
            my ($tdelta, $cb);
            my $bdelta = $op[1] - $tempo;
            my $stepinc = $bdelta / $op[0];
	    	for ($tdelta = 1, $cb = $tempo + $stepinc;
                   $tdelta < $op[0]; $tdelta++, $cb += $stepinc) {
               push @{$cevents}, ['set_tempo', $totaltime + $tdelta * 2, 
                          int($tempo_factor / $cb)];
            }
            $tempo = $op[1];
      	  } elsif ($cmd == 0xed) {
			# volume
			$volume[$v] = $op[0];
          	push @{$e[$v]}, ['control_change', $totaltime, $channel[$v], 7,
          			int ($volume[$v] * $mvolume / 512)];
          } elsif ($cmd == 0xee) {
          	# volume fade
          	my ($tdelta, $cb);
          	my $bdelta = $op[1] - $volume[$v];
          	my $stepinc = $bdelta / $op[0];
          	for ($tdelta = 1, $cb = $volume[$v] + $stepinc;
                 	$tdelta < $op[0]; $tdelta++, $cb += $stepinc) {
	          push @{$e[$v]}, ['control_change', $totaltime + $tdelta * 2,
	          	 	 $channel[$v], 7, int ($cb * $mvolume / 512)];
          	}
            $volume[$v] = $op[1];
		  } elsif ($cmd == 0xe9) {
		  	# transpose (global)
		  	$transpose = $op[0];
		  } elsif ($cmd == 0xea) {
		  	# transpose (voice)
		  	$transpose[$v] = $op[0];
          } elsif ($cmd == 0xef) {
            # gosub
            $subret[$v] = $p[$v];
            $subcount[$v] = $op[2];
            $substart[$v] = 0x100 * $op[1] + $op[0];
            $p[$v] = $substart[$v];
          } elsif ($cmd == 0xfa) {
          	$percbase = $op[0];
          } elsif ($cmd == 0x100) {
            # end repeat/return
            if ($subcount[$v]) {
              if (--$subcount[$v] > 0) {
              	$p[$v] = $substart[$v];
              } else {
                $p[$v] = $subret[$v];
                $substart[$v] = 0;
                $subret[$v] = 0;
              }
            } else {
              next VTBL;
            }
          } # cmd switch
          redo VOICE;  # keep going till we hit a note command
        }  # cmd/note switch
      } # for v
    } # for totaltime
  } # foreach vtbl
  
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  for ($v = 0; $v < 8; $v++) {
  	printf OUT "    Voice %d:\n", $v;
  	print OUT join("\n", @{$out[$v]}), "\n";
  	
    push @{$opus->tracks_r},
    	new MIDI::Track ({'events_r'
    						 => MIDI::Score::score_r_to_events_r($e[$v])});
  }
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%02X - %s.mid",
  						 $song, $songtitles[$song]);
} # foreach song

print STDERR "\n";
close OUT;

sub hexdump {
  my ($addr, $data, $line) = @_;
  $line ||= 16;

  for (my $p = 0; $p < length $data; $p += $line) {
    printf OUT "    %04x: ", $addr + $p;
    if ($line == 16) {
      print OUT join(' ', map {sprintf "%02x", ord }
			 split //, substr($data, $p, 8)),
  	  '  ', join(' ', map {sprintf "%02x", ord }
			 split //, substr($data, $p + 8, 8)), "\n";
    } else {
      print OUT join(' ', map {sprintf "%02x", ord }
			 split //, substr($data, $p, $line)), "\n";
    }
  }
}

sub s2o {
  my ($bank, $offs) = (int($_[0] / 0x10000), $_[0] % 0x10000);
  return $bank * 0x8000 + $offs - 0x8000 + 0x200;
}

sub readrom {
  my ($src) = @_;
  my ($buf);
  my $mem = "\0" x 65536;

  seek ROM, &s2o($src), 0;
  for (;;) {
  	read ROM, $buf, 4;
  	my ($len, $addr) = unpack "v2", $buf;
  	last unless $len;
  	read ROM, $buf, $len;
  	substr($mem, $addr, $len) = $buf;
  }
  return $mem;
}
