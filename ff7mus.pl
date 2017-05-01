#!/usr/bin/perl

# ff7mus 
# cab 2017-04-09
# some basis on ff9mus
# cab 5 Sep 2004
# with help from zoopd

use MIDI;
use Data::Dumper;

@titles = (
	"",
	"",
	"102 Opening - Bombing Mission",
	"905 Bombing Mission (Variation)",
	"209 Chasing the Black-Caped Man",
	"407 On the Other Side of the Mountain",
	"903 Fanfare (Variation)",
	"103 Mako Reactor",
	"110 Fighting",
	"312 Fiddle de Chocobo",
	"104 Anxious Heart",
	"120 Still More Fighting",
	"220 Cait Sith's Theme",
	"319 Aerith's Theme",
	"201 FF VII Main Theme",
	"",
	"",
	"211 Rufus' Welcoming Ceremony",
	"",
	"117 Who Are You",
	"202 Ahead on Our Way",
	"212 It's Difficult to Stand on Both Feet, Isn't It",
	"411 If You Open Your Heart",
	"206 Waltz de Chocobo",
	"118 Don of the Slums",
	"213 Trail of Blood",
	"301 Cosmo Canyon",
	"121 Red XIII's Theme",
	"303 Great Warrior",
	"108 Lurking in the Darkness",
	"109 Shinra Corporation",
	"119 Infiltrating Shinra Tower",
	"114 Underneath the Rotting Pizza",
	"205 Farm Boy",
	"204 On That Day, 5 Years Ago",
	"105 Tifa's Theme",
	"216 Costa del Sol",
	"115 Oppressed People",
	"112 Flowers Blooming in the Church",
	"122 Crazy Motorcycle",
	"221 Sandy Badlands",
	"305 Those Chosen by the Planet",
	"106 Barret's Theme",
	"218 Mining Town",
	"302 Life Stream",
	"207 Electric de Chocobo",
	"113 Turk's Theme",
	"111 Fanfare",
	"403 Highwind Takes to the Skies",
	"412 The Mako Cannon Fires! - Shinra Explodes",
	"316 Interrupted by Fireworks",
	"",
	"208 Cinco de Chocobo",
	"214 J-E-N-O-V-A",
	"304 Descendant of Shinobi",
	"107 Hurry!",
	"219 Gold Saucer",
	"405 Parochial Town",
	"203 Good Night, Until Tomorrow",
	"215 Continue",
	"313 A Great Success",
	"314 Tango of Tears",
	"311 Racing Chocobos",
	"315 Debut",
	"406 Off the Edge of Despair",
	"123 Holding My Thoughts In My Heart",
	"",
	"306 The Nightmare's Beginning",
	"116 Honeybee Manor",
	"317 Forest Temple",
	"217 Mark of the Traitor",
	"408 Hurry Faster!",
	"321 The Great Northern Cave",
	"307 Cid's Theme",
	"409 Sending a Dream Into the Universe",
	"318 You Can Hear the Cry of the Planet",
	"323 Who Am I",
	"309 Wutai",
	"320 Buried in the Snow",
	"310 Stolen Materia",
	"322 Reunion",
	"210 Fortress of the Condor",
	"",
	"401 Shinra Army Wages a Full-Scale Attack",
	"402 Weapon Raid",
	"414 Jenova Absolute",
	"404 A Secret, Sleeping in the Deep Sea",
	"413 Judgment Day",
	"415 The Birth of God",
	"",
	"410 The Countdown Begins",
	"308 Steal the Tiny Bronco!",
	"904 Those Chosen By the Planet (Variation)",
	"901 World Crisis (Clip)",
	"902 Who Am I (Alternate)",
	"",
	"101 The Prelude",
	"417 World Crisis",
	"",
	"418 Staff Roll",
);

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
	   0xE8 => 'Set Tempo', 0xE9 => 'Tempo Fade',
	   0xEA => 'Set Reverb Depth', 0xEB => 'Reverb Depth Fade',
	   0xEC => 'Percussion Mode On', 0xED => 'Percussion Mode Off',
	   0xEE => 'Goto',
	   0xF0 => 'Conditional Repeat',
	   0xF2 => 'Patch Change', 0xFC => 'Extended Patch Change',
	   0xFD => 'Set Time Signature', 0xFE => 'Measure #',
	   0xFF => 'Halt');

#@durtbl = &getpsx("v*", 0x80049c28, 0xb * 2);
#print OUT "Duration table: ", join(" ", map {sprintf "%02x", $_} @durtbl), "\n";
@durtbl = (0xC0, 0x60, 0x30, 0x18, 0x0C, 0x06,
		   0x03, 0x20, 0x10, 0x08, 0x04);

@patchmap = ();
@transpose = ();
@percmap = (50) x 12;

