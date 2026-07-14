from __future__ import annotations

from dataclasses import dataclass

_FTYP_MARKER = b"ftyp"
_MOOV_MARKER = b"moov"
_MOOF_MARKER = b"moof"
_MDAT_MARKER = b"mdat"


@dataclass(frozen=True)
class Fmp4Segment:
    kind: str
    data: bytes


class Fmp4SegmentParser:
    def __init__(self, max_buffer_bytes: int = 1024 * 1024):
        self._buf = bytearray()
        self._max_buffer_bytes = max_buffer_bytes
        self._seen_init = False
        self.last_init_segment: bytes | None = None

    def feed(self, chunk: bytes) -> list[Fmp4Segment]:
        if chunk:
            self._buf.extend(chunk)

        segments: list[Fmp4Segment] = []
        while len(self._buf) > 8:
            segment = self._extract_next_segment()
            if segment is None:
                self._trim_oversized_buffer()
                break
            segments.append(segment)
        return segments

    def _extract_next_segment(self) -> Fmp4Segment | None:
        if not self._seen_init:
            init_data = self._extract_box_pair(_FTYP_MARKER, _MOOV_MARKER)
            if init_data is None:
                return None
            self._seen_init = True
            self.last_init_segment = init_data
            return Fmp4Segment(kind="init", data=init_data)

        media_data = self._extract_box_pair(_MOOF_MARKER, _MDAT_MARKER)
        if media_data is None:
            return None
        return Fmp4Segment(kind="media", data=media_data)

    def _extract_box_pair(self, first_marker: bytes, second_marker: bytes) -> bytes | None:
        first_idx = self._buf.find(first_marker)
        if first_idx < 4:
            return None
        first_start = first_idx - 4

        second_idx = self._buf.find(second_marker, first_idx + 4)
        if second_idx < 4:
            return None
        second_size = int.from_bytes(self._buf[second_idx - 4:second_idx], "big")
        second_end = second_idx - 4 + second_size
        if second_size < 8 or second_end > len(self._buf):
            return None

        data = bytes(self._buf[first_start:second_end])
        del self._buf[:second_end]
        return data

    def _trim_oversized_buffer(self) -> None:
        if len(self._buf) <= self._max_buffer_bytes * 2:
            return
        markers = (_FTYP_MARKER, _MOOF_MARKER)
        positions = [self._buf.find(marker, 4) for marker in markers]
        positions = [pos - 4 for pos in positions if pos >= 4]
        if positions:
            del self._buf[: min(positions)]


__all__ = ["Fmp4Segment", "Fmp4SegmentParser"]
