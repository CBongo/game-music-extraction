#!/usr/bin/perl
#
# kikmidi - convert kikstart menu theme and bgm to MIDI format
# Christopher Bongaarts - 2023-04-28
#

use Data::Dumper;
use MIDI;

# menu music
open IN, "< kik1_1ad0.bin" or die;
read IN, $buf, 0x190 * 3;  # 400 bytes/voice
close IN;

@menu_bytes = unpack "C*", $buf;

# game music
open IN, "< kik1_4600.bin" or die;
read IN, $buf, 0xc0;  # 192 bytes, just 1 voice
close IN;

@bgm_bytes = unpack "C*", $buf;

my $velocity = 100;

my $opus = new MIDI::Opus ({'format' => 1, 'ticks' => 96});
my $ctrack = new MIDI::Track;
push @{$opus->tracks_r}, $ctrack;
my $cevents = [
	['time_signature', 0, 4, 2, 96, 8],   # 4/4 (2^2) time
	['key_signature', 0, 2, 0],  # 2 sharps - D major
        ['set_tempo', 0, 266667]   # 60 int/s / 8 int/8th note * 2 8th/qtr note
    ];

# shorthand array to access track events
my @e;
for ($v = 0; $v < 3; $v++) {
  @{$e[$v]} = [];
  push @{$e[$v]}, ['track_name', 0, "Voice $v"];
}

for ($v=0; $v < 3; $v++) {
  #print "=====  Menu - voice $v:\n";
  &process_voice($v, \@menu_bytes, $v * 0x190, 0x190);
  #print "\n";
}

$ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
for ($v = 0; $v < 3; $v++) {
  push @{$opus->tracks_r},
      new MIDI::Track ({'events_r'
                          => MIDI::Score::score_r_to_events_r($e[$v])});
}
#$opus->dump({dump_tracks => 1});
$opus->write_to_file(sprintf "kikmenu.mid");

## start over for bgm song
$opus = new MIDI::Opus ({'format' => 1, 'ticks' => 96});
$ctrack = new MIDI::Track;
push @{$opus->tracks_r}, $ctrack;
$cevents = [
        ['time_signature', 0, 4, 2, 96, 8],
        ['key_signature', 0, 1, 0],  # 1 sharp - G major
        ['set_tempo', 0, 266667]   # 60 int/s / 8 int/8th note * 2 8th/qtr note
    ];

@{$e[0]} = [];
push @{$e[0]}, ['track_name', 0, "Voice 0"];

#print "=====  BGM - voice 3:\n";
&process_voice(0, \@bgm_bytes, 0, 0xc0);

$ctrack->events_r(MIDI::Score::score_r_to_events_r($cevents));
push @{$opus->tracks_r},
      new MIDI::Track ({'events_r'
                          => MIDI::Score::score_r_to_events_r($e[0])});

#$opus->dump({dump_tracks => 1});
$opus->write_to_file(sprintf "kikbgm.mid");

exit;

# the end.

sub process_voice {
  my ($v, $b, $start, $len) = @_;

  my $dur = 48;   # all eighth notes
  my $totaltime = 0;
  for (my $i=$start; $i < $start + $len; $i++) {
    #printf "  %02x ", $$b[$i];
    if ($$b[$i] == 0) {
      #print "  Rest\n";
    } elsif ($$b[$i] == 1) {
      #print "  Tie\n";
#print "TIE before: ", Dumper($e[$v]),"\n";
      my ($last_ev) = grep {$_->[0] eq 'note'} reverse @{$e[$v]};
      # make prev note last right up till now
      #$last_ev->[2] = $totaltime - $last_ev->[1];
      $last_ev->[2] += $dur;  # already did notedur
#print "TIE  after: ", Dumper($e[$v]),"\n";

    } else {
      #print "  ", &notename($$b[$i]), "\n";
      push @{$e[$v]}, ['note', $totaltime, $dur, $v,
                       &map_note($$b[$i]), $velocity];
    }
    #print "\n" unless ($i+1) % 8;   # linefeed each measure
    $totaltime += $dur;
  }
}

sub map_note {
  my $n = shift;

  # concert A4 = notenum 0x23 = MIDI notenum 69 (0x45)
  return $n + 0x22;
}
  
