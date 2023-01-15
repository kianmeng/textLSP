import logging
import tempfile

from typing import Optional, Generator, List
from dataclasses import dataclass

from lsprotocol.types import (
    Range,
    Position,
    TextDocumentContentChangeEvent,
    TextDocumentContentChangeEvent_Type2,
)
from pygls.workspace import Document, position_from_utf16
from tree_sitter import Language, Parser, Tree, Node

from ..utils import get_class, synchronized, git_clone, get_user_cache
from ..types import (
    OffsetPositionIntervalList,
    Interval
)
from .. import documents

logger = logging.getLogger(__name__)


class BaseDocument(Document):
    @property
    def cleaned_source(self) -> str:
        return self.source

    @property
    def cleaned_lines(self):
        return self.cleaned_source.splitlines(True)

    @property
    def language(self) -> str:
        return 'en'

    def position_at_offset(self, offset: int, cleaned=False) -> Position:
        pos = 0
        lines = self.cleaned_lines if cleaned else self.lines
        for lidx, line in enumerate(lines):
            line_len = len(line)
            if pos + line_len > offset:
                return Position(
                    line=lidx,
                    character=offset-pos
                )
            pos += line_len

    def range_at_offset(self, offset: int, length: int, cleaned=False) -> Range:
        start = self.position_at_offset(offset)
        if start is None:
            return None

        length = start.character + length
        lines = self.cleaned_lines if cleaned else self.lines
        for lidx, line in enumerate(lines[start.line:], start.line):
            line_len = len(line)
            if line_len > length:
                return Range(
                    start=start,
                    end=Position(
                        line=lidx,
                        character=length
                    )
                )
            length -= line_len

        return Range(
            start=start,
            end=Position(
                line=len(lines)-1,
                character=len(lines[-1])-1
            )
        )

    def offset_at_position(self, position: Position, cleaned=False) -> int:
        # doesn't really matter
        lines = self.cleaned_lines if cleaned else self.lines
        pos = position_from_utf16(lines, position)
        row, col = pos.line, pos.character
        return col + sum(len(line) for line in lines[:row])

    def paragraph_at_offset(self, offset: int, min_length=0, cleaned=False) -> Interval:
        """
        returns (start_offset, length)
        """
        start_idx = offset
        end_idx = offset
        source = self.cleaned_source if cleaned else self.source
        len_source = len(source)

        assert start_idx >= 0
        assert end_idx < len_source

        while (
            start_idx >= 0
            and not (
                all(source[start_idx-i] == '\n' for i in range(1, min(3, start_idx+1)))
                or all(source[start_idx-i] == '\n' for i in range(min(2, start_idx+1)))  # empty line
            )
        ):
            start_idx -= 1

        while True:
            while (
                end_idx <= len_source-1
                and not (
                    all(source[end_idx+i] == '\n' for i in range(min(2, len_source-end_idx)))
                    or all(source[start_idx-i] == '\n' for i in range(min(2, start_idx+1)))  # empty line
                )
            ):
                end_idx += 1

            if end_idx < len_source-1 and end_idx-start_idx+1 < min_length:
                end_idx += 1
            else:
                break

        return Interval(start_idx, end_idx-start_idx+1)

    def paragraph_at_position(self, position: Position, cleaned=False) -> Interval:
        offset = self.offset_at_position(position, cleaned)
        if offset is None:
            return None
        return self.paragraph_at_offset(offset, cleaned=cleaned)

    def paragraphs_at_range(self, position_range: Range, cleaned=False) -> List[Interval]:
        res = list()
        source = self.cleaned_source if cleaned else self.source
        position = position_range.start

        while position < position_range.end:
            paragraph = self.paragraph_at_position(position, cleaned)
            if paragraph is None:
                continue
            res.append(paragraph)
            text = source[paragraph.start:paragraph.start+paragraph.length].splitlines()
            line = position.line+len(text)
            char = 0
            position = Position(
                line=line,
                character=char
            )

        return res

    def last_position(self, cleaned=False):
        lines = self.cleaned_lines if cleaned else self.lines
        return Position(
            line=(len(lines)-1),
            character=len(lines[-1])-1
        )


