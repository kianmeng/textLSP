import re
import bisect

from typing import Optional, Any
from dataclasses import dataclass

from lsprotocol.types import Position, Range


TEXT_PASSAGE_PATTERN = re.compile('[.?!] |\\n')


class ConfigurationError(Exception):
    pass


@dataclass
class Interval():
    start: int
    length: int

    def __eq__(self, o: object):
        if not isinstance(o, Interval):
            return NotImplemented
        return self.start == o.start and self.length == o.length

    def __hash__(self):
        return hash((self.start, self.length))

    def __gt__(self, o: object):
        if not isinstance(o, Interval):
            return NotImplemented
        return self.start > o.start


@dataclass
class OffsetPositionInterval():
    offset_interval: Interval
    position_range: Range
    value: Optional[Any] = None


class OffsetPositionIntervalList():

    def __init__(self):
        self._offset_start = list()
        self._offset_end = list()
        self._position_start_line = list()
        self._position_start_character = list()
        self._position_end_line = list()
        self._position_end_character = list()
        self._value = list()

    def add_interval_values(
        self,
        offset_start: int,
        offset_end: int,
        position_start_line: int,
        position_start_character: int,
        position_end_line: int,
        position_end_character: int,
        value: Any
    ):
        self._offset_start.append(offset_start)
        self._offset_end.append(offset_end)
        self._position_start_line.append(position_start_line)
        self._position_start_character.append(position_start_character)
        self._position_end_line.append(position_end_line)
        self._position_end_character.append(position_end_character-1)
        self._value.append(value)

    def add_interval(self, interval: OffsetPositionInterval):
        self.add_interval_values(
            interval.offset_interval.start,
            interval.offset_interval.end,
            interval.position_range.start.line,
            interval.position_range.start.character,
            interval.position_range.end.line,
            interval.position_range.end.character,
            interval.value,
        )

    def get_interval(self, idx: int) -> OffsetPositionInterval:
        return OffsetPositionInterval(
            offset_interval=Interval(
                start=self._offset_start[idx],
                length=self._offset_end[idx]-self._offset_start[idx]+1,
            ),
            position_range=Range(
                start=Position(
                    line=self._position_start_line[idx],
                    character=self._position_start_character[idx],
                ),
                end=Position(
                    line=self._position_end_line[idx],
                    character=self._position_end_character[idx]+1,
                ),
            ),
            value=self._value[idx]
        )

    def __len__(self):
        return len(self._offset_start)

    @property
    def values(self):
        return self._value

    def sort(self):
        indices = [
            item[0]
            for item in sorted(
                enumerate(self._offset_start),
                key=lambda x:x[1]
            )
        ]
        self._offset_start = [
            self._offset_start[idx]
            for idx in indices
        ]
        self._offset_end = [
            self._offset_end[idx]
            for idx in indices
        ]
        self._position_start_line = [
            self._position_start_line[idx]
            for idx in indices
        ]
        self._position_start_character = [
            self._position_start_character[idx]
            for idx in indices
        ]
        self._position_end_line = [
            self._position_end_line[idx]
            for idx in indices
        ]
        self._position_end_character = [
            self._position_end_character[idx]
            for idx in indices
        ]

    def get_idx_at_offset(self, offset: int) -> int:
        min_lst = self._offset_start
        max_lst = self._offset_end

        idx = bisect.bisect_left(max_lst, offset)
        if idx < len(max_lst) and min_lst[idx] <= offset <= max_lst[idx]:
            return idx

        return None

    def get_interval_at_offset(self, offset: int) -> OffsetPositionInterval:
        idx = self.get_idx_at_offset(offset)
        if idx is None:
            return None
        return self.get_interval(idx)

    def get_idx_at_position(self, position: Position) -> int:
        idx = bisect.bisect_left(self._position_end_line, position.line)

        lst = list()
        i = idx
        length = len(self)
        while self._position_end_line[i] == self._position_end_line[idx]:
            lst.append(self._position_end_character[i])
            i += 1
            if i >= length:
                break

        idx2 = bisect.bisect_left(lst, position.character)
        idx += idx2

        if (
            self._position_start_line[idx] <= position.line <= self._position_end_line[idx]
            and self._position_start_character[idx] <= position.character <= self._position_end_character[idx]
        ):
            return idx

        return None

    def get_interval_at_position(self, position: Position) -> OffsetPositionInterval:
        idx = self.get_idx_at_position(position)
        if idx is None:
            return None
        return self.get_interval(idx)