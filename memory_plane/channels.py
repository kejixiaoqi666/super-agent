"""Channel contracts and deterministic local adapter for client/Bot tests."""
from dataclasses import dataclass, field
from queue import Queue, Empty
import time, uuid


@dataclass(frozen=True)
class InboundMessage:
    channel: str; account_id: str; conversation_id: str; sender_id: str; text: str
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(frozen=True)
class OutboundMessage:
    conversation_id: str; text: str; task_id: str | None = None
    buttons: tuple[str, ...] = ()


class ChannelAdapter:
    name = "abstract"
    def receive(self, timeout=0): raise NotImplementedError
    def send(self, message): raise NotImplementedError


class LoopbackChannel(ChannelAdapter):
    name = "loopback"
    def __init__(self): self.inbox, self.outbox = Queue(), Queue()
    def push(self, message): self.inbox.put(message)
    def receive(self, timeout=0):
        try: return self.inbox.get(timeout=timeout)
        except Empty: return None
    def send(self, message): self.outbox.put(message); return message
    def pop(self, timeout=0):
        try: return self.outbox.get(timeout=timeout)
        except Empty: return None


class PrincipalMapper:
    def __init__(self, mapping): self.mapping = dict(mapping)
    def resolve(self, message):
        principal = self.mapping.get((message.channel, message.account_id, message.sender_id))
        if principal is None: raise PermissionError("unmapped channel identity")
        return principal