class CleanableDocument(BaseDocument):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cleaned_source = None

    @property
    def cleaned_source(self) -> str:
        if self._cleaned_source is None:
            self._sync_clean_source()
        return self._cleaned_source

    @synchronized
    def _sync_clean_source(self):
        # just so that implementations don't need to remember to use
        # synchronized
        self._clean_source()

    def _clean_source(self):
        raise NotImplementedError()

    def apply_change(self, change: TextDocumentContentChangeEvent) -> None:
        super().apply_change(change)
        self._cleaned_source = None

    def offset_at_position(self, position: Position, cleaned=False) -> int:
        if not cleaned:
            return super().offset_at_position(position, cleaned)

        raise NotImplementedError()

    def paragraphs_at_range(self, position_range: Range, cleaned=False) -> Interval:
        if not cleaned:
            return super().paragraphs_at_range(position_range, cleaned)

        raise NotImplementedError()

    def last_position(self, cleaned=False):
        if not cleaned:
            return super().last_position(cleaned)

        raise NotImplementedError()


@dataclass
class TextNode():
    text: str
    start_point: tuple
    end_point: tuple

    @staticmethod
    def from_ts_node(node: Node):
        return TextNode(
            text=node.text.decode('utf-8'),
            start_point=node.start_point,
            end_point=node.end_point,
        )

    @staticmethod
    def space(start_point: tuple, end_point: tuple):
        return TextNode(
            text=' ',
            start_point=start_point,
            end_point=end_point,
        )

    @staticmethod
    def new_line(start_point: tuple, end_point: tuple):
        return TextNode(
            text='\n',
            start_point=start_point,
            end_point=end_point,
        )

    def __len__(self):
        return len(self.text)


