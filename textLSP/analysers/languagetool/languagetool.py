import logging

from collections import defaultdict
from language_tool_python import LanguageTool
from lsprotocol.types import (
        DidOpenTextDocumentParams,
        DidChangeTextDocumentParams,
        DidCloseTextDocumentParams,
        Diagnostic,
        DiagnosticSeverity,
)
from pygls.server import LanguageServer

from ..analyser import Analyser


logger = logging.getLogger(__name__)


LANGUAGE_MAP = defaultdict(lambda: 'en-US')
LANGUAGE_MAP['en'] = 'en-US'
LANGUAGE_MAP['en-US'] = 'en-US'


class LanguageToolAnalyser(Analyser):
    def __init__(self, language_server: LanguageServer, config: dict):
        super().__init__(language_server, config)
        self.tools = dict()

    def did_open(self, params: DidOpenTextDocumentParams):
        diagnostics = list()
        doc = self.get_document(params)
        matches = self._get_tool_for_language(doc.language).check(doc.cleaned_source)

        for match in matches:
            diagnostics.append(
                Diagnostic(
                    range=doc.range_at_offset(match.offset, match.errorLength),
                    message=match.message,
                    source='languagetool',
                    severity=DiagnosticSeverity.Warning,
                    code=match.ruleId,
                )
            )

        self.language_server.publish_diagnostics(doc.uri, diagnostics)

    # def did_change(self, params: DidChangeTextDocumentParams):
    #     raise NotImplementedError()

    def did_close(self, params: DidCloseTextDocumentParams):
        workspace = self.language_server.workspace
        doc_langs = {
            document.language
            for _, document in workspace.documents.items()
        }
        tool_langs = set(self.tools.keys())

        for lang in tool_langs - doc_langs:
            self.tools[lang].close()
            del self.tools[lang]

    def close(self):
        for lang, tool in self.tools.items():
            tool.close()

    def _get_mapped_language(self, language):
        return LANGUAGE_MAP[language]

    def _get_tool_for_language(self, language):
        lang = self._get_mapped_language(language)
        if lang in self.tools:
            return self.tools[lang]

        tool = LanguageTool(lang)
        self.tools[lang] = tool

        return tool