import logging

from typing import Optional

from lsprotocol.types import (
    TextDocumentContentChangeEvent,
    VersionedTextDocumentIdentifier,
)
from pygls.workspace import Workspace, Document

from .documents.document import DocumentTypeFactory
from .analysers.handler import AnalyserHandler

logger = logging.getLogger(__name__)


class TextLSPWorkspace(Workspace):
    def __init__(self, analyser_handler: AnalyserHandler, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.analyser_handler = analyser_handler

    def _create_document(
        self,
        doc_uri: str,
        source: Optional[str] = None,
        version: Optional[int] = None,
        language_id: Optional[str] = None,
    ) -> Document:
        return DocumentTypeFactory.get_document(
            doc_uri=doc_uri,
            source=source,
            version=version,
            language_id=language_id,
            sync_kind=self._sync_kind
        )

    @staticmethod
    def workspace2textlspworkspace(workspace: Workspace, analyser_handler: AnalyserHandler):
        return TextLSPWorkspace(
            analyser_handler=analyser_handler,
            root_uri=workspace._root_uri,
            sync_kind=workspace._sync_kind,
            workspace_folders=[folder for folder in workspace._folders.values()],
        )

    def update_document(self,
                        text_doc: VersionedTextDocumentIdentifier,
                        change: TextDocumentContentChangeEvent):
        doc = self._docs[text_doc.uri]
        self.analyser_handler.update_document(doc, change)
        super().update_document(text_doc, change)