class TreeSitterDocument(CleanableDocument):
    LIB_PATH_TEMPLATE = '{}/treesitter/{}.so'.format(get_user_cache(), '{}')

    def __init__(self, language_name, grammar_url, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._language = TreeSitterDocument.get_language(language_name, grammar_url)
        self._parser = TreeSitterDocument.get_parser(
            language_name,
            grammar_url,
            self._language
        )
        self._text_intervals = None

    @staticmethod
    def build_library(name, url) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            git_clone(url, tmpdir)
            Language.build_library(
                TreeSitterDocument.LIB_PATH_TEMPLATE.format(name),
                [tmpdir]
            )

    @staticmethod
    def get_language(name, url) -> Language:
        try:
            return Language(
                TreeSitterDocument.LIB_PATH_TEMPLATE.format(name),
                name,
            )
        except Exception:
            TreeSitterDocument.build_library(name, url)
            return Language(
                TreeSitterDocument.LIB_PATH_TEMPLATE.format(name),
                name,
            )

    @staticmethod
    def get_parser(name, url, language=None) -> Parser:
        parser = Parser()
        if language is None:
            language = TreeSitterDocument.get_language(name, url)
        parser.set_language(language)
        return parser

    def _clean_source(self):
        tree = self._parser.parse(bytes(self.source, 'utf-8'))
        self._text_intervals = OffsetPositionIntervalList()

        offset = 0
        for node in self._iterate_text_nodes(tree):
            node_len = len(node)
            self._text_intervals.add_interval_values(
                    offset,
                    offset+node_len-1,
                    node.start_point[0],
                    node.start_point[1],
                    node.end_point[0],
                    node.end_point[1],
                    node.text,
            )
            offset += node_len

        self._cleaned_source = ''.join(self._text_intervals.values)

    def _iterate_text_nodes(self, tree: Tree) -> Generator[TextNode, None, None]:
        raise NotImplementedError()

    def position_at_offset(self, offset: int, cleaned=False) -> Position:
        if not cleaned:
            return super().position_at_offset(offset, cleaned)

        if self._cleaned_source is None:
            self._clean_source()

        item = self._text_intervals.get_interval_at_offset(offset)
        assert offset >= item.offset_interval.start
        diff = offset - item.offset_interval.start

        return Position(
            line=item.position_range.start.line,
            character=item.position_range.start.character+diff,
        )

    def range_at_offset(self, offset: int, length: int, cleaned=False) -> Range:
        if not cleaned:
            return super().range_at_offset(offset, length, cleaned)

        start = self.position_at_offset(offset, cleaned)
        offset += length
        item = self._text_intervals.get_interval_at_offset(offset-1)
        item_end = item.offset_interval.start + item.offset_interval.length
        assert offset <= item_end, f'{offset}, {item_end}, {item}'
        diff = item_end - offset

        end = Position(
            line=item.position_range.end.line,
            character=item.position_range.end.character-diff,
        )

        return Range(
            start=start,
            end=end,
        )

    def offset_at_position(self, position: Position, cleaned=False) -> int:
        if not cleaned:
            return super().offset_at_position(position, cleaned)

        if self._cleaned_source is None:
            self._clean_source()

        item = self._text_intervals.get_interval_at_position(position)
        if item is None:
            return None

        assert item.position_range.start.line == position.line, "This shouldn't happen!"
        assert item.position_range.start.character <= position.character
        diff = position.character - item.position_range.start.character

        return item.offset_interval.start + diff

    def paragraphs_at_range(self, position_range: Range, cleaned=False) -> List[Interval]:
        if not cleaned:
            return super().paragraphs_at_range(position_range, cleaned)

        if self._cleaned_source is None:
            self._clean_source()

        res = list()
        res_set = set()

        idx = self._text_intervals.get_idx_at_position(position_range.start)
        for i in range(idx, len(self._text_intervals)):
            interval = self._text_intervals.get_interval(i)
            if interval.position_range.start > position_range.end:
                break

            paragraph = self.paragraph_at_offset(
                interval.offset_interval.start,
                min_length=interval.offset_interval.length,
                cleaned=True
            )
            if paragraph is not None and paragraph not in res_set:
                res.append(paragraph)
                res_set.add(paragraph)

        return res


class DocumentTypeFactory():
    TYPE_MAP = {
        'text': 'txt',
        'tex': 'latex',
    }

    @staticmethod
    def get_file_type(language_id):
        return DocumentTypeFactory.TYPE_MAP.get(language_id) or language_id

    @staticmethod
    def get_document(
        doc_uri: str,
        source: Optional[str] = None,
        version: Optional[int] = None,
        language_id: Optional[str] = None,
        sync_kind=None,
    ) -> Document:
        try:
            cls = get_class(
                '{}.{}'.format(
                    documents.__name__,
                    DocumentTypeFactory.get_file_type(language_id)
                ),
                BaseDocument,
            )
            return cls(
                uri=doc_uri,
                source=source,
                version=version,
                language_id=language_id,
                sync_kind=sync_kind
            )
        except ImportError:
            return BaseDocument(
                uri=doc_uri,
                source=source,
                version=version,
                language_id=language_id,
                sync_kind=sync_kind
            )


class ChangeTracker():
    def __init__(self, doc: BaseDocument, cleaned=False):
        self.document = doc
        self.cleaned = cleaned
        length = len(doc.cleaned_source) if cleaned else len(doc.source)
        # list of tuples (span_length, was_changed)
        # negative span_length means that the span was deleted
        self._items = [(length, False)]
        self.full_document_change = False

    def update_document(self, change: TextDocumentContentChangeEvent):
        if self.full_document_change:
            return

        if type(change) == TextDocumentContentChangeEvent_Type2:
            self.full_document_change = True
            self._items = [(-1, True)]
            return

        new_lst = list()
        start_offset = self.document.offset_at_position(
            change.range.start,
            self.cleaned,
        )
        end_offset = self.document.offset_at_position(
            change.range.end,
            self.cleaned,
        )

        item_idx, item_offset = self._get_offset_idx(start_offset)
        change_length = len(change.text)
        range_length = end_offset-start_offset
        start_offset = start_offset - item_offset

        if start_offset > 0:
            new_lst.append((start_offset, self._items[item_idx][1]))

        if change_length >= range_length:
            effective_change_length = change_length
        else:
            effective_change_length = change_length-range_length
        effective_change_length = max(effective_change_length, -1*item_offset)
        new_lst.append((effective_change_length, True))

        doc_len = len(self.document.cleaned_source) if self.cleaned else len(self.document.source)
        if end_offset < doc_len:
            new_lst.append((
                self._items[item_idx][0]-start_offset-range_length,
                self._items[item_idx][1]
            ))

        self._replace_at(item_idx, new_lst)

    def _get_offset_idx(self, offset):
        pos = 0
        idx = 0

        while pos <= offset and pos+self._items[idx][0] <= offset:
            pos += self._items[idx][0]
            idx += 1

        return idx, pos

    def _replace_at(self, idx, tuples):
        length = len(tuples)
        assert length > 0
        self._items[idx] = tuples[0]
        for i in range(1, length):
            self._items.insert(idx+i, tuples[i])

    def get_changes(self) -> List[Interval]:
        if self.cleaned:
            doc_length = len(self.document.cleaned_source)
        else:
            doc_length = len(self.document.source)

        if self.full_document_change:
            return [Interval(0, doc_length)]

        res = list()
        pos = 0
        for item in self._items:
            if item[1]:
                length = item[0]
                if length < 0:
                    position = max(0, pos+length)
                    # use pos instead of position since the doc_length will change after modification
                    length = min(length*-1, doc_length-pos)
                else:
                    position = min(pos, doc_length-1)
                res.append(Interval(position, length))
            pos += item[0]

        return res

    def __len__(self):
        return len([item for item in self._items if item[1]])
