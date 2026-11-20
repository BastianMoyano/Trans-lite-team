"""Chat panel shown inside the app: a conversation about the minutes of a meeting or a project."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .chat import (
    ChatError,
    ChatSession,
    build_workspace,
    discard_conversation,
    has_chat_content,
    load_conversation,
    save_conversation,
)
from .minutes import CLI_LABELS

NO_CONTENT = "Todavía no hay transcripciones ni minutas aquí. Transcribe una reunión para poder conversar sobre ella."
NO_SCOPE = "Selecciona una reunión para conversar sobre su minuta."


@dataclass
class _Conversation:
    title: str
    meetings: list[dict]
    session: ChatSession | None = None
    cli: str | None = None
    turns: list[tuple[str, str]] = field(default_factory=list)  # (speaker, markdown)
    saved_session: str | None = None  # CLI session of a conversation restored from disk


class _Relay(QObject):
    """Carries answers from the worker thread back to the UI thread."""

    finished = Signal(str, object, object)  # scope key, answer or None, error message or None


class ChatPanel(QWidget):
    """Conversation about the minutes and transcripts in scope; each scope keeps its own conversation.

    The CLI answers in a background thread, so the window stays responsive. Before every question
    the copies in data/.chat/<scope>/ are rebuilt (never while the CLI is answering, since a scope
    asks one question at a time). Each answered turn is saved in conversation.json there, so
    closing the app keeps the conversation and its CLI session; "Nueva conversación" forgets it.
    """

    conversation_changed = Signal()  # an answer arrived or the conversation was reset

    def __init__(
        self,
        data_root: str | Path,
        *,
        default_cli: str = "claude",
        cli_provider: Callable[[], str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.data_root = Path(data_root)
        self._cli_provider = cli_provider
        self._conversations: dict[str, _Conversation] = {}
        self._scope: str | None = None
        self._busy: set[str] = set()
        self._relay = _Relay()
        self._relay.finished.connect(self._answered)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.scope_label = QLabel()
        self.scope_label.setObjectName("sectionMeta")
        self.scope_label.setWordWrap(True)
        header.addWidget(self.scope_label, 1)
        self.cli_combo = QComboBox()
        self.cli_combo.setAccessibleName("CLI para el chat")
        for cli, label in CLI_LABELS.items():
            self.cli_combo.addItem(label, cli)
        self.cli_combo.setCurrentIndex(max(0, self.cli_combo.findData(default_cli)))
        self.cli_combo.setVisible(cli_provider is None)  # Teams Lite reuses its minutes selector
        header.addWidget(self.cli_combo)
        self.new_button = QPushButton("Nueva conversación")
        self.new_button.setObjectName("ghostButton")
        header.addWidget(self.new_button)
        layout.addLayout(header)

        self.history = QTextBrowser()
        self.history.setObjectName("chatHistory")
        self.history.setOpenExternalLinks(False)
        layout.addWidget(self.history, 1)
        self.notice = QLabel()
        self.notice.setObjectName("fieldHint")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Pregunta sobre lo que se dijo o se decidió…")
        self.input.setAccessibleName("Pregunta para la IA")
        self.send_button = QPushButton("Enviar")
        self.send_button.setObjectName("primaryButton")
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button)
        layout.addLayout(row)

        self.send_button.clicked.connect(self.send)
        self.input.returnPressed.connect(self.send)
        self.new_button.clicked.connect(self.new_conversation)
        self.set_scope(None, "", [])

    @property
    def busy(self) -> bool:
        return self._scope in self._busy

    def _cli(self) -> str:
        return self._cli_provider() if self._cli_provider is not None else str(self.cli_combo.currentData())

    def set_scope(self, scope_key: str | None, title: str, meetings: list[dict]) -> None:
        """Show the conversation of a meeting or a project; meetings are those it may read."""
        self._scope = scope_key
        if scope_key is not None:
            conversation = self._conversations.get(scope_key)
            if conversation is None:
                conversation = self._conversations[scope_key] = self._restore(scope_key, title, meetings)
            else:
                # The copies are rebuilt before every question, so the latest meetings always apply.
                conversation.title, conversation.meetings = title, meetings
        self._render()

    def _restore(self, scope_key: str, title: str, meetings: list[dict]) -> _Conversation:
        saved = load_conversation(self.data_root, scope_key)
        if saved is None:
            return _Conversation(title, meetings)
        return _Conversation(title, meetings, cli=saved["cli"], turns=saved["turns"], saved_session=saved["session"])

    def _current(self) -> _Conversation | None:
        return self._conversations.get(self._scope) if self._scope is not None else None

    def _has_content(self, conversation: _Conversation) -> bool:
        return any(has_chat_content(self.data_root, meeting["id"]) for meeting in conversation.meetings)

    def _render(self, notice: str | None = None) -> None:
        conversation = self._current()
        if conversation is None:
            self.scope_label.setText("")
            self.history.clear()
            ready, message = False, NO_SCOPE
        else:
            who = CLI_LABELS.get(conversation.cli or "", "")
            self.scope_label.setText(f"Conversación sobre: {conversation.title}" + (f" · con {who}" if who else ""))
            self.history.setMarkdown("\n\n".join(f"**{speaker}:** {text}" for speaker, text in conversation.turns))
            self.history.verticalScrollBar().setValue(self.history.verticalScrollBar().maximum())
            started = conversation.session is not None or conversation.saved_session is not None
            ready = started or self._has_content(conversation)
            if self.busy:
                message = f"{who or 'La IA'} está respondiendo…"
            elif not ready:
                message = NO_CONTENT
            elif not conversation.turns:
                message = "Las respuestas salen solo de las minutas y transcripciones de estas reuniones."
            else:
                message = ""
        self.notice.setText(notice if notice is not None else message)
        can_type = ready and not self.busy
        self.input.setEnabled(can_type)
        self.send_button.setEnabled(can_type)
        self.new_button.setEnabled(conversation is not None and bool(conversation.turns) and not self.busy)

    def forget(self, scope_key: str) -> None:
        """Drop a conversation whose meetings no longer exist (a deleted meeting)."""
        if scope_key in self._busy:
            return  # its answer is still on the way; the scope is never shown again anyway
        self._conversations.pop(scope_key, None)
        if scope_key == self._scope:
            self._scope = None
            self._render()

    def new_conversation(self) -> None:
        conversation = self._current()
        if conversation is None or self.busy:
            return
        self._conversations[self._scope] = _Conversation(conversation.title, conversation.meetings)
        try:
            discard_conversation(self.data_root, self._scope)
        except OSError as exc:
            self._render(f"No se pudo borrar la conversación guardada: {exc}")
        else:
            self._render()
        self.conversation_changed.emit()

    def send(self) -> None:
        conversation, scope = self._current(), self._scope
        question = self.input.text().strip()
        if conversation is None or scope is None or not question or self.busy:
            return
        try:
            # Rebuilt before every question: minutes generated meanwhile reach this answer.
            workspace = build_workspace(self.data_root, conversation.meetings, scope_key=scope, title=conversation.title)
            if conversation.session is None:
                # A restored conversation keeps its CLI and resumes its session.
                conversation.session = ChatSession(workspace, conversation.cli or self._cli(),
                                                   session=conversation.saved_session)
        except (ChatError, OSError) as exc:
            self._render(str(exc) if isinstance(exc, ChatError) else f"No se pudo preparar el chat: {exc}")
            return
        conversation.cli = conversation.session.cli
        session = conversation.session
        conversation.turns.append(("Tú", question))
        self.input.clear()
        self._busy.add(scope)
        self._render()

        def work() -> None:
            try:
                self._relay.finished.emit(scope, session.ask(question), None)
            except ChatError as exc:
                self._relay.finished.emit(scope, None, str(exc))
            except Exception as exc:  # never leave the panel waiting forever
                self._relay.finished.emit(scope, None, f"No se pudo obtener la respuesta: {exc}")

        threading.Thread(target=work, name="meeting-chat", daemon=True).start()

    def _answered(self, scope: str, answer: str | None, error: str | None) -> None:
        self._busy.discard(scope)
        conversation = self._conversations.get(scope)
        if conversation is not None and answer is not None:
            conversation.turns.append((CLI_LABELS.get(conversation.cli or "", "IA"), answer))
            try:
                save_conversation(self.data_root, scope, title=conversation.title, cli=conversation.cli,
                                  session=conversation.session.session_id if conversation.session else None,
                                  turns=conversation.turns)
            except OSError as exc:
                error = f"La respuesta llegó, pero no se pudo guardar la conversación: {exc}"
        if scope == self._scope:
            self._render(error)
            self.conversation_changed.emit()

    def export_text(self) -> str:
        """The visible conversation as Markdown, for Copiar and Descargar."""
        conversation = self._current()
        if conversation is None:
            return ""
        return "\n\n".join(f"**{speaker}:** {text}" for speaker, text in conversation.turns)
