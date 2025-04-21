#!/usr/bin/perl

# ff9mus 
# cab 5 Sep 2004
# with help from zoopd

use Compress::Zlib;
use MIDI;
use Data::Dumper;

@notes = ("C ", "C#", "D ", "D#", "E ", "F ", "F#",
          "G ", "G#", "A ", "A#", "B ");

%optext = (0xA0 => 'Halt', 0xA1 => 'Program Change', 0xA3 => 'Set Track Vol', 
	   0xA5 => 'Set Octave', 0xA6 => 'Inc Octave', 0xA7 => 'Dec Octave',
	   0xA8 => 'Set Velocity', 0xA9 => 'Velocity Fade',
	   0xAA => 'Set Pan', 0xAB => 'Pan Fade',
	   0xAD => 'Set Attack', 0xAE => 'Set Decay',
	   0xAF => 'Set Sustain Level', 0xB0 => 'Set Decay and SusLvl',
	   0xB1 => 'Set Sustain Rate', 0xB2 => 'Set Release',
	   0xB3 => 'Reset ADSR', 0xB6 => 'Vibrato Off',
	   0xB7 => 'Set Attack Mode', 0xBA => 'Tremolo Off',
	   0xBB => 'Set Sustain Rate Mode', 0xBE => 'Ping-pong Off',
	   0xBF => 'Set Release Mode',
	   0xC2 => 'Reverb On', 0xC3 => 'Reverb Off',
	   0xC4 => 'Noise On', 0xC5 => 'Noise Off',
	   0xC6 => 'FM On', 0xC7 => 'FM Off',
	   0xC8 => 'Begin Repeat', 0xC9 => 'End Repeat',
	   0xCA => 'Repeat Always', 0xCD => 'Nop',
	   0xD4 => 'Vibrato Active', 0xD5 => 'Vibrato Inactive',
	   0xD6 => 'Tremolo Active', 0xD7 => 'Tremolo Inactive',
	   0xFF => 'Halt');

%feoptext = (0x00 => 'Set Tempo', 0x01 => 'Tempo Fade',
	     0x02 => 'Set Reverb Vol', 0x03 => 'Reverb Vol Fade',
	     0x04 => 'Perc Mode On',
	     0x06 => 'Goto', 0x07 => 'Branch if vblk+6c >= op1',
	     0x08 => 'Goto if arg == rptcount',
	     0x09 => 'Goto if arg == rptcount w/pop',
	     0x0e => 'Call Sub', 0x0f => 'Return From Sub',
	     0x10 => 'Set NextAvailPV', 0x11 => 'Clear NextAvailPV',
	     0x14 => 'Program Change');

$tempo_factor = 13_107_200_000;  # timer2 period .2362us * 96 ticks * 65536

$textmode = 0;

while ($ARGV[0] =~ /^-(.*)/) {
  if ($1 eq 't') {
    # text mode
    $textmode = 1;
  } else {
    die "Unknown argument $1\n";
  }
  shift;
}

