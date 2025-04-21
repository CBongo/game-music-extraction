#!/usr/bin/perl

# ccmus.pl - extract music sequence from chrono cross( PSX) 
# cab 2017-05-20
# with help from zoopd

use Compress::Zlib;
use MIDI;
use Data::Dumper;

@notes = ("C ", "C#", "D ", "D#", "E ", "F ", "F#",
          "G ", "G#", "A ", "A#", "B ");

%optext = (0xA0 => 'Halt', 0xA1 => 'Program Change', 
		0xA2 => 'Utility Duration', 0xA3 => 'Set Track Vol', 
	   0xA5 => 'Set Octave', 0xA6 => 'Inc Octave', 0xA7 => 'Dec Octave',
	   0xA8 => 'Set Expression', 0xA9 => 'Expression Fade',
	   0xAA => 'Set Pan', 0xAB => 'Pan Fade',
	   0xAD => 'Set Attack', 0xAE => 'Set Decay',
	   0xAF => 'Set Sustain Level', 0xB0 => 'Set Decay and SusLvl',
	   0xB1 => 'Set Sustain Rate', 0xB2 => 'Set Release',
	   0xB3 => 'Reset ADSR',
	   0xB4 => 'Vibrato On', 0xB5 => 'Vibrato Depth', 0xB6 => 'Vibrato Off',
	   0xB7 => 'Set Attack Mode',
	   0xB8 => 'Tremolo On', 0xB9 => 'Tremolo Depth', 0xBA => 'Tremolo Off',
	   0xBB => 'Set Sustain Rate Mode',
	   0xBC => 'Ping-pong On', 0xBD => 'Ping-pong Depth', 0xBE => 'Ping-pong Off',
	   0xBF => 'Set Release Mode',
	   0xC0 => 'trance(?) absolute', 0xC1 => 'trance(?) relative',
	   0xC2 => 'Reverb On', 0xC3 => 'Reverb Off',
	   0xC4 => 'Noise On', 0xC5 => 'Noise Off',
	   0xC6 => 'FM On', 0xC7 => 'FM Off',
	   0xC8 => 'Begin Repeat', 0xC9 => 'End Repeat', 0xCA => 'Repeat Always',
	   0xCC => 'Slur on', 0xCD => 'Slur off',
	   0xD4 => 'Vibrato Active', 0xD5 => 'Vibrato Inactive',
	   0xD6 => 'Tremolo Active', 0xD7 => 'Tremolo Inactive',
	   0xD8 => 'Set Pitch Bend', 0xD9 => 'Add Pitch Bend',
	   0xDD => 'Vibrato Fade', 0xDE => 'Tremolo Fade',
	   0xDF => 'Ping-pong Fade',
	   0xFF => 'Halt');

%feoptext = (0x00 => 'Set Tempo', 0x01 => 'Tempo Fade',
	     0x02 => 'Set Reverb Vol', 0x03 => 'Reverb Vol Fade',
	     0x04 => 'Perc Mode On', 0x05 => 'Perc Mode Off',
	     0x06 => 'Goto Relative', 0x07 => 'Branch if vblk+6c >= op1',
	     0x08 => 'Goto if arg == rptcount',
	     0x09 => 'Goto if arg == rptcount w/pop',
	     0x0e => 'Call Sub', 0x0f => 'Return From Sub',
	     0x10 => 'Set NextAvailPV', 0x11 => 'Clear NextAvailPV',
		 0x12 => 'Track Vol Fade',
	     0x14 => 'Program Change', 0x15 => 'Time Signature',
		 0x16 => 'Measure #');

#@durtbl = &getpsx("v*", 0x8006b5bc, 0xb * 2);
#print OUT "Duration table: ", join(" ", map {sprintf "%02x", $_} @durtbl), "\n";
@durtbl = (0xC0, 0x60, 0x30, 0x18, 0x0C, 0x06,
		   0x03, 0x20, 0x10, 0x08, 0x04);

#@oplen  = &getpsx("C*", 0x8006b53c, 0x60);
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

#print OUT "oplen table: ", join(" ", @oplen), "\n";

#@opFElen = &getpsx("C*", 0x8006b59c, 0x20);
@opFElen = (3,4,3,4,1,1,0,0,
			0,0,2,2,0,0,0,0,
			2,1,3,1,2,3,3,0,
			0,4,1,1,2,1,1,0);   
$opFElen[0x06] = 3;
$opFElen[0x07] = 4;
$opFElen[0x0e] = 3;
$opFElen[0x0f] = 1;
#print OUT "oplen FE table: ", join(" ", @opFElen), "\n";

print OUT "\n";

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

require 'titles.pl';

