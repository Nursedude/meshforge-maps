"""Thread-safe publish-subscribe event bus for decoupled component communication.

Enables MQTT subscriber, collectors, and the map server to communicate
without direct coupling. Events flow from producers (collectors, MQTT)
through the bus to consumers (WebSocket broadcast, status tracking).

Typed events:
    NodeEvent    - Node position/info/telemetry updates
    ServiceEvent - Collector/service health state changes
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Event categories for subscription filtering."""
    NODE_POSITION = "node.position"
    NODE_INFO = "node.info"
    NODE_TELEMETRY = "node.telemetry"
    NODE_TOPOLOGY = "node.topology"
    SERVICE_UP = "service.up"
    SERVICE_DOWN = "service.down"
    SERVICE_DEGRADED = "service.degraded"
    DATA_REFRESHED = "data.refreshed"


@dataclass
class Event:
    """Base event with type, timestamp, and arbitrary payload."""
    event_type: EventType
    timestamp: float = field(default_factory=time.time)
    source: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeEvent(Event):
    """Event for node position/info/telemetry updates."""
    node_id: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None

    @classmethod
    def position(cls, node_id: str, lat: float, lon: float,
                 source: str = "mqtt", **extra) -> "NodeEvent":
        return cls(
            event_type=EventType.NODE_POSITION,
            node_id=node_id,
            lat=lat,
            lon=lon,
            source=source,
            data=extra,
        )

    @classmethod
    def info(cls, node_id: str, source: str = "mqtt", **extra) -> "NodeEvent":
        return cls(
            event_type=EventType.NODE_INFO,
            node_id=node_id,
            source=source,
            data=extra,
        )

    @classmethod
    def telemetry(cls, node_id: str, source: str = "mqtt",
                  **extra) -> "NodeEvent":
        return cls(
            event_type=EventType.NODE_TELEMETRY,
            node_id=node_id,
            source=source,
            data=extra,
        )

    @classmethod
    def topology(cls, node_id: str, source: str = "mqtt",
                 **extra) -> "NodeEvent":
        return cls(
            event_type=EventType.NODE_TOPOLOGY,
            node_id=node_id,
            source=source,
            data=extra,
        )


@dataclass
class ServiceEvent(Event):
    """Event for collector/service health state changes."""
    service_name: str = ""

    @classmethod
    def up(cls, service_name: str, **extra) -> "ServiceEvent":
        return cls(
            event_type=EventType.SERVICE_UP,
            service_name=service_name,
            source=service_name,
            data=extra,
        )

    @classmethod
    def down(cls, service_name: str, reason: str = "",
             **extra) -> "ServiceEvent":
        extra["reason"] = reason
        return cls(
            event_type=EventType.SERVICE_DOWN,
            service_name=service_name,
            source=service_name,
            data=extra,
        )

    @classmethod
    def degraded(cls, service_name: str, reason: str = "",
                 **extra) -> "ServiceEvent":
        extra["reason"] = reason
        return cls(
            event_type=EventType.SERVICE_DEGRADED,
            service_name=service_name,
            source=service_name,
            data=extra,
        )


# Type alias for subscriber callbacks
Subscriber = Callable[[Event], None]


class EventBus:
    """Thread-safe publish-subscribe event bus.

    Subscribers register for specific event types. When an event is
    published, all matching subscribers are called synchronously in
    the publisher's thread. Each callback is wrapped in try/except
    so one bad subscriber never breaks others.

    Usage:
        bus = EventBus()
        bus.subscribe(EventType.NODE_POSITION, my_handler)
        bus.publish(NodeEvent.position("!abc123", 40.0, -105.0))

    Wildcard subscriptions:
        bus.subscribe(None, my_handler)  # receives ALL events
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # EventType -> set of callbacks; None key = wildcard subscribers
        self._subscribers: Dict[Optional[EventType], Set[Subscriber]] = {}
        self._stats = _BusStats()

    def subscribe(self, event_type: Optional[EventType],
                  callback: Subscriber) -> None:
        """Register a callback for an event type (or None for all events)."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = set()
            self._subscribers[event_type].add(callback)

    def unsubscribe(self, event_type: Optional[EventType],
                    callback: Subscriber) -> None:
        """Remove a previously registered callback."""
        with self._lock:
            subs = self._subscribers.get(event_type)
            if subs:
                subs.discard(callback)
                if not subs:
                    del self._subscribers[event_type]

    def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers.

        Calls subscribers for the specific event type plus wildcard
        subscribers. Each callback is wrapped in try/except to
        isolate failures.
        """
        with self._lock:
            # Collect targeted + wildcard subscribers
            targets: List[Subscriber] = []
            specific = self._subscribers.get(event.event_type)
            if specific:
                targets.extend(specific)
            wildcard = self._subscribers.get(None)
            if wildcard:
                targets.extend(wildcard)

        self._stats.inc_published()

        for callback in targets:
            self._safe_call(callback, event)

    def _safe_call(self, callback: Subscriber, event: Event) -> None:
        """Call a subscriber, catching and logging any exception."""
        try:
            callback(event)
            self._stats.inc_delivered()
        except Exception:
            self._stats.inc_errors()
            logger.exception(
                "Event bus subscriber %s failed on %s",
                getattr(callback, "__name__", repr(callback)),
                event.event_type.value,
            )

    def subscriber_count(self, event_type: Optional[EventType] = None) -> int:
        """Count subscribers for a specific event type (or all if None)."""
        with self._lock:
            if event_type is not None:
                return len(self._subscribers.get(event_type, set()))
            return sum(len(s) for s in self._subscribers.values())

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "total_published": self._stats.total_published,
            "total_delivered": self._stats.total_delivered,
            "total_errors": self._stats.total_errors,
        }

    def reset(self) -> None:
        """Remove all subscribers and reset stats."""
        with self._lock:
            self._subscribers.clear()
        self._stats.reset()


class _BusStats:
    """Thread-safe counters for event bus diagnostics."""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_published = 0
        self._total_delivered = 0
        self._total_errors = 0

    def reset(self) -> None:
        """Reset all counters to zero (thread-safe)."""
        with self._lock:
            self._total_published = 0
            self._total_delivered = 0
            self._total_errors = 0

    def inc_published(self) -> None:
        with self._lock:
            self._total_published += 1

    def inc_delivered(self) -> None:
        with self._lock:
            self._total_delivered += 1

    def inc_errors(self) -> None:
        with self._lock:
            self._total_errors += 1

    @property
    def total_published(self) -> int:
        with self._lock:
            return self._total_published

    @property
    def total_delivered(self) -> int:
        with self._lock:
            return self._total_delivered

    @property
    def total_errors(self) -> int:
        with self._lock:
            return self._total_errors
