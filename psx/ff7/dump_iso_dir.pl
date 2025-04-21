#!/usr/bin/perl


use Fcntl qw(SEEK_SET);

$sector_size = 2352;

my ($filename, $start, $dsize) = @ARGV;

die "usage: $0 <filename> <dirsector> [dsize]\n" unless $filename && defined $start;

open IN, "< $filename" or die "open $filename failed: $!\n";
seek IN, hex($start) * $sector_size + 24, SEEK_SET or die "seek failed: $!\n";

#$nr = read IN, $buf, $sector_size;
#warn "read less than sector size ($nr)" unless $nr == $sector_size;
#printf STDERR "MSF %02x:%02x:%02x  mode %d\n", 
#	unpack "C4", substr($buf, 12, 4);
#$buf = substr($buf, 24, 2048);

$dsize = hex($dsize) || 2048;
#print "dsize=$dsize\n";
while ($dsize > 0) {
#print "sector start pos=", tell(IN), "\n";
  for ($i = 2048; $i > 0; $i -= $dentlen) {
    read IN, $buf, 1;
    $dentlen = unpack "C", $buf;
    $i--, last if $dentlen == 0;
    read IN, $buf, $dentlen - 1;

    ($earlen, $extL, $extB, $lenL, $lenB, $dname)
       = unpack "CVNVNx14C/a", $buf;
    #print "Got elen=$earlen extL=$extL extB=$extB lenL=$lenL lenB=$lenB dname.len=", length($dname), "\n";
    $dname = "." if $dname eq "\0";
    $dname = ".." if $dname eq "\001";
    printf "%5d %-16s %8x %8x\n", ++$index, $dname, $extL, $lenL;
  }
#print "left: $i bytes\n";
  read IN, $buf, $i+280+24; # skip sector trailers, headers
  $dsize -= 2048;
#print "new dsize=$dsize\n";
}

close IN;
