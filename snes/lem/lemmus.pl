#!/usr/bin/perl

# cab - March 2001

use MIDI;

$tempo_factor = 12_000_000;  # 60M is std MIDI divisor

@optext = ("Set patch", "e1 (vol?)", "e2", "e3", "e4", "e5",
           "Master vol fade", "Set tempo", "Tempo fade",
           "Transpose (global)", "Transpose (voice)",
           "eb", "ec", "ed", "ee",
           "Begin repeat", "f0", "f1", "f2", "f3", "f4", 
           "Set echo voices/volume", "Disable echo",
           "Set echo delay, fdbk, filter", "Echo vol fade",
           "f9", "Patch perc offset", "fb", "fc", "fd", "fe");

@notes = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B");

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x 0x1c;
}  

open ROM, "< lemmings.smc" or die;

seek ROM, &s2o(0x00f1a1), 0;
read ROM, $buf, 0x1c * 8;
@songtitles = unpack "A8" x 0x1c, $buf;

seek ROM, &s2o(0x08d4c7), 0;

read ROM, $buf, 0x2d * 2;
@sptr = map { $_ + 0x080000 } unpack "v*", $buf;

for ($i = 0; $i < 0x1c; $i++) {
    read ROM, $buf, 7;
    my ($samp, $tbl) = unpack "v2", substr($buf, 3, 4);
    my ($song) = &unpack3(substr($buf, 0, 3));
    push @sdata, $song;
    push @samp, $samp + 0x080000;
    push @tbl, $tbl + 0x080000;
}

# spc data start addr
read ROM, $buf, 3;
$spcdata = &unpack3($buf) + 4; # skip len, addr

for ($i = 0; $i < 8; $i++) {
  read ROM, $buf, 4;
  my ($gsamp, $gdata) = unpack "v*", $buf;
  push @gsamp, $gsamp + 0x080000;
  push @gdata, $gdata + 0x080000;
}

seek ROM, &s2o($spcdata + 0xe31 - 0x800), 0;
read ROM, $buf, 0x1f;
@oplen = unpack "C*", $buf;