@fnames = <psf/*.psf>;
if (@ARGV) {
  @fnames = grep { my $x = $_; grep { $x =~ /FF9 $_/ } @ARGV } @fnames;
}

foreach $fname (sort @fnames) {
  print STDERR "$fname\n";
  $oname = $fname;
  $oname =~ s/psf/txt/g;
  $mname = $fname;
  $mname =~ s/psf/mid/g;

  open IN, "< $fname" or warn, next;
  if ($textmode) {
    open OUT, "> $oname" or warn, next;
  }
  read IN, $buf, 0x10;

  undef %unhandled;
  undef %unhandledFE;

  ($sig, $version, $ressize, $exesize, $crc) = unpack "A3CV3", $buf;

  unless ($sig eq 'PSF') {
    print OUT "Not a PSX file";
    next;
  }

  printf OUT "File type:     %s%d\n", $sig, $version;
  printf OUT "Reserved size: %08x\n", $ressize;
  printf OUT "EXE size:      %08x\n", $exesize;
  printf OUT "CRC:           %08x\n", $crc;

  read IN, $rbuf, $ressize if $ressize > 0;
  read IN, $ebuf, $exesize if $exesize > 0;

  $actualcrc = crc32($ebuf);

  printf OUT "Actual CRC:    %08x (%s)\n", $actualcrc,
              ($actualcrc == $crc ? 'OK' : 'BAD');

  # read tags (optional)
  undef %tags;
  read IN, $buf, 5;
  if ($buf eq '[TAG]') {
    while (<IN>) {
      chomp; next unless $_;
      s/\s*$//;  # remove trailing whitespace
      my ($tag, $value) = /^\s*([^=]+)\s*=\s*(.*)$/;
      $tags{$tag} .= $value;
    }
    print OUT "Tags:\n";
    foreach $tag (sort keys %tags) {
      print OUT "  $tag=$tags{$tag}\n";
    }
  }
  close IN;

  $buf = uncompress($ebuf);

  unless ($buf =~ /^PS-X EXE/) {
    print OUT "Not a PSX EXE\n";
  }

  ($pc, $tstart, $tlen) = unpack "x16Vx4V2", $buf;
  $sp = unpack "V", substr($buf, 0x30);
  $copyright = unpack "A*", substr($buf, 0x4c, 0x800-0x4c);

  printf OUT "Text start:  %08x\n", $tstart;
  printf OUT "Text end:    %08x\n", $tstart + $tlen - 1;
  printf OUT "Text len:    %08x\n", $tlen;
  printf OUT "Initial PC:  %08x\n", $pc;
  printf OUT "Initial SP:  %08x\n", $sp;
  print OUT "Copyright notice:\n$copyright\n\n";

  $buf = substr $buf, 0x800;

  @durtbl = &getpsx("v*", 0x8006f3f4, 0xb * 2);
  print OUT "Duration table: ", join(" ", map {sprintf "%02x", $_} @durtbl), "\n";

  #@oplen  = &getpsx("C*", 0x8006f374, 0x60);
  @oplen = (0,2,2,2,3,2,1,1,
	2,3,2,3,2,2,2,2,
	3,2,2,1,4,2,1,2,
	4,2,1,2,3,2,1,2,
	2,2,1,1,1,1,1,1,
	1,0,0,0,1,0,2,2,
	1,0,2,2,1,1,1,1,
	2,2,2,0,2,3,3,3,
	1,2,1,0,3,3,3,0);

  $oplen[0xA0-0xA0] = 1;  # override: halt
  $oplen[0xC9-0xA0] = 2;  # override: end repeat
  $oplen[0xCA-0xA0] = 1;  # override: repeat always
  $oplen[0xCB-0xA0] = 1;
  $oplen[0xCD-0xA0] = 1;  # override: nop
  $oplen[0xE3-0xA0] = 1;  # override: halt
  $oplen[0xE3-0xA0] = 1;  # override: halt
  $oplen[0xE7-0xA0] = 1;  # override: halt
  $oplen[0xE8-0xA0] = 1;  # override: halt
  $oplen[0xE9-0xA0] = 1;  # override: halt
  $oplen[0xEA-0xA0] = 1;  # override: halt
  $oplen[0xEB-0xA0] = 1;  # override: halt
  $oplen[0xEC-0xA0] = 1;  # override: halt
  $oplen[0xED-0xA0] = 1;  # override: halt
  $oplen[0xEE-0xA0] = 1;  # override: halt
  $oplen[0xEF-0xA0] = 1;  # override: halt
  $oplen[0xFF-0xA0] = 1;  # override: halt
    
  print OUT "oplen table: ", join(" ", @oplen), "\n";

  #@opFElen = &getpsx("C*", 0x8006f3d4, 0x20);
  @opFElen = (3,4,3,4,1,1,0,0,
	0,0,2,5,0,0,0,0,
	2,1,3,0,2,3,3,0,
	0,4,1,1,2,1,1,0);   
 
  $opFElen[0x06] = 3;
  $opFElen[0x07] = 4;
  $opFElen[0x0e] = 3;
  $opFElen[0x0f] = 1;
  print OUT "oplen FE table: ", join(" ", @opFElen), "\n";

  print OUT "\n";

  $songbase = 0x80100000;

  $vmask = &getpsx("V", $songbase + 0x20, 4);
  @vmask = split //, &getpsx("b32", $songbase + 0x20, 4);
  $vcount = &getpsx("%b32", $songbase + 0x20, 4);

  printf OUT "Voice mask:  %08x\n", $vmask;
  print  OUT "Voice mask bits: ", join(",",@vmask), "\n";
  printf OUT "Voice count: %d\n", $vcount;

  for ($v = 0, $vp = $songbase + 0x40; $v < 32; $v++) {
    printf OUT "Voice %02x ", $v;
    if ($vmask[$v]) {
      $vptroffs[$v] = &getpsx("v", $vp, 2);
      $vptrs[$v] = $vptroffs[$v] + $vp;
      printf OUT "\@ %08x (%04x)\n", $vptrs[$v], $vptroffs[$v];
      $vp += 2;
    } else {
      print OUT "not used\n";
    }
  }

  print OUT "\n";

  my $opus = new MIDI::Opus {'format' => 1, 'ticks' => 48};
  my $ctrack = new MIDI::Track;
  push @{$opus->tracks_r}, $ctrack;
  my $cevents = [];

  my $maxtime = 0;
  my $maxvoice = -1;
  my $vchan = 0;

VOICE:
  for ($v = 0, $vp = $songbase + 0x40; $v < 32; $v++, $vchan = ($vchan + ($vchan == 8 ? 2 : 1)) % 16) {
    next unless $vmask[$v];

    my $track = new MIDI::Track;
    
    my $totaltime = 0;
    my $master_rpt = 1;
    
    my $tempo = 255;
    my $balance = 0x80;
    my $velocity = 64;
    my $octave = 4;
    my $durmult = 0;
    my $perckey = 0;   # key to use for percussion

    my $rptidx = 0;
    my (@rptcnt, @rptpos, @rptcur);
    my $e = []; # track events
    push @$e, ['track_name', 0, "Voice $v"];
    push @$e, ['control_change', 0, $vchan, 0, 0]; # bank 0
    
    printf OUT "Voice %02x:\n", $v;
    for ($p = $vptrs[$v]; $p && $vptrs[$v + 1] && $p < $vptrs[$v+1]; ) {
      $cmd = &getpsx("C", $p++, 1);
      printf OUT "  %08x:  %02X ", $p, $cmd;
      if ($cmd < 0xA0 || ($cmd >= 0xF0 && $cmd <= 0xFD)) {
	# note/tie/rest
	my ($notenum, $dur);
        if ($cmd < 0xA0) {
	  $notenum = int($cmd / 0xb);
	  $dur = $durtbl[$cmd % 0xb];
	  print OUT "            ";
	} else {
          my ($op1) = &getpsx("C", $p++, 1);
	  $notenum = $cmd - 0xF0;
	  $dur = $op1;
	  printf OUT "%02X          ", $op1;
	}
        if ($notenum < 12) {
	  printf OUT "Note %s (%02d) ", $notes[$notenum], $notenum;
	  my $mnote = 12 * ($octave + $t) + $notenum;
	  my $channel = $vchan;
	  if ($perckey) {
	    # substitute percussion key on chan 10
	    $channel = 9;  # 10 zero-based
	    $mnote = $perckey;
	  }
	  push @$e, ['note', $totaltime,
                     $dur - 2,
                     $channel, $mnote, $velocity];
	  
        } elsif ($notenum == 12) {
	  print OUT "Tie          ";
	  my ($last_ev) = grep {$_->[0] eq 'note'}
				 reverse @$e;
	  $last_ev->[2] += $dur;
	} else {
	  print OUT "Rest         ";
        }
        printf OUT "Dur %02X\n", $dur;
        $totaltime += $dur;
      } elsif ($cmd == 0xFE) {
	my $op1 = &getpsx("C", $p++, 1);
        printf OUT "%02X ", $op1;
        my ($op, @op);
	my $oplen = $opFElen[$op1];
        if ($oplen > 0) {
	  if ($oplen > 1) {
	    @op = &getpsx("C*", $p, $oplen - 1);
	    $p += $oplen - 1;
	  }
	  print OUT join(" ", (map { sprintf "%02X", $_ } @op), "   " x (3 - @op));
	  print OUT $feoptext{$op1};
	} else {
	  #die sprintf "unhandled opcode FE %02X", $op1;
	  $unhandledFE{$op1} = 1;
	}
	print OUT "\n";  
	if ($op1 == 0x00) {
	  # tempo
	  $tempo = ($op[1] << 8) + $op[0];
	  push @{$cevents},
	      ['set_tempo', $totaltime, int($tempo_factor / $tempo)];	  
	} elsif ($op1 == 0x06) {
	  # goto
	  $p = 0;
	  next;
	}
      } else {
	my @op;
        my $oplen = $oplen[$cmd - 0xA0];
        if ($oplen > 0) {
	  if ($oplen > 1) {
	    @op = &getpsx("C*", $p, $oplen - 1);
	    $p += $oplen - 1;
	  }
	  print OUT join(" ", (map { sprintf "%02X", $_ } @op), "   " x (4 - @op));
	  print OUT $optext{$cmd};
        } else {
	  #die sprintf "unhandled opcode %02X", $cmd;
	  $unhandled{$cmd} = 1;
        } 
	print OUT "\n";
	if ($cmd == 0xA5) {
	  # set octave
	  $octave = $op[0];
	} elsif ($cmd == 0xA6) {
	  # inc octave
	  $octave++;
	} elsif ($cmd == 0xA7) {
	  # dec octave
	  $octave--;
	} elsif ($cmd == 0xA8) {
	  # set velocity
	  $velocity = $op[0];
	} elsif ($cmd == 0xAA) {
	  # set pan
	  $balance = $op[0];
	} elsif ($cmd == 0xC8) {
	  # begin repeat
	  $rptcur[++$rpt] = 0;
	  $rptpos[$rpt] = $p;
	} elsif ($cmd == 0xC9) {
	  # end repeat
	  if ($textmode || ++$rptcur[$rpt] == $op[0]) {
	    # all done
	    $rpt--;
	  } else {
	    # do again
	    $p = $rptpos[$rpt];
	    next;
	  }
	} elsif ($cmd == 0xCA) {
	  # repeat always
	}
      }
    }
    $track->events_r(MIDI::Score::score_r_to_events_r($e));
    push @{$opus->tracks_r}, $track;
  }

  print OUT "Unhandled opcodes: ",
	join(" ", map {sprintf "%02X", $_} sort {$a <=> $b} keys %unhandled), "\n"
	if keys %unhandled;
  print OUT "Unhandled FE opcodes: ",
	join("  ", map {sprintf "FE %02X", $_} sort {$a <=> $b} keys %unhandledFE), "\n"
	if keys %unhandledFE;
  close OUT;
  $ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
  #$opus->dump({dump_tracks => 1});
  $opus->write_to_file($mname) unless $textmode;
}

sub getpsx {
  my ($format, $start, $len) = @_;

  return unpack $format,
         substr($buf, $start - $tstart, $len);
}

sub hexdump {
  my ($addr, $data, $line) = @_;
  $line ||= 16;

  for (my $p = 0; $p < length $data; $p += $line) {
    printf OUT "    %08x: ", $addr + $p;
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