@fnames = <akao/*.bin>;
if (@ARGV) {
  @fnames = grep { my $x = $_; grep { $x =~ /$_-[0-9a-f]+.bin/ } @ARGV } @fnames;
}

foreach $fname (sort @fnames) {
  print STDERR "$fname\n";
  $oname = $fname;
  $oname =~ s/bin$/txt/g;
  $oname =~ s/([0-9a-f]{2})-[0-9a-f]+\./$1 $titles[hex($1)]./;
  #$oname =~ s/([0-9a-f]{2})-[0-9a-f]+\./$1./;
  $oname =~ s/ \././g;
  $mname = $oname;
  $mname =~ s/txt$/mid/g;
  printf STDERR "%s\n", $textmode ? $oname : $mname;

  open IN, "< $fname" or warn, next;
  if ($textmode) {
    open OUT, "> $oname" or warn, next;
  }
  read IN, $buf, 0x20;

  undef %unhandled;
  undef %unhandledFE;

  my ($magic, $id, $len, @unk) = unpack "A4v2C24", $buf;
  read IN, $buf, $len;
  close IN;

  printf OUT "Magic:   %s\n", $magic;
  printf OUT "ID:      %02x\n", $id;
  printf OUT "Length:  %04x\n", $len;
  printf OUT "Unknown: %s\n", join(" ", map {sprintf "%02x", $_} @unk);

  print OUT "\n";

@patchmap = ();
@transpose = ();
@percmap = (50) x 12;

  $vmask = &getpsx("V", 0, 4);
  @vmask = split //, &getpsx("b32", 0, 4);
  $vcount = &getpsx("%b32", 0, 4);

  printf OUT "Voice mask:  %08x\n", $vmask;
  print  OUT "Voice mask bits: ", join(",",@vmask), "\n";
  printf OUT "Voice count: %d\n", $vcount;
  print OUT "\n";

  my ($patchdata, $percdata) = &getpsx("V2", 0x10, 8);
  printf OUT "Patch      data offset: %8x\n", $patchdata;
  printf OUT "Percussion data offset: %8x\n", $percdata;
  print OUT "\n";

  for ($v = 0, $vp = 0x20; $v < 32; $v++) {
    if ($vmask[$v]) {
      $vptroffs[$v] = &getpsx("v", $vp, 2);
      $vptrs[$v] = $vptroffs[$v] + $vp;
	  printf OUT "Voice %02x ", $v;
      printf OUT "\@ %04x (%04x)\n", $vptrs[$v], $vptroffs[$v];
      $vp += 2;
    } else {
      #print OUT "not used\n";
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
  for ($v = 0, $vp = 4; $v < 32; $v++, $vchan = ($vchan + ($vchan == 8 ? 2 : 1)) % 16) {
    next unless $vmask[$v];

    my $track = new MIDI::Track;
    
    my $totaltime = 0;
    my $master_rpt = 1;
    
    my $tempo = 255;
    my $balance = 0x80;
    my $velocity = 100;
	my $expression = 0;
    my $octave = 4;
    my $durmult = 0;
    my $perckey = 0;   # key to use for percussion

    my $rpt = 0;
    my (@rptcnt, @rptpos, @rptcur);
    my $e = []; # track events
    push @$e, ['track_name', 0, "Voice $v"];
    push @$e, ['control_change', 0, $vchan, 0, 0]; # bank 0
    
    printf OUT "Voice %02x:\n", $v;
	my $next_vptr = 0;
	for (my $n = $v + 1;  $n < @vptrs; $n++) {
		$next_vptr = $vptrs[$n];
		last if $next_vptr;
	}
	$next_vptr ||= $len;  # end of last voice = end of file

    for ($p = $vptrs[$v]; $p && $p < $next_vptr; ) {
		$cmd = &getpsx("C", $p++, 1);
		printf OUT "  %08x:  %02X ", $p-1, $cmd;
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
 
			if ($op1 == 0x00) {
			  # tempo
			  $tempo = ($op[1] << 8) + $op[0];
			  printf OUT " ~%d bpm", $tempo / 218;
			  push @{$cevents},
				  ['set_tempo', $totaltime, int($tempo_factor / $tempo)];	  
			} elsif ($op1 == 0x06) {
				# goto
				$p = 0;
				print OUT "\n"; 
				next;
			} elsif ($op1 == 0x15) {
				# time sig
				# work around bogus 0/0 time sigs
				my $d = $op[0] != 0 ? 0xC0/$op[0] : 0;
				printf OUT " (%d/%d)", $op[1], $d;
				# midi wants log base 2 of denominator
				# lazy mode: only care about a few values, make a map
				my (%denom) = (2 => 1, 4 => 2, 8 => 3, 16 => 4, 32 => 5);
				# numerator, denominator, metronome clicks, 32nds/qtr
				push @$cevents, ['time_signature', $totaltime,
					$op[1], $denom{$d}, 24, 8] if $d;
			} elsif ($op1 == 0x16) {
				# measure number
				printf OUT "%d", ($op[1] << 8) + $op[0];
			}
			print OUT "\n"; 
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
         substr($buf, $start, $len);
}

sub set_controller {
	my ($events, $etime, $channel, $controller, $value) = @_;
	push @$events, ['control_change', $etime, $channel, $controller, $value];
}

sub fade {
	my ($events, $starttime, $startval, $endval, $rate, $msg, @args) =@_;
	# parameter fade
	my ($tdelta, $cb);
	my $bdelta = $endval - $startval;
	my $stepinc = $bdelta / $rate;
	for ($tdelta = 1, $cb = $startval + $stepinc;
			$tdelta < $rate; $tdelta++, $cb += $stepinc) {
		#print $starttime + $tdelta, ",", 
		#       int($startval + $stepinc * $tdelta), "\n";
		#next unless $tdelta % 8 == 0;
		push @$events, [$msg, $starttime + $tdelta, 
							@args, int($cb)];
	}
}

sub offset {
	my ($p, $offset) = @_;
	my $signed_offset = unpack "s", pack "S", $offset;
	printf STDERR "signed offset %hu (%04x) became %hd\n", $offset, $offset, $signed_offset;
	my $new_p = $p + $signed_offset;
	printf STDERR "%04x + %04x = %04x\n", $p, $offset, $new_p;
	return $new_p;
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