for ($song = 0; $song < 0x1c; $song++) {
  next unless $dosong[$song];
  printf STDERR "%02x ", $song;

  $opus = new MIDI::Opus ('format' => 1);
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [];

  my (@sampptrs, @sblocks, $tblock);
  $mem = "\000" x 0x10000;
  my $actsong = ($sptr[$song] - 0x08d521) / 7;

  open OUT, sprintf("> txt/%02X - %s", $song, $songtitles[$song]);
  printf OUT "Song %02x (%02x) - %s:\n", $song, $actsong, $songtitles[$song];

  @sampptrs = &readsamp($samp[$actsong]);
  print OUT "  Samples: ", join(' ', map { sprintf "%06x", $_ } @sampptrs), "\n";

  ($tblock) = &readblocks($tbl[$actsong], 1);
  @sblocks  = &readblocks($sdata[$actsong], 0);
  print OUT "  Instrument block:\n";
  &hexdump(@{$tblock}, 6);
  print OUT "\n";
  foreach $block (@sblocks) {
    substr($mem, $block->[0], length $block->[1]) = $block->[1];
  }
  my $msptr = getmemw(0x1800);
  printf OUT "Song start: %04x\n", $msptr;

  while ($msptr) {
    my $percpatch = 0;
    my $vtbl = getmemw($msptr);
    my @vtbl = unpack("v8", substr($mem, $vtbl, 16));
    printf OUT "VTBL: %04x\n", $vtbl;
    for (my $i = 0; $i < 8; $i++) {
      printf OUT "  %d:%04x", $i, $vtbl[$i];
    }
    print OUT "\n";

    for (my $v = 0; $v < 8; $v++) {
#print STDERR "v=$v\n";
      print OUT "Voice $v:\n";
      my $track = new MIDI::Track;

      my $dur;
      my $durpct;
      my $totaltime;
      my $master_rpt = 1;

      my $velocity = 100;
      my $volume = 0;
      my $balance = 64;
      my $tempo = 120;
      my $channel = $v;
      my $perckey = 0;
      my $t = 0;  # transpose (from patch settings)
      my $transpose = 0;
      my $e = [];  # track events
      push @$e, ['track_name', 0, "Voice " . ($v + 1)];
      push @$e, ['control_change', 0, $channel, 0, 0]; # bank 0

      for (my $p = $vtbl[$v]; $p < $vtbl[$v+1]; $p++) {
        my $opcount = 0;
        my $cmd = getmem($p);
#printf STDERR " p=%04x  cmd=%02x\n", $p, $cmd;
        printf OUT "  %04x:  %02x", $p, $cmd;
        $cmd = 0x100 if $cmd == 0;  # hack for end repeat

        if ($cmd < 0x80) {
          $dur = $cmd;
          $opcount = 1;
          my $op1 = getmem($p+1);
          printf OUT " %02x", $op1;
#printf STDERR " op1=%02x\n", $op1;
          if ($op1 < 0x80) {
            $opcount++;
            $durpct = $op1;
            my $op2 = getmem($p+2);
            printf OUT " %02x", $op2;
#printf STDERR " op2=%02x\n", $op2;
            if ($op2 < 0x80) {
              $opcount++;
              $cmd = getmem($p+3);
              printf OUT " %02x", $cmd;
            } else {
              $cmd = $op2;
            }
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
          $notedur ||= 1;
          $notedur = $dur - 2 if $notedur > $dur - 2;

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
                 $cmd - 0xca + $percpatch, $dur;
            #my $mnote = -$sampmap[$cmd-0xca+$percpatch];
            my $mnote = 38;
            push @$e, ['note', $totaltime, $notedur, 9, $mnote, $velocity];
          }
          $totaltime += $dur << 2;
        } else {
          my $oplen = $cmd == 0x100 ? 0 : $oplen[$cmd - 0xe0];
          my $optext = $cmd == 0x100 ? "End repeat" : $optext[$cmd - 0xe0];
          my (@op) = unpack "C*", substr($mem, $p+1, $oplen);
          print OUT map {sprintf " %02x", $_} @op;
          print OUT "   " x (4-$oplen);
          print OUT $optext;

          if ($cmd == 0xe0) {
            # set patch
            my $inst = 0;
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
          } elsif ($cmd == 0xfa) {
            $percpatch = $op[0];
          }
          $p += $oplen;
        }
        
        print OUT "\n";
      } # for p
      print OUT "\n";
      # set track events from work array
      $track->events_r(MIDI::Score::score_r_to_events_r($e));
      push @{$opus->tracks_r}, $track;
    } # for v
    $msptr = 0;  # just do first vtbl for now
  }
  close OUT;
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file(sprintf "mid/%02X - %s.mid", $song, $songtitles[$song]);
}

print STDERR "\n";
close ROM;

sub readsamp {
  my ($addr) = @_;
  my ($buf, @sampptrs);

  seek ROM, &s2o($addr), 0;
  while (1) {
    read ROM, $buf, 3;
    my ($s) = unpack3($buf);
    last if $s % 0x10000 == 0;
    push @sampptrs, $s;
  }
  return @sampptrs;
}
sub readblocks {
  my ($addr, $single) = @_;
  my ($buf, @blocks);

  seek ROM, &s2o($addr), 0;
  while (1) {
    read ROM, $buf, 4;
    my ($len, $addr) = unpack "v*", $buf;
    last if $len == 0;
    read ROM, $buf, $len;
    push @blocks, [ $addr, $buf ]; 
    last if $single;
  }
  return @blocks;
}

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

sub getmem {
  my ($addr) = @_;
  return ord(substr($mem, $addr, 1));
}
sub getmemw {
  my ($addr) = @_;
  return unpack("v", substr($mem, $addr, 2));
}
sub s2o {
  my ($bank, $offs) = (int($_[0] / 0x10000), $_[0] % 0x10000);
  return $bank * 0x8000 + $offs - 0x8000;
}
sub unpack3 {
  my ($lo, $mid, $hi) = map { ord } split //, $_[0];
  return $lo + 0x100 * $mid + 0x10000 * $hi;
}
