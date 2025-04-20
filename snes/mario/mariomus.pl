#!/usr/bin/perl

# cab - 19 August, 2001

use MIDI;

$tempo_factor = 24_000_000;  # 60M is std MIDI divisor

%songtitles = ('A' => [
				'Title', 'Main Map', 'Yoshi Island', 'Vanilla Dome',
				'Star Road', 'Forest of Illusion', 'Valley of Bowser',
				'Bowser Valley Entrance', 'Special'
			   ], 'B' => [
				'SMWorld 2', 'SMWorld', 'Water', 'Bowser', 'Koopa Kid',
				'Cave', 'Haunted House', 'Castle', 'Death',
				'Game Over', 'Koopa Kid Defeated', 'Level Complete',
				'Invincibility', 'P Block', '0E', '0F',
				'10', 'Bonus Game', 'Story', 'Bonus Game End',
				'Destroy Castle', 'Bowser', '16', '17',	'Bowser 2',
				'Bowser 2', '1A', 'Mario and Princess'
			   ]);

@optext = ("Set patch", "Pan", "Pan fade", "undef", "de",
           "df", "Master volume", "Master vol fade", "Tempo", "Tempo fade",
           "Transpose (global)", "e5", "e6", "e7", "e8",
           "Call Subroutine", "ea", "eb", "ec", "undef", "Portamento", 
           "Echo vbits/volume", "Echo off",
           "Echo delay, feedback, filter", "Echo volume fade");

@notes = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B");

@patchmap = ( 73, 50, 72, 12, 62, 25, 63, 114, # 00-07
              33, 3, 0, 50, 115, 0, 0, 0, # 08-0F
              0, 0, 50, 0, -42, -44, -60, -76, # 10-17 (13 and up are Perc)
              0, -61, 0, 0, 0, 0, 0, 0, # 18-1F
              0, 0, 0);               # 20-22

@transp   = ( 0, 0, 0, 0, 0, 0, 0, 0, # 00-07
              0, 0, 0, 0, -1, 0, 0, 0, # 08-0F
              0, 0, 0, 0, 0, 0, 0, 0, # 10-17
              0, 0, 0, 0, 0, 0, 0, 0, # 18-1F
              0, 0, 0);               # 20-22

if (@ARGV) {
  foreach (@ARGV) {
  	$bank = substr $_, 0, 1, "";
    $dosong{uc $bank}[hex $_] = 1;
  }
} else {
  @{$dosong{A}} = (1) x 9;
  @{$dosong{B}} = (1) x 0x1c;
}  

open ROM, "< mario.smc" or die;

# spc data start addr
$spcdata = &readrom(0x0e8000);
$songdata{A} = &readrom(0xe98b1);
$songdata{B} = &readrom(0xeaed6);

# tables from spc code
@oplen     = unpack "C*", substr($spcdata, 0x0fc2 - 0x500, 0x1d);
@durpcttbl = unpack "C*", substr($spcdata, 0x1268 - 0x500, 8);
@voltbl    = unpack "C*", substr($spcdata, 0x1270 - 0x500, 0x10);
@pantbl    = unpack "C*", substr($spcdata, 0x1280 - 0x500, 0x15);

