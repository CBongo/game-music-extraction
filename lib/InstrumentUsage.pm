package InstrumentUsage;
use strict;
use warnings;
use JSON::PP;
use Digest::SHA qw(sha256_hex);

# The decoder supplies source facts before curated GM patch/octave substitutions.
# Unknown acoustic pitch and notes preceding any known patch are omitted.
sub new {
    my ($class, %args) = @_;
    die 'source is required' unless ref($args{source}) eq 'HASH';
    my $timebase = $args{timebase} // 96;
    die 'timebase must be positive' unless $timebase > 0;
    return bless { source => $args{source}, timebase => $timebase, tracks => {} }, $class;
}
sub _key { JSON::PP->new->canonical->encode([@_]) }
# Converter timing remains distinct from source/hardware timing and effect fidelity.
sub set_sequence_render_context {
    my ($self, %a) = @_;
    die 'render context is required' unless ref($a{context}) eq 'HASH';
    $self->{sequence_render_contexts}{_key($a{sequence_id})} = _json_copy({
        sequence_id => $a{sequence_id}, %{$a{context}},
    });
}
sub begin_track {
    my ($self, %a) = @_;
    $self->{tracks}{_key($a{sequence_id}, $a{track_id})} = {
        sequence_id => $a{sequence_id}, track_id => $a{track_id}, notes => [], effects => [],
        pending_effects => [], effect_context => {}, effect_context_order => [],
    };
}
sub _track {
    my ($self, $a) = @_;
    return $self->{tracks}{_key($a->{sequence_id}, $a->{track_id})}
        || die 'begin_track must precede observations';
}
sub select_patch {
    my ($self, %a) = @_;
    die 'source_instrument_id is required' unless defined $a{source_instrument_id};
    my %id = map { exists $a{$_} ? ($_ => $a{$_}) : () }
        qw(source_instrument_id source_patch_command song_local_slot sample_ids);
    my $track = $self->_track(\%a);
    $track->{identity} = \%id;
    $track->{patch_provenance} = $self->_provenance($a{offset});
    # Preserve pre-patch effect occurrences and attribute them once to the first patch.
    if (@{$track->{pending_effects}}) {
        for my $fx (@{$track->{pending_effects}}) {
            my $copy = _json_copy($fx);
            $copy->{provenance}{parameters}{observed_before_patch_selection} = JSON::PP::true;
            push @{$track->{effects}}, { identity => $track->{identity}, effect => $copy };
        }
        $track->{pending_effects} = [];
    }
}
sub clear_patch {
    my ($self, %a) = @_;
    my $track = $self->_track(\%a);
    delete $track->{identity};
    delete $track->{patch_provenance};
    delete $track->{last_note};
}
sub _provenance {
    my ($self, $offset) = @_;
    my $p = { method => 'inline decoder observation before curated GM mapping' };
    my $files = $self->{source}{source_files};
    if ($files && @$files) {
        $p->{location} = { path => $files->[0]{path} };
        $p->{location}{offset} = $offset if defined $offset;
    }
    return $p;
}
sub note {
    my ($self, %a) = @_;
    my $track = $self->_track(\%a);
    return unless $track->{identity};
    my %n = map { exists $a{$_} ? ($_ => $a{$_}) : () }
        qw(raw_sequence_note sounding_midi_note octave_state global_transpose_semitones
           track_transpose_semitones ramp_transpose_semitones sample_tuning_semitones
           pitch_context_complete event_index duration);
    $n{pitch_context_complete} = $n{pitch_context_complete}
        ? JSON::PP::true : JSON::PP::false if exists $n{pitch_context_complete};
    die 'raw_sequence_note is required' unless defined $n{raw_sequence_note};
    $n{track_id} = $a{track_id};
    $n{time} = { value => $a{time}, unit => 'midi_tick', timebase => $self->{timebase} } if defined $a{time};
    $n{duration_unit} = 'midi_tick' if exists $n{duration};
    $n{provenance} = $self->_provenance($a{offset});
    $n{provenance}{parameters} = { patch_selection => $track->{patch_provenance} };
    if (exists $a{render_gate_duration} || exists $a{render_velocity}) {
        $n{provenance}{parameters}{render_context} = {
            basis => $a{render_context_basis} // 'decoder_conversion',
            effects_complete => JSON::PP::false,
            map { exists $a{$_->[0]} ? ($_->[1] => $a{$_->[0]}) : () }
                ([render_gate_duration => 'gate_duration_ticks'],
                 [render_velocity => 'velocity_0_127']),
        };
    }
    if (@{$track->{effect_context_order}}) {
        $n{provenance}{parameters}{effect_context} = _json_copy([
            map { $track->{effect_context}{$_} } @{$track->{effect_context_order}}
        ]);
    }
    my $entry = { identity => $track->{identity}, occurrence => \%n };
    push @{$track->{notes}}, $entry;
    $track->{last_note} = $entry;
    $track->{pitch_context_changed} = 0;
}
sub tie {
    my ($self, %a) = @_;
    my $track = $self->_track(\%a);
    if ($track->{last_note}) {
        $track->{last_note}{occurrence}{duration} += $a{duration};
        my $render = $track->{last_note}{occurrence}{provenance}{parameters}{render_context};
        $render->{gate_duration_ticks} += $a{duration}
            if $render && exists $render->{gate_duration_ticks};
        # A tie can extend an earlier onset across intervening pitch controls.
        $track->{last_note}{occurrence}{pitch_context_complete} = JSON::PP::false
            if $track->{pitch_context_changed};
    }
}
sub effect {
    my ($self, %a) = @_;
    my $track = $self->_track(\%a);
    my %fx = map { exists $a{$_} ? ($_ => $a{$_}) : () }
        qw(sequence_id track_id raw_opcode raw_operands semantic normalized event_index);
    $fx{time} = { value => $a{time}, unit => 'midi_tick', timebase => $self->{timebase} } if defined $a{time};
    $fx{provenance} = $self->_provenance($a{offset});
    if (($a{semantic} // '') =~ /^(vibrato|pitch_bend|portamento|transpose|detune)$/
        || ($a{context_key} // '') =~ /^(noise|pitchmod|sound_effect)$/) {
        $track->{pitch_context_changed} = 1;
    }
    my $key = exists $a{context_key} ? _key($a{context_key}) : _key($a{raw_opcode});
    if (!exists $track->{effect_context}{$key}) {
        push @{$track->{effect_context_order}}, $key;
    }
    $track->{effect_context}{$key} = _json_copy(\%fx);
    if ($track->{identity}) {
        push @{$track->{effects}}, { identity => $track->{identity}, effect => \%fx };
    } else {
        push @{$track->{pending_effects}}, \%fx;
    }
}
sub _json_copy {
    my ($value) = @_;
    return JSON::PP->new->decode(JSON::PP->new->canonical->encode($value));
}
sub records {
    my ($self) = @_;
    my %records;
    for my $tk (sort keys %{$self->{tracks}}) {
        my $track = $self->{tracks}{$tk};
        for my $entry (@{$track->{notes}}, @{$track->{effects}}) {
            my $id = $entry->{identity};
            my $r = $records{_key($id)} ||= {
                record_id => 'usage:' . sha256_hex(_key($self->{source}, $id)), source => $self->{source}, identity => $id,
                observation => { usage => { provenance => $self->_provenance(undef), sequences => [] }, effects => [] },
                _sequences => {},
            };
            my $seq = $r->{_sequences}{_key($track->{sequence_id})} ||= {
                sequence_id => $track->{sequence_id}, track_ids => [], source_patch_commands => [],
                raw_sequence_notes => [], note_histogram => {}, note_occurrences => [],
            };
            push @{$seq->{track_ids}}, $track->{track_id}
                unless grep { _key($_) eq _key($track->{track_id}) } @{$seq->{track_ids}};
            if (defined $id->{source_patch_command}) {
                my $cmd = $id->{source_patch_command};
                push @{$seq->{source_patch_commands}}, $cmd
                    unless grep { _key($_) eq _key($cmd) } @{$seq->{source_patch_commands}};
            }
            if ($entry->{effect}) {
                push @{$r->{observation}{effects}}, $entry->{effect};
                next;
            }
            my $n = $entry->{occurrence};
            push @{$seq->{note_occurrences}}, $n;
            my $raw = $n->{raw_sequence_note};
            $seq->{note_histogram}{$raw}++;
            push @{$seq->{raw_sequence_notes}}, $raw unless grep { $_ == $raw } @{$seq->{raw_sequence_notes}};
            if (defined $n->{sounding_midi_note} && int($n->{sounding_midi_note}) == $n->{sounding_midi_note}) {
                my $snd = $n->{sounding_midi_note};
                $seq->{sounding_midi_notes} ||= [];
                push @{$seq->{sounding_midi_notes}}, $snd unless grep { $_ == $snd } @{$seq->{sounding_midi_notes}};
            }
        }
    }
    my @out;
    for my $key (sort keys %records) {
        my $r = $records{$key};
        my $seqs = delete $r->{_sequences};
        my $usage = $r->{observation}{usage};
        $usage->{sequences} = [map { $seqs->{$_} } sort keys %$seqs];
        $usage->{sequence_count} = scalar @{$usage->{sequences}};
        my @render_contexts = map {
            my $ctx = $self->{sequence_render_contexts}{_key($_->{sequence_id})};
            $ctx ? _json_copy($ctx) : ()
        } @{$usage->{sequences}};
        $usage->{provenance}{parameters}{sequence_render_contexts} = \@render_contexts
            if @render_contexts;
        my (@raw, @sounding);
        for my $seq (@{$usage->{sequences}}) {
            @{$seq->{raw_sequence_notes}} = sort { $a <=> $b } @{$seq->{raw_sequence_notes}};
            push @raw, @{$seq->{raw_sequence_notes}};
            if ($seq->{sounding_midi_notes}) {
                @{$seq->{sounding_midi_notes}} = sort { $a <=> $b } @{$seq->{sounding_midi_notes}};
                push @sounding, @{$seq->{sounding_midi_notes}};
            }
        }
        @raw = sort { $a <=> $b } @raw;
        @sounding = sort { $a <=> $b } @sounding;
        $usage->{overall_raw_note_range} = { minimum => $raw[0], maximum => $raw[-1] } if @raw;
        $usage->{overall_sounding_note_range} = { minimum => $sounding[0], maximum => $sounding[-1] } if @sounding;
        push @out, $r;
    }
    return \@out;
}
sub write_records {
    my ($class, $path, $records) = @_;
    open my $out, '>:raw', $path or die "Cannot write $path: $!";
    print {$out} JSON::PP->new->utf8->canonical->pretty->encode({
        schema_version => 'instrument-analysis/v1',
        generator => { name => 'InstrumentUsage Perl decoder sidecar' }, records => $records,
    }) or die "Cannot write $path: $!";
    close $out or die "Cannot close $path: $!";
}
sub write_json {
    my ($self, $path) = @_;
    __PACKAGE__->write_records($path, $self->records);
}
1;
