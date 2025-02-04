import re
import bisect
import enum
import difflib
import uuid

from typing import Optional, Any, List
from dataclasses import dataclass
from sortedcontainers import SortedDict

from lsprotocol.types import (
    Position,
    Range,
    CodeActionKind,
    WorkDoneProgressBegin,
    WorkDoneProgressReport,
    WorkDoneProgressEnd,
)

from .utils import position_to_tuple


TEXT_PASSAGE_PATTERN = re.compile('[.?!] |\\n')
LINE_PATTERN = re.compile('\\n')


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
        self._position_end_character.append(position_end_character)
        self._value.append(value)

    def add_interval(self, interval: OffsetPositionInterval):
        self.add_interval_values(
            interval.offset_interval.start,
            interval.offset_interval.start + interval.offset_interval.length - 1,
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
                    character=self._position_end_character[idx],
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

    def get_idx_at_position(self, position: Position, strict=True) -> int:
        """
        :param strict: If Flase, return the idx of the next (or last) interval if does not exist
        """
        idx = bisect.bisect_left(self._position_end_line, position.line)
        length = len(self)

        if idx == length:
            return None if strict else length-1
        if position.line < self._position_start_line[idx]:
            return None if strict else idx
        if position.line > self._position_end_line[idx]:
            return None if strict else length-1

        lst = list()
        i = idx
        while self._position_end_line[i] == self._position_end_line[idx]:
            lst.append(self._position_end_character[i])
            i += 1
            if i >= length:
                break

        idx2 = bisect.bisect_left(lst, position.character)
        idx += idx2

        if idx == length:
            return None if strict else length-1

        if self._position_start_character[idx] <= position.character <= self._position_end_character[idx]:
            return idx
        if (
                position.line < self._position_start_line[idx] or
                position.character < self._position_start_character[idx]
           ):
            return None if strict else idx

        return None if strict else min(idx+1, length-1)

    def get_interval_at_position(self, position: Position, strict=True) -> OffsetPositionInterval:
        """
        :param strict: If Flase, return the object of the next (or last) interval if does not exist
        """
        idx = self.get_idx_at_position(position, strict)
        if idx is None:
            return None
        return self.get_interval(idx)


class PositionDict():

    def __init__(self):
        self._positions = SortedDict()

    def add(self, position: Position, item):
        position = position_to_tuple(position)
        self._positions[position] = item

    def get(self, position: Position):
        position = position_to_tuple(position)
        return self._positions[position]

    def pop(self, position: Position):
        position = position_to_tuple(position)
        return self._positions.popitem(position)

    def update(self, old_position: Position, new_position: Position = None,
               new_value=None):
        assert new_position is not None or new_value is not None, ' new_position'
        ' or new_value should be specified.'

        old_position = position_to_tuple(old_position)
        new_position = position_to_tuple(new_position)
        if new_position is None:
            self._positions[old_position] = new_value
            return

        if new_value is None:
            new_value = self._positions.popitem(old_position)
        else:
            del self._positions[old_position]

        self._positions[new_position] = new_value

    def remove(self, position: Position):
        position = position_to_tuple(position)
        del self._positions[position]

    def remove_from(self, position: Position, inclusive=True):
        position = position_to_tuple(position)
        num = 0
        for key in list(self._positions.irange(
            minimum=position,
            inclusive=(inclusive, False)
        )):
            del self._positions[key]
            num += 1

        return num

    def remove_between(self, range: Range, inclusive=(True, True)):
        minimum = position_to_tuple(range.start)
        maximum = position_to_tuple(range.end)
        num = 0
        for key in list(self._positions.irange(
            minimum=minimum,
            maximum=maximum,
            inclusive=inclusive,
        )):
            del self._positions[key]
            num += 1

        return num

    def irange(self, minimum: Position = None, maximum: Position = None, *args,
               **kwargs):
        if minimum is not None:
            minimum = position_to_tuple(minimum)
        if maximum is not None:
            maximum = position_to_tuple(maximum)

        return self._positions.irange(minimum, maximum, *args, **kwargs)

    def irange_values(self, *args, **kwargs):
        for key in self.irange(*args, **kwargs):
            yield self._positions[key]

    def __iter__(self):
        return iter(self._positions.values())


@enum.unique
class TextLSPCodeActionKind(str, enum.Enum):
    AcceptSuggestion = CodeActionKind.QuickFix + '.accept_suggestion'
    Command = 'command'


@dataclass
class TokenDiff():
    INSERT = 'insert'
    DELETE = 'delete'
    REPLACE = 'replace'

    type: str
    old_token: str
    new_token: str
    offset: int
    length: int

    @staticmethod
    def _split(text):
        return [item for item in re.split("(\s)", text) if item != ""]

    @staticmethod
    def token_level_diff(text1, text2) -> List:
        tokens1 = TokenDiff._split(text1)
        tokens2 = TokenDiff._split(text2)
        diff = difflib.SequenceMatcher(None, tokens1, tokens2)

        return [
            TokenDiff(
                type=item[0],
                old_token=''.join(tokens1[item[1]:item[2]]),
                new_token=''.join(tokens2[item[3]:item[4]]),
                offset=0 if item[1] == 0 else len(''.join(tokens1[:item[1]])),
                length=len(''.join(tokens1[item[1]:item[2]])),
            )
            for item in diff.get_opcodes()
            if item[0] != 'equal'
        ]

    def __str__(self):
        return (
            f'{self.type}: {self.old_token} -> {self.new_token} '
            f'({self.offset}, {self.length})'
        )


class ProgressBar():
    def __init__(self, ls, title='', percentage=0, token=None):
        self.ls = ls
        self.title = title
        self.percentage = percentage
        self.token = token
        if self.token is None:
            self.token = self.create_token()

    def begin(self, title=None, percentage=None):
        if title is not None:
            self.title = title
        if percentage is not None:
            self.percentage = percentage

        if self.token not in self.ls.progress.tokens:
            self.ls.progress.create(self.token)
        self.ls.progress.begin(
            self.token,
            WorkDoneProgressBegin(
                title=self.title,
                percentage=self.percentage,
            )
        )

    def update(self, message, percentage=0):
        self.ls.progress.report(
            self.token,
            WorkDoneProgressReport(
                message=message,
                percentage=percentage
            ),
        )

    def end(self, message):
        self.ls.progress.end(
            self.token,
            WorkDoneProgressEnd(message=message)
        )

    def __enter__(self):
        self.begin()
        return self

    def __exit__(self, type, value, traceback):
        self.end('Done')

    @staticmethod
    def create_token():
        return str(uuid.uuid4())
