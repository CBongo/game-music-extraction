#!/usr/bin/perl

# cab - May 16, 2001

use MIDI;

$tempo_factor = 12_000_000;  # 60M is std MIDI divisor

@songtitles = ("Powerup", "(1)Desert", "(2)Bubbles", "(3)Deja Vu",
			   "(4)The Moai", "(7)High Speed", "(5)Inferno", "(6)Garden",
			   "(6)Garden-copy", "(8)Factory", "(9)Organic",
			   "(1)Boss 1", "(3)Boss 3", "Death", "(2)Boss 2",
			   "(9)Boss 9 - Brain", "Weaponry", "Launch",
			   "King of Kings (high score)", "Title", "Continue",
			   "(6)Boss 6 - Laser Platform", "(4)Boss 4 - Big Laser",
			   "(8)Boss 8 - Spiderbot", "Powerup 2", "(7)Boss 7 - Missles",
			   "Secret Stage", "Credits", "(5)Boss 5 - Crystal Star");

@songtitles = map { ($_, $_) } @songtitles;
$songtitles[0x81] = "";
push @songtitles, ("FX", "FX", "FX", "FX", "FX");
$songtitles[0xa1] = "";
push @songtitles, ("FX", "Konami Sound", "FX", "FX", "FX", "FX");

@optext = ("Set patch", "Pan", "Pan fade", "e3", "e4",
           "Begin repeat/mod", "End repeat/mod", "Tempo", "nop",
           "nop", "Transpose (voice)",
           "Tremolo", "Tremolo off", "Volume", "Volume fade",
           "Call Subroutine", "f0", "f1", "f2", "f3", "Detune", 
           "nop (echo)", "nop (echo)",
           "nop (echo)", "nop (echo)",
           "f9", "nop", "Envelope", "nop", "nop", "nop");

@notes = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B");

@patchmap = ( 38, 56, 7, 0, 52, 55, 0, 0, # 00-07
              68, 33, 0, 80, 0, 122, 0, 62, # 08-0F
              0, 0, 0, 50, -36, -40, -42, -46, # 10-17
              -49, -45, -48, -50, 0, 0, 0, 0, # 18-1F
              0, 0, 0);               # 20-22

@transp   = ( -2, 0, 0, 0, 0, 0, 0, 0, # 00-07
              0, -1, 0, 0, 0, 0, 0, 0, # 08-0F
              0, 0, 0, 0, 0, 0, 0, 0, # 10-17
              0, 0, 0, 0, 0, 0, 0, 0, # 18-1F
              0, 0, 0);               # 20-22

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1,0) x (0x3a / 2);
  foreach (0x82 .. 0x86, 0xa2 .. 0xa7) {
    $dosong[$_] = 1;
  }
}  

open ROM, "< gradius3.smc" or die;

# spc data start addr
$spcdata = &decompress(0x0f8000);
open SPCBIN, "> g3spcde.bin" or die;
print SPCBIN pack "v2", length($spcdata), 0x0400;
print SPCBIN $spcdata;
print SPCBIN pack "v2", 0, 0x400;
close SPCBIN;

# tables from spc code
@oplen  = unpack "C*", substr($spcdata, 0x0d7a - 0x400, 0x1f);
@durpct = unpack "C*", substr($spcdata, 0x10e5 - 0x400, 8);
@voltbl = unpack "C*", substr($spcdata, 0x10ed - 0x400, 0x10);
@pantbl = unpack "C*", substr($spcdata, 0x10aa - 0x400, 0x16);

# song ptrs
seek ROM, &s2o(0x018937), 0;
read ROM, $buf, 0x3a * 2;
@sptr = map { $_ + 0x010000 } unpack "v*", $buf;
for ($i = 0; $i < 0x3a; $i++) {
    seek ROM, &s2o($sptr[$i]), 0;
    read ROM, $buf, 7;
    my ($type, $apucmd, $apudest, $src, $srcbank)
      = unpack "C2v2C", $buf;
    $src += $srcbank * 0x10000;
    #printf "Song %02x: %d %02x %04x %06x\n",
    #  $i, $type, $apucmd, $apudest, $src;
    $song[$i]{apucmd}  = $apucmd;
    $song[$i]{apudest} = $apudest;
    $song[$i]{src}     = $src;
}

# spc song ptrs
for ($i = 0x82; $i < 0xa8; $i++) {
    my ($apudest) 
      = unpack "v", substr($spcdata, (0x10fb - 0x400) + 2 * $i, 2);
    #printf "Song %02x: %02x %04x %06x\n",
    #  $i, $i, $apudest, -1;
    $song[$i]{apucmd}  = $i;
    $song[$i]{apudest} = $apudest;
    $song[$i]{src}     = -1;
    $i = 0xa1 if $i == 0x86;  # skip ahead
}

