"""A small Standard MIDI File reader (no dependencies), enough for rhythm-game charts.

Reads the header (format, track count, ticks per quarter note) and every track's events with **absolute integer
ticks**: note on/off (a note on with velocity 0 is a note off), meta events (tempo, time signature, track name, text
types 0x01-0x0F, end of track), SysEx (kept as raw bytes) and the other channel messages (skipped but parsed, so
running status stays right). Running status is honoured for channel messages; a meta or SysEx event does not reset
it (some chart editors rely on that, see TheNathannator/GuitarGame_ChartFormats "Notable Specification Violations").

`note_spans(track)` pairs note ons and offs into (tick, length, note, velocity, channel) spans, first in first out per
note and channel. SMPTE time division is rejected: every chart format in use is ticks-per-quarter.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Iterator


class MidiError(ValueError):
    pass


@dataclass
class MidiEvent:
    tick: int
    kind: str              # note_on | note_off | tempo | time_signature | track_name | text | sysex | end | other
    channel: int = 0
    note: int = 0
    velocity: int = 0
    value: int = 0         # tempo: microseconds per quarter note
    numerator: int = 4     # time signature
    denominator: int = 4
    text: str = ""         # track name / text events (latin-1 decoded, so every byte survives)
    meta_type: int = 0     # meta events: the type byte (0x01 text, 0x05 lyric, ...)
    data: bytes = b""      # sysex payload (without the leading 0xF0), other meta payloads


@dataclass
class MidiTrack:
    events: list[MidiEvent] = field(default_factory=list)

    @property
    def name(self) -> str:
        for e in self.events:
            if e.kind == "track_name":
                return e.text
            if e.tick > 0:
                break
        return ""


@dataclass
class MidiFile:
    format: int
    resolution: int        # ticks per quarter note
    tracks: list[MidiTrack] = field(default_factory=list)

    def track(self, name: str) -> MidiTrack | None:
        for t in self.tracks:
            if t.name == name:
                return t
        return None

    def tempo_events(self) -> list[MidiEvent]:
        """Tempo events of the whole file (type 1 files keep them on the first track, but any track is accepted)."""
        return sorted((e for t in self.tracks for e in t.events if e.kind == "tempo"), key=lambda e: e.tick)

    def time_signatures(self) -> list[MidiEvent]:
        return sorted((e for t in self.tracks for e in t.events if e.kind == "time_signature"), key=lambda e: e.tick)


def _varlen(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("truncated variable-length number")
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos
    raise MidiError("variable-length number longer than 4 bytes")


_DATA_BYTES = {0x8: 2, 0x9: 2, 0xA: 2, 0xB: 2, 0xC: 1, 0xD: 1, 0xE: 2}


def parse_midi(data: bytes) -> MidiFile:
    if data[:4] != b"MThd":
        raise MidiError("not a MIDI file (no MThd header)")
    (hlen,) = struct.unpack(">I", data[4:8])
    fmt, ntracks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise MidiError("SMPTE time division is not supported")
    if division == 0:
        raise MidiError("time division is 0")
    mf = MidiFile(format=fmt, resolution=division)
    pos = 8 + hlen
    while pos + 8 <= len(data) and len(mf.tracks) < ntracks:
        cid = data[pos:pos + 4]
        (clen,) = struct.unpack(">I", data[pos + 4:pos + 8])
        body = data[pos + 8:pos + 8 + clen]
        pos += 8 + clen
        if cid != b"MTrk":
            continue        # unknown chunk: skip (the spec says so)
        mf.tracks.append(_parse_track(body))
    return mf


def _parse_track(body: bytes) -> MidiTrack:
    track = MidiTrack()
    pos, tick, status = 0, 0, 0
    n = len(body)
    while pos < n:
        delta, pos = _varlen(body, pos)
        tick += delta
        if pos >= n:
            break
        b = body[pos]
        if b == 0xFF:                                   # meta event
            if pos + 1 >= n:
                break
            mtype = body[pos + 1]
            length, pos = _varlen(body, pos + 2)
            payload = body[pos:pos + length]
            pos += length
            ev = _meta(tick, mtype, payload)
            track.events.append(ev)
            if ev.kind == "end":
                break
            continue
        if b in (0xF0, 0xF7):                           # sysex (or an escape packet)
            length, pos = _varlen(body, pos + 1)
            payload = body[pos:pos + length]
            pos += length
            track.events.append(MidiEvent(tick, "sysex", data=bytes(payload)))
            continue
        if b & 0x80:
            status = b
            pos += 1
        elif not status:
            raise MidiError(f"data byte 0x{b:02x} without a running status at tick {tick}")
        hi, ch = status >> 4, status & 0x0F
        size = _DATA_BYTES.get(hi)
        if size is None:                                # system common/realtime inside a file: skip the byte
            continue
        d = body[pos:pos + size]
        pos += size
        if len(d) < size:
            break
        if hi == 0x9 and d[1] > 0:
            track.events.append(MidiEvent(tick, "note_on", ch, d[0], d[1]))
        elif hi in (0x8, 0x9):
            track.events.append(MidiEvent(tick, "note_off", ch, d[0], d[1] if hi == 0x8 else 0))
        else:
            track.events.append(MidiEvent(tick, "other", ch, data=bytes([status]) + bytes(d)))
    return track


def _meta(tick: int, mtype: int, payload: bytes) -> MidiEvent:
    if mtype == 0x51 and len(payload) >= 3:
        return MidiEvent(tick, "tempo", value=int.from_bytes(payload[:3], "big"), meta_type=mtype)
    if mtype == 0x58 and len(payload) >= 2:
        return MidiEvent(tick, "time_signature", numerator=payload[0], denominator=2 ** payload[1], meta_type=mtype)
    if mtype == 0x03:
        return MidiEvent(tick, "track_name", text=payload.decode("latin-1"), meta_type=mtype)
    if 0x01 <= mtype <= 0x0F:
        return MidiEvent(tick, "text", text=payload.decode("latin-1"), meta_type=mtype)
    if mtype == 0x2F:
        return MidiEvent(tick, "end", meta_type=mtype)
    return MidiEvent(tick, "other", meta_type=mtype, data=bytes(payload))


@dataclass
class NoteSpan:
    tick: int
    length: int
    note: int
    velocity: int
    channel: int = 0


def note_spans(track: MidiTrack) -> list[NoteSpan]:
    """Pair note ons with their note offs (per note and channel, first in first out). A note on that is never closed
    gets length 0. A second note on for a sounding note closes the first one there (what sequencers do)."""
    open_: dict[tuple[int, int], list[MidiEvent]] = {}
    spans: list[NoteSpan] = []
    for e in track.events:
        if e.kind == "note_on":
            open_.setdefault((e.note, e.channel), []).append(e)
        elif e.kind == "note_off":
            stack = open_.get((e.note, e.channel))
            if stack:
                on = stack.pop(0)
                spans.append(NoteSpan(on.tick, e.tick - on.tick, on.note, on.velocity, on.channel))
    for stack in open_.values():
        for on in stack:
            spans.append(NoteSpan(on.tick, 0, on.note, on.velocity, on.channel))
    spans.sort(key=lambda s: (s.tick, s.note))
    return spans


def text_events(track: MidiTrack) -> Iterator[MidiEvent]:
    return (e for e in track.events if e.kind == "text")