SONGBANK:
foreach $songbank ('A', 'B') {
SONG:
for ($song = 0, $numdone = 0; $song < @{$dosong{$songbank}}; $song++) {
  next unless $dosong{$songbank}[$song];

  printf STDERR "%s%02x ", $songbank, $song;
  print STDERR "\n" if ++$numdone % 16 == 0;

  #open OUT, sprintf("> txt/%s%02X - %s",
  #					$songbank, $song, $songtitles{$songbank}[$song]);
  #printf OUT "Song %s%02x - %s:\n", 
  #		$songbank, $song, $songtitles{$songbank}[$song];

  my $songbase = unpack "v", substr($songdata{$songbank}, $song * 2, 2);

  # finally, a place for a closure.  lisp class was fun AND useful.
  my $readsong = sub { my ($format, $start, $len) = @_;
                       return unpack $format,
                         substr($songdata{$songbank}, $start - 0x1360, $len);
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
  my $tempo = 0x36;
  my $transpose = 0;  # global
  my $mvolume = 0xc0;

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
        my $cmd = &$readsong("C", $p[$v]++, 1);
        $cmd = 0x100 if $cmd == 0;  # hack for end repeat

        if ($cmd < 0x80) {
          $dur[$v] = $cmd;
          my $op1 = &$readsong("C", $p[$v]++, 1);
          if ($op1 < 0x80) {
            $durpct[$v] = $durpcttbl[($op1 >> 4) & 0x7];
            $velocity[$v] = int($voltbl[$op1 & 0xf] * 2/3);
            $cmd = &$readsong("C", $p[$v]++, 1);
          } else {
            $cmd = $op1;
          }
        }
        if ($cmd < 0xda) {
          # note
          my $notedur = int($dur[v] * $durpct[v] / 256);
          $notedur = $dur[v] - 2 if $notedur > $dur[v] - 2;
          $notedur = 1 if $notedur < 1;
          
          if ($cmd == 0xc6) {
          	# tie
            my ($last_ev) = grep {$_->[0] eq 'note'} reverse @{$e[$v]};
  	        $last_ev->[2] += $dur[$v];  # use full dur; already did notedur
          } elsif ($cmd == 0xc7) {
          	# rest
          } elsif ($cmd < 0xd0) {	
          	# note
            $cmd &= 0x7f;
            my ($octave, $note) = (int($cmd/0xc), $cmd % 0xc);
            my $mnote = 12 * ($octave + $t[$v]+2) + $note + $transpose[$v];
            push @{$e[$v]}, ['note', $totaltime, $notedur, $channel[$v],
            	 $mnote, $velocity[$v]];
          } else {
            # percussion note
            my $mnote = -$patchmap[$cmd-0xd0+0x13];
            #my $mnote = 38;
            push @{$e[$v]}, ['note', $totaltime, $notedur, 9, $mnote,
            	 $velocity[$v]];
          }
          $nexttime[$v] = $dur[$v];
        } else {
          # command
          my $oplen = $cmd == 0x100 ? 0 : $oplen[$cmd - 0xda] - 1;
          my (@op) = &$readsong("C*", $p[$v], $oplen);
          $p[$v] += $oplen;

          if ($cmd == 0xda) {
            # set patch
            my $inst = $patchmap[$op[0]];
            $t[$v] = $transp[$op[0]];
	    	push @{$e[$v]}, ['patch_change', $totaltime, $v, $inst];
          } elsif ($cmd == 0xe2) {
            # tempo
            $tempo = $op[0];
            push @{$cevents},
	          ['set_tempo', $totaltime, int($tempo_factor / $tempo)];
          } elsif ($cmd == 0xe3) {
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
          } elsif ($cmd == 0xe0) {
          	# master volume
          	$mvolume = $op[0];
          	push @{$e[$v]}, ['control_change', $totaltime, $channel[$v], 7,
          			int ($volume[$v] * $mvolume / 512)];
          } elsif ($cmd == 0xe1) {
          	# master volume fade
          	$mvolume = $op[1];
          	push @{$e[$v]}, ['control_change', $totaltime, $channel[$v], 7,
          			int ($volume[$v] * $mvolume / 512)];
      	  } elsif ($cmd == 0xe7) {
			# volume
			$volume[$v] = $op[0];
          	push @{$e[$v]}, ['control_change', $totaltime, $channel[$v], 7,
          			int ($volume[$v] * $mvolume / 512)];
          } elsif ($cmd == 0xe8) {
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
		  } elsif ($cmd == 0xdb) {
	  		# pan
	  		my $pan = $op[0] & 0x1f;
	  		my ($surroundL, $surroundR) = ($op[0] & 0x80, $op[0] & 0x40);
          	$pan[$v] = $pantbl[$pan];
	  		push @{$e[$v]}, ['control_change',
	  						 $totaltime, $channel[$v], 10, $pan[$v]];
		  } elsif ($cmd == 0xdc) {
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
		  #} elsif ($cmd == 0xe4) {
		  	# transpose (global)
          #} elsif ($cmd == 0xe5) {
          #  $rptaddr[$v] = $p[$v];
          #  $rptcount[$v] = 0;
          #} elsif ($cmd == 0xe6) {
          #  if (++$rptcount[$v] == $op[0]) {
          #    # done.
          #    #$rptaddr[$v] = 0;
          #    $rptcount[$v] = 0;
          #  } else {
          #    $p[$v] = $rptaddr;
          #  }
          } elsif ($cmd == 0xe9) {
            # gosub
            $subret[$v] = $p[$v];
            $subcount[$v] = $op[2];
            $substart[$v] = 0x100 * $op[1] + $op[0];
            $p[$v] = $substart[$v];
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
    push @{$opus->tracks_r},
    	new MIDI::Track ({'events_r'
    						 => MIDI::Score::score_r_to_events_r($e[$v])});
  }
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%s%02X - %s.mid",
  						 $songbank, $song, $songtitles{$songbank}[$song]);
} # foreach song
} # foreach bank

print STDERR "\n";
close ROM;


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

  seek ROM, &s2o($src), 0;
  read ROM, $buf, 4;
  my ($len, $addr) = unpack "v2", $buf;
  read ROM, $buf, $len;  # might get a couple extra, no biggie
  return $buf;
}