for ($song = 0, $numdone = 0; $song < @dosong; $song++) {
  next unless $dosong[$song];

  printf STDERR "%02x ", $song;
  print STDERR "\n" if ++$numdone % 16 == 0;

  $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 24});
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [];

  open OUT, sprintf("> txt/%02X - %s", $song, $songtitles[$song]);
  printf OUT "Song %02x - %s:\n", $song, $songtitles[$song];

  my $songbase = $song[$song]{apudest};
  my $sdata;
  if ($song[$song]{src} < 0) {
    # spc song.  no length; copy everything to end - not so bad,
    # as spc songs are at the end of the spc data.
    $sdata = substr $spcdata, $song[$song]{apudest} - 0x400;
  } else {
    # copy song data from ROM
    $sdata = &decompress($song[$song]{src});
  }
  # finally, a place for a closure.  lisp class was fun AND useful.
  my $readsong = sub { my ($format, $start, $len) = @_;
                       return unpack $format,
                         substr($sdata, $start - $songbase, $len);
                     };
  
  #&hexdump($song[$song]{apudest}, $sdata);

  printf OUT "Song start: %04x\n", $songbase;

  my @vtbls;
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
      }
      print OUT "\n";
    }
  }

    my $maxtime = 0;
    for (my $v = 0; $v < 8; $v++) {
#print STDERR "v=$v\n";
      print OUT "Voice $v:\n";

      my $dur;
      my $durpct;
      my $totaltime = 0;
      my $master_rpt = 1;

      my $velocity = 100;
      my $volume = 0;
      my $balance = 64;
      my $tempo = 120;
      my $channel = $v;
      my $perckey = 0;
      my $t = 0;  # transpose (from patch settings)
      my $transpose = 0;
      my $substart = 0;
      my $subret = 0;
      my $subcount = 0;
      my $rptaddr  = 0;
      my $rptcount = 0;

      my $track = new MIDI::Track;
      my $e = [];  # track events
      push @$e, ['track_name', 0, "Voice $v"];
      push @$e, ['control_change', 0, $channel, 0, 0]; # bank 0

  for ($vtblnum = 0; $vtblnum < @vtbls; $vtblnum++) {
    my @vtbl = @{$vtbls[$vtblnum]};
      for (my $p = $vtbl[$v]; ; $p++) {
        last unless $p;
        #last unless ($v > 0 || $subret || $p < $vtbl[$v+1]);
        last if ($v > 0) && ($totaltime >= $maxtime
        					 || $totaltime >= $vtbltime[$vtblnum]);

        my $opcount = 0;
        my $cmd = &$readsong("C", $p, 1);
#printf STDERR " p=%04x  cmd=%02x\n", $p, $cmd;
        printf OUT "  %04x:  %02x", $p, $cmd;
        $cmd = 0x100 if $cmd == 0;  # hack for end repeat

        if ($cmd < 0x80) {
          $dur = $cmd;
          $opcount = 1;
          my $op1 = &$readsong("C", $p+1, 1);
          printf OUT " %02x", $op1;
#printf STDERR " op1=%02x\n", $op1;
          if ($op1 < 0x80) {
            $opcount++;
            $durpct = $durpct[($op1 >> 4) & 0x7];
            #$volume = $voltbl[$op1 & 0xf];
            $cmd = &$readsong("C", $p+2, 1);
            printf OUT " %02x", $cmd;
          } else {
            $cmd = $op1;
          }
          $p += $opcount;
#printf STDERR " final cmd=%02x p=%04x\n", $cmd, $p;
        }
        if ($cmd < 0xe0) {
          # note
          print OUT "   " x (4-$opcount);
          my $notedur = int($dur * $durpct / 256);
          $notedur = $dur - 2 if $notedur > $dur - 2;
          $notedur = 1 if $notedur < 1;
          
          if ($cmd == 0xc8) {
            printf OUT "Tie          Dur %02x", $dur;   
            my ($last_ev) = grep {$_->[0] eq 'note'} reverse @$e;
  	    $last_ev->[2] += $dur;  # use full dur; already did notedur
          } elsif ($cmd == 0xc9) {
            printf OUT "Rest         Dur %02x", $dur;   
          } elsif ($cmd < 0xca) {
            $cmd &= 0x7f;
            my ($octave, $note) = (int($cmd/0xc), $cmd % 0xc);
            printf OUT "Note %s     Dur %02x",
                $notes[$note] . $octave, $dur;
            my $mnote = 12 * ($octave + $t+2) + $note + $transpose;
            push @$e, ['note', $totaltime, $notedur, $channel, $mnote, $velocity];
          } else {
            # percussion note
            printf OUT "Perc %02x     Dur %02x",
                 $cmd - 0xca, $dur;
            my $mnote = -$patchmap[$cmd-0xca+0x14];
            #my $mnote = 38;
            push @$e, ['note', $totaltime, $notedur, 9, $mnote, $velocity];
          }
          $totaltime += $dur;
        } else {
          # command
          my $oplen = $cmd == 0x100 ? 0 : $oplen[$cmd - 0xe0];
          my $optext = $cmd == 0x100 ? "End repeat/return" : $optext[$cmd - 0xe0];
          my (@op) = &$readsong("C*", $p+1, $oplen);
          print OUT map {sprintf " %02x", $_} @op;
          print OUT "   " x (4-$oplen);
          print OUT $optext;

          if ($cmd == 0xe0) {
            # set patch
            my $inst = $patchmap[$op[0]];
            $t = $transp[$op[0]];
	    push @$e, ['patch_change', $totaltime, $v, $inst];
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
          } elsif ($cmd == 0xe5) {
            $rptaddr = $p + $oplen;
            $rptcount = 0;
          } elsif ($cmd == 0xe6) {
            if (++$rptcount == $op[0]) {
              # done.
              #$rptaddr = 0;
              $rptcount = 0;
            } else {
              $p = $rptaddr;
              $oplen = 0;
            }
          } elsif ($cmd == 0xef) {
            # gosub
            $subret = $p + $oplen;
            $subcount = $op[2];
            $substart = 0x100 * $op[1] + $op[0];
            $p = $substart;
            $oplen = -1;
          } elsif ($cmd == 0x100) {
            # end repeat/return
            if ($subcount) {
              if (--$subcount > 0) {
              	$p = $substart;
              	$oplen = -1;
              } else {
                $p = $subret;
                $substart = 0;
                $subret = 0;
                $oplen = 0;
              }
            } else {
            	printf STDERR "voice %d tried main loop return at %04x\n", $v, $p if $v;
              $maxtime = $totaltime if $v == 0;
              $vtbltime[$vtblnum] = $totaltime if $v == 0;
              last;
            }
          }
          $p += $oplen;
        }
        
        print OUT "\n";
      } # for p
      print OUT "\n";
    } # foreach vtbl
      print OUT "\n";
      # set track events from work array
      $track->events_r(MIDI::Score::score_r_to_events_r($e));
      push @{$opus->tracks_r}, $track;
    } # for v
  &hexdump($song[$song]{apudest}, $sdata);
  close OUT;
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%02X - %s.mid", $song, $songtitles[$song]);
}

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