#@oplen  = &getpsx("C*", 0x80049948, 0x60);
@oplen = (0,2,2,2,3,2,1,1,
		2,3,2,3,2,2,2,2,
		3,2,2,1,4,2,1,2,
		4,2,1,2,3,2,1,2,
		2,2,1,1,1,1,1,1,
		1,0,0,0,1,0,2,2,
		1,0,2,2,1,1,1,1,
		2,2,2,0,2,3,3,3,
		0,0,0,0,0,0,0,0,
		3,4,3,4,3,1,0,0,
		0,0,2,1,3,1,2,3,
		2,1,0,0,0,3,3,0);

$oplen[0xA0-0xA0] = 1;  # override: halt
$oplen[0xC9-0xA0] = 2;  # override: end repeat
$oplen[0xCA-0xA0] = 1;  # override: repeat always
$oplen[0xCB-0xA0] = 1;
$oplen[0xCD-0xA0] = 1;  # override: slur off
$oplen[0xD1-0xA0] = 1;  # override: halt
$oplen[0xDB-0xA0] = 1;  # override: halt
$oplen[0xE0-0xA0] = 1;  # override: halt
$oplen[0xE1-0xA0] = 1;  # override: halt
$oplen[0xE2-0xA0] = 1;  # override: halt
$oplen[0xE3-0xA0] = 1;  # override: halt
$oplen[0xE4-0xA0] = 1;  # override: halt
$oplen[0xE5-0xA0] = 1;  # override: halt
$oplen[0xE6-0xA0] = 1;  # override: halt
$oplen[0xE7-0xA0] = 1;  # override: halt
$oplen[0xEE-0xA0] = 3;  # override: goto
$oplen[0xEF-0xA0] = 4;  # override: conditional goto
$oplen[0xF0-0xA0] = 4;  # override: conditional repeat
$oplen[0xF1-0xA0] = 4;  # override: conditional repeat
$oplen[0xFA-0xA0] = 1;  # override: halt
$oplen[0xFB-0xA0] = 1;  # override: halt
$oplen[0xFC-0xA0] = 1;  # override: extended patch change
$oplen[0xFF-0xA0] = 1;  # override: halt

#print OUT "oplen table: ", join(" ", @oplen), "\n";

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

