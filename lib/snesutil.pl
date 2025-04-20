# -*- perl -*-
#
# SNES ROM manipulation utilities
# chris bongaarts cab@umn.edu 23 March 2001
#

use IO::File;

sub openrom ($) {
  my $filename = shift;
  my (%rom, $lobuf, $hibuf, $info);

  $rom{size} = -s $filename;
  $rom{offset} = $rom{size} % 0x8000;
  $rom{fh} = new IO::File "< $filename";
  seek $rom{fh}, $rom{offset} + 0x7FC0, 0;
  read $rom{fh}, $lobuf, 32;
  seek $rom{fh}, $rom{offset} + 0xFFC0, 0;
  read $rom{fh}, $hibuf, 32;

  my (@loinfo) = unpack("A21C7v2", $lobuf);
  my (@hiinfo) = unpack("A21C7v2", $hibuf);

  if (($loinfo[9] ^ 0xFFFF) ==  $loinfo[8]) {
    $rom{lorom} = 1;
    $info = \@loinfo;
  } elsif (($hiinfo[9] ^ 0xFFFF) == $hiinfo[8]) {
    $rom{hirom} = 1;
    $info = \@hiinfo;
  } else {
    if ($loinfo[1] & 0xf == 0) {
      $rom{lorom} = 1;
      $info = \@loinfo;
    } elsif ($hiinfo[1] & 0xf == 1) {
      $rom{hirom} = 1;
      $info = \@hiinfo;
    } else {
      print STDERR "can't determine rom type; assuming lorom\n";
      $rom{lorom} = 1;
      $info = \@loinfo;
    }
  }
  @rom{title,chips,country,licensee,checksum,ichecksum}
     = @{$info}[0,2,5,6,9,8];
  $rom{version} = "1.$info->[7]";
  $rom{speed} = $info->[1] & 0xf0 >> 4;
  $rom{mode} = $info->[1] & 0xf;
  $rom{romsize} = 1 << ($info->[3] - 7);
  $rom{sramsize} = 1 << ($info->[4] + 3);

  #print STDERR "\nROM info:\n";
  #foreach (sort keys %rom) {
  #  print STDERR "$_: $rom{$_}\n";
  #}
  return \%rom;
}

sub read2ptrs {
  # read $count 2-byte little-endian pointers from offset $start
  my ($start, $count) = @_;
  my ($buf);
  seek ROM, $start, 0;
  read ROM, $buf, $count * 2;
  return unpack "v$count", $buf;
}

sub read3ptrs {
  # read $count 3-byte little-endian pointers from offset $start
  my ($start, $count) = @_;
  my ($buf);
  seek ROM, $start, 0;
  read ROM, $buf, $count * 3;
  return map { &ptr2sv($_) } unpack ("a3" x $count, $buf); 
}

sub s2o {
  # hirom
  my ($snes) = @_;
  $snes =~ s/^(..)\/?(....)$/$1$2/;   # trim bank separator if need be

  my ($bank, $offset) = map { hex } (unpack "A2A4", $snes);
  return ($bank & 0x3f) * 0x10000 + $offset + 0x200; 
}

sub o2s {
  my ($offs) = @_;

  my ($bank, $offset);
  $offs -= 0x200;  # trim smc header
  $bank = int($offs / 0x10000) | 0xc0;
  $offset = $offs & 0xFFFF;
  return sprintf "%02X/%04X", $bank, $offset;
}


1;