sub decompress {
  my ($src) = @_;
  my ($buf, $out);

  seek ROM, &s2o($src), 0;
  read ROM, $buf, 2;
  my ($len) = unpack "v", $buf;
  read ROM, $buf, $len;  # might get a couple extra, no biggie
  my $p = 0;
  while ($p < $len - 2) {
    my ($op) = unpack "C", substr($buf, $p++, 1);
    my $qty = ($op & 0x1f) + 2;
#printf STDERR "%04x: op:%02x", length($out), $op;
    if ($op < 0x80) {
      # copy previously decompressed data
      $qty = ($op >> 2) + 2;
      my ($where) = unpack "C", substr($buf, $p++, 1);
#printf STDERR " where1=%04x", $where;
      $where = (((($op & 3) << 8) | $where) - 0x3df) & 0x3ff;
#printf STDERR " where2=%04x", $where;
      $where += int(length($out) / 0x400) * 0x400;
      $where -= 0x400 if $where > length $out;
#printf STDERR " where3=%04x qty=%02x", $where, $qty;
      # fast way breaks on overlapping writes
      #$out .= substr($out, $where, $qty);
      while ($qty-- > 0) {
        $out .= substr($out, $where++, 1);
      }
    } elsif ($op < 0xa0) {
      # straight copy
      $out .= substr($buf, $p, $qty - 2);
      $p += $qty - 2;      
    } elsif ($op < 0xc0) {
      # expand bytes to words
      $out .= join "", map { "\0$_" } split //, substr($buf, $p, $qty);
      $p += $qty;
    } elsif ($op < 0xe0) {
      # repeat byte
      $out .= substr($buf, $p++, 1) x $qty;
    } else {
      # zeros
      $out .= "\0" x $qty;
    }
#print STDERR "\n";
  }
  return $out;
}