@fnames = <akao/*.bin>;
if (@ARGV) {
  @fnames = grep { my $x = $_; grep { $x =~ /$_.bin/ } @ARGV } @fnames;
}

foreach $fname (sort @fnames) {
  print STDERR "$fname\n";
  $oname = $fname;
  $oname =~ s/bin$/txt/g;
  $oname =~ s/([0-9a-f]{2})\./$1 $titles[hex($1)]./;
  $oname =~ s/ \././g;
  $mname = $oname;
  $mname =~ s/txt$/mid/g;
  printf STDERR "%s\n", $textmode ? $oname : $mname;

  open IN, "< $fname" or warn, next;
  if ($textmode) {
    open OUT, "> $oname" or warn, next;
  }
  read IN, $buf, 0x10;

  undef %unhandled;

  my ($magic, $id, $len, @unk) = unpack "A4v2C8", $buf;
  read IN, $buf, $len;
  close IN;

  printf OUT "Magic:   %s\n", $magic;
  printf OUT "ID:      %02x\n", $id;
  printf OUT "Length:  %04x\n", $len;
  printf OUT "Unknown: %s\n", join(" ", map {sprintf "%02x", $_} @unk);


  print OUT "\n";

  $vmask = &getpsx("V", 0, 4);
  @vmask = split //, &getpsx("b32", 0, 4);
  $vcount = &getpsx("%b32", 0, 4);

  printf OUT "Voice mask:  %08x\n", $vmask;
  print  OUT "Voice mask bits: ", join(",",@vmask), "\n";
  printf OUT "Voice count: %d\n", $vcount;

  for ($v = 0, $vp = 0x4; $v < 32; $v++) {
    if ($vmask[$v]) {
      $vptroffs[$v] = &getpsx("v", $vp, 2);
      $vptrs[$v] = $vptroffs[$v] + $vp + 2;
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
	my $util_dur = 0;
	my $channel = $vchan;
	my $t = 0;

    my $rptidx = 0;
    my (@rptcnt, @rptpos, @rptcur);
    my $e = []; # track events
    push @$e, ['track_name', 0, "Voice $v"];
    push @$e, ['control_change', 0, $vchan, 0, 0]; # bank 0
    
    printf OUT "\nVoice %02x:\n", $v;
	my $next_vptr = 0;
	for (my $n = $v + 1;  $n < @vptrs; $n++) {
		$next_vptr = $vptrs[$n];
		last if $next_vptr;
	}
	$next_vptr ||= $len;  # end of last voice = end of file

    for ($p = $vptrs[$v]; $p && $p < $next_vptr; ) {
		$cmd = &getpsx("C", $p++, 1);
		printf OUT "  %04x:  %02X ", $p-1, $cmd;
		if ($cmd < 0xA0) {
			# note/tie/rest
			my ($notenum, $dur);
			$notenum = int($cmd / 0xb);
			if ($util_dur) {
				$dur = $util_dur;
				$util_dur = 0;
			} else {
				$dur = $durtbl[$cmd % 0xb];
			}
			print OUT "            ";
			if ($notenum < 12) {
				printf OUT "Note %s (%02d) ", $notes[$notenum], $notenum;
				my $mnote = 12 * ($octave + $t) + $notenum;
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
			printf OUT "Dur %02X", $dur;
			$totaltime += $dur;
		} else {
			# command
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
	
			if ($cmd == 0xA0) {
				# halt
				next;
			} elsif ($cmd == 0xA1 || $cmd == 0xF2) {
				# program change
				my $inst = $patchmap[$op[0]];
				push @$e, ['patch_change', $totaltime, $channel, $inst];
				$t = $transpose[$op[0]];
			} elsif ($cmd == 0xA2) {
				#util dur
				$util_dur = $op[0];
			} elsif ($cmd == 0xA3) {
				# track vol
				&set_controller($e, $totaltime, $channel, 7, $op[0]);
			} elsif ($cmd == 0xA5) {
				# set octave
				$octave = $op[0];
			} elsif ($cmd == 0xA6) {
				# inc octave
				$octave++;
			} elsif ($cmd == 0xA7) {
				# dec octave
				$octave--;
			} elsif ($cmd == 0xA8) {
				# set expression
				$expression = $op[0];
				&set_controller($e, $totaltime, $channel, 11, $expression);
			} elsif ($cmd == 0xA9) {
				# expression fade
				my $new_expr = $op[1];
				&fade($e, $totaltime, $expression, $new_expr,
					  $op[0], 'control_change', $channel, 11);
			} elsif ($cmd == 0xAA) {
				# set pan
				$balance = $op[0];
				&set_controller($e, $totaltime, $channel, 10, $balance);
			} elsif ($cmd == 0xAB) {
				my $new_bal = $op[1];
				&fade($e, $totaltime, $balance, $new_bal,
					   $op[0], 'control_change', $channel, 10);
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
			} elsif ($cmd == 0xE8) {
				# tempo
				$tempo = ($op[1] << 8) + $op[0];
				printf OUT " ~%d bpm", $tempo / 218;
				push @{$cevents},
					['set_tempo', $totaltime, int($tempo_factor / $tempo)];	
			} elsif ($cmd == 0xE9) {
				# tempo fade
				my $new_tempo = ($op[2] << 8) + $op[1];
				printf OUT " to ~%d bpm", $new_tempo / 218;
				&fade($cevents, $totaltime,
						int($tempo_factor / $tempo),
						int($tempo_factor / $new_tempo), $op1, 'set_tempo');
				$tempo = $new_tempo;
			} elsif ($cmd == 0xEC) {
				# percussion mode on
				# TODO: identify args for perc mode
				$perckey = $percmap[$op[1]];
				$channel = 9;
			} elsif ($cmd == 0xED) {
				# percussion mode off
				$perckey = 0;
				$channel = $vchan;
			} elsif ($cmd == 0xEE) {
				# goto
				#$p = &offset($p, ($op[1] << 8) + $op[0])
				#	unless $textmode;
			} elsif ($cmd == 0xF0 || $cmd == 0xF1) {
				# conditional goto
				if (!$textmode && $rptcur[$rpt] == $op[0]) {
					# goto
					$p = &offset($p, ($op[1] << 8) + $op[0]);
					#$rpt-- if $cmd == 0xF1;
				}

			} elsif ($cmd == 0xFD) {
				# time sig
				printf OUT " (%d/%d)", $op[1], 0xC0/$op[0];
				# midi wants log base 2 of denominator
				# lazy mode: only care about a few values, make a map
				my (%denom) = (2 => 1, 4 => 2, 8 => 3, 16 => 4, 32 => 5);
				# numerator, denominator, metronome clicks, 32nds/qtr
				push @$cevents, ['time_signature', $totaltime,
					$op[1], $denom{0xC0/$op[0]}, 24, 8];
			} elsif ($cmd == 0xFE) {
				# measure number
				printf OUT "%d", ($op[1] << 8) + $op[0];
			}
		}
		print OUT "\n";
	}
	$track->events_r(MIDI::Score::score_r_to_events_r($e));
    push @{$opus->tracks_r}, $track;
  }

  print OUT "Unhandled opcodes: ",
	join(" ", map {sprintf "%02X", $_} sort {$a <=> $b} keys %unhandled), "\n"
	if keys %unhandled;
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
