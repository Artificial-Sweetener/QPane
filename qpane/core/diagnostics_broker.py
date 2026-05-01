#    QPane - High-performance PySide6 image viewer
#    Copyright (C) 2025  Artificial Sweetener and contributors
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Diagnostics broker for QPane.

Centralizes provider registration so the view stays the single owner of render

and viewport collaborators while optional domains extend the overlay.
"""

from __future__ import annotations


import logging

from collections.abc import Callable, Iterable

from dataclasses import dataclass

from typing import TYPE_CHECKING


from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QThread,
    QTimer,
    Signal,
)


from .diagnostics import (
    DiagnosticsProvider,
    DiagnosticsRegistry,
    DiagnosticsSnapshot,
    build_core_diagnostics,
)
from ..types import DiagnosticRecord


logger = logging.getLogger(__name__)


if TYPE_CHECKING:  # pragma: no cover

    from ..concurrency import TaskExecutorProtocol
    from ..qpane import QPane
    from ..rendering import View
_DEFAULT_DOMAIN = "custom"

_CORE_TIER = "core"

_DETAIL_TIER = "detail"


_DEFAULT_PRIORITIES = {
    _CORE_TIER: 100,
    _DETAIL_TIER: 110,
}


_DOMAIN_PRIORITIES = {
    ("render", _CORE_TIER): 0,
    ("cache", _CORE_TIER): 10,
    ("cache", _DETAIL_TIER): 15,
    ("swap", _CORE_TIER): 20,
    ("swap", _DETAIL_TIER): 25,
    ("mask", _CORE_TIER): 30,
    ("mask", _DETAIL_TIER): 35,
    ("sam", _CORE_TIER): 40,
    ("sam", _DETAIL_TIER): 45,
    ("executor", _CORE_TIER): 50,
    ("executor", _DETAIL_TIER): 55,
    ("retry", _CORE_TIER): 60,
    ("retry", _DETAIL_TIER): 65,
}


@dataclass(frozen=True)
class _DiagnosticsProviderEntry:
    """Metadata describing a registered diagnostics provider."""

    raw_provider: DiagnosticsProvider
    wrapped_provider: DiagnosticsProvider
    domain: str
    tier: str
    priority: int


class Diagnostics(QObject):
    """Coordinate diagnostics providers for a QPane instance.

    Wrap :class:`DiagnosticsRegistry` so installers can add domain providers
    without touching the registry directly. Centralized registration keeps the
    view in control of render and viewport state while cache, swap, mask, and
    executor domains feed the overlay.
    """

    diagnosticsUpdated = Signal(object)

    def __init__(self, qpane: "QPane") -> None:
        """Initialize the broker and attach a fresh diagnostics registry."""
        super().__init__(qpane)
        self._qpane = qpane
        self._registry = DiagnosticsRegistry(qpane)
        self._registered: set[DiagnosticsProvider] = set()
        self._providers: list[_DiagnosticsProviderEntry] = []
        self._detail_enabled: dict[str, bool] = {}
        self._stack_accessor: Callable[[], "View | None"] | None = None
        self._core_provider: DiagnosticsProvider | None = None
        self._executor_accessor: Callable[[], "TaskExecutorProtocol | None"] | None = (
            None
        )
        self._executor_summary_provider: DiagnosticsProvider | None = None
        self._executor_detail_provider: DiagnosticsProvider | None = None
        self._retry_detail_provider: DiagnosticsProvider | None = None
        self._cached_snapshot: DiagnosticsSnapshot | None = None
        self._dirty_domains: set[str] = set()
        self._provider_rows: dict[DiagnosticsProvider, tuple[DiagnosticRecord, ...]] = (
            {}
        )
        self._force_refresh = False
        self._dirty_refresh_timer: QTimer | None = None
        self._presenter_missing_logged = False

    def set_domain_detail_enabled(self, domain: str, enabled: bool) -> None:
        """Enable or disable detail-tier providers for ``domain``."""
        previous = self._detail_enabled.get(domain)
        self._detail_enabled[domain] = enabled
        if previous != enabled:
            self.set_dirty(domain)

    def domain_detail_enabled(self, domain: str) -> bool:
        """Return True when detail-tier providers for ``domain`` are enabled."""
        return self._detail_enabled.get(domain, False)

    def detail_domains(self) -> tuple[str, ...]:
        """Return sorted domain names that expose detail-tier providers."""
        return tuple(
            sorted(
                {
                    entry.domain
                    for entry in self._providers
                    if entry.tier == _DETAIL_TIER
                }
            )
        )

    def cached_snapshot(self, *, force: bool = False) -> DiagnosticsSnapshot:
        """Return cached diagnostics, refreshing when forced or dirty.

        Side effects:
            Emits ``diagnosticsUpdated`` when a fresh snapshot is collected.
        """
        if force or self._should_refresh():
            return self._collect_snapshot(force=force)
        if self._cached_snapshot is None:
            self._cached_snapshot = DiagnosticsSnapshot(tuple())
        return self._cached_snapshot

    def set_dirty(self, domain: str | None = None) -> None:
        """Mark diagnostics as stale so the next refresh gathers fresh rows."""

        def _mark() -> None:
            """Mark the given domain as dirty and schedule a refresh."""
            if domain:
                self._dirty_domains.add(domain)
            else:
                self._dirty_domains.update(entry.domain for entry in self._providers)
            self._schedule_dirty_refresh()

        self._invoke_on_main(_mark)

    @property
    def registry(self) -> DiagnosticsRegistry:
        """Expose the underlying diagnostics registry for direct consumers."""
        return self._registry

    def register_provider(
        self,
        provider: DiagnosticsProvider,
        *,
        domain: str = _DEFAULT_DOMAIN,
        tier: str = _CORE_TIER,
    ) -> None:
        """Register an arbitrary diagnostics provider once."""
        self._register_once(provider, domain=domain, tier=tier)

    def register_core_providers(
        self, stack_accessor: Callable[[], "View | None"]
    ) -> None:
        """Install renderer, viewport, and pyramid diagnostics providers.

        Args:
            stack_accessor: Callable that returns the active :class:`View` or
                ``None`` when the QPane is not yet initialized. The accessor is
                used to resolve presenter-owned collaborators instead of
                reaching into the QPane directly.
        """
        self._stack_accessor = stack_accessor
        if self._core_provider is None:
            self._core_provider = self._build_core_provider()
        self._register_once(
            self._core_provider,
            domain="render",
            tier=_CORE_TIER,
        )

    def register_executor_providers(
        self,
        executor_accessor: Callable[[], "TaskExecutorProtocol | None"],
        retry_provider: Callable[["QPane"], Iterable[DiagnosticRecord]],
        retry_summary_provider: Callable[["QPane"], Iterable[DiagnosticRecord]],
    ) -> None:
        """Install executor utilisation and retry diagnostics providers.

        Args:
            executor_accessor: Callable that yields the shared executor.
            retry_provider: Callable that emits retry metrics for the active QPane.
            retry_summary_provider: Callable that emits summary retry metrics for
                the active QPane.
        """
        self._executor_accessor = executor_accessor
        if self._executor_summary_provider is None:
            self._executor_summary_provider = self._build_executor_summary_provider()
            self._register_once(
                self._executor_summary_provider,
                domain="executor",
                tier=_DETAIL_TIER,
            )
        if self._executor_detail_provider is None:
            self._executor_detail_provider = self._build_executor_provider()
            self._register_once(
                self._executor_detail_provider,
                domain="executor",
                tier=_DETAIL_TIER,
            )
        if self._retry_detail_provider is None:
            self._retry_detail_provider = retry_provider
            self._register_once(
                retry_provider,
                domain="retry",
                tier=_DETAIL_TIER,
            )
        self._register_once(
            retry_summary_provider,
            domain="retry",
            tier=_DETAIL_TIER,
        )

    def register_cache_providers(
        self, cache_provider: DiagnosticsProvider, *, tier: str = _DETAIL_TIER
    ) -> None:
        """Attach cache diagnostics without double-registration."""
        self._register_once(cache_provider, domain="cache", tier=tier)

    def register_swap_providers(
        self, swap_provider: DiagnosticsProvider, *, tier: str = _DETAIL_TIER
    ) -> None:
        """Attach swap diagnostics without double-registration."""
        self._register_once(swap_provider, domain="swap", tier=tier)

    def register_mask_providers(
        self, mask_provider: DiagnosticsProvider, *, tier: str = _DETAIL_TIER
    ) -> None:
        """Attach mask diagnostics without double-registration."""
        self._register_once(mask_provider, domain="mask", tier=tier)

    def gather(self) -> DiagnosticsSnapshot:
        """Collect diagnostics from all registered providers.

        Side effects:
            Emits ``diagnosticsUpdated`` after the snapshot is gathered.
        """
        return self.cached_snapshot(force=True)

    def providers(self) -> tuple[DiagnosticsProvider, ...]:
        """Expose the registered providers for testing hooks."""
        return tuple(entry.raw_provider for entry in self._providers)

    def _should_refresh(self) -> bool:
        """Return True when diagnostics should be gathered again."""
        if self._cached_snapshot is None:
            return True
        if self._dirty_domains:
            return True
        return False

    def _collect_snapshot(self, *, force: bool = False) -> DiagnosticsSnapshot:
        """Gather diagnostics and broadcast the refreshed snapshot."""
        previous_force = self._force_refresh
        self._force_refresh = force
        try:
            snapshot = self._registry.gather()
        finally:
            self._force_refresh = previous_force
        self._cached_snapshot = snapshot
        self._dirty_domains.clear()
        self.diagnosticsUpdated.emit(snapshot)
        return snapshot

    def _schedule_dirty_refresh(self) -> None:
        """Coalesce dirty updates so providers refresh off the GUI thread."""
        timer = self._dirty_refresh_timer
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(50)
            timer.timeout.connect(self._handle_dirty_timeout)
            self._dirty_refresh_timer = timer
        self._invoke_on_main(timer.start)

    def _handle_dirty_timeout(self) -> None:
        """Refresh diagnostics after the dirty debounce expires."""
        self.cached_snapshot(force=False)

    # Internal helpers

    def _register_once(
        self,
        provider: DiagnosticsProvider,
        *,
        domain: str,
        tier: str,
        priority: int | None = None,
    ) -> None:
        """Register ``provider`` unless it is already known to the broker."""
        if provider in self._registered:
            return
        self._registered.add(provider)
        computed_priority = (
            priority if priority is not None else self._priority_for(domain, tier)
        )

        def _wrapped(qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
            """Invoke provider with caching and optional detail-tier gating."""
            if tier == _DETAIL_TIER and not self.domain_detail_enabled(domain):
                return tuple()
            should_refresh = (
                self._force_refresh
                or provider not in self._provider_rows
                or domain in self._dirty_domains
            )
            if not should_refresh:
                return self._provider_rows.get(provider, tuple())
            rows = tuple(provider(qpane))
            self._provider_rows[provider] = rows
            return rows

        self._providers.append(
            _DiagnosticsProviderEntry(
                raw_provider=provider,
                wrapped_provider=_wrapped,
                domain=domain,
                tier=tier,
                priority=computed_priority,
            )
        )
        self._providers.sort(key=lambda entry: entry.priority)
        self._registry.register(_wrapped, priority=computed_priority)

    def _invoke_on_main(self, callback: Callable[[], None]) -> None:
        """Execute callback on the Qt main thread if invoked from a worker."""
        app = QCoreApplication.instance()
        main_thread = app.thread() if app else None
        if main_thread is None or QThread.currentThread() == main_thread:
            try:
                callback()
            except Exception:
                logger.warning(
                    "Diagnostics callback failed on main thread",
                    exc_info=True,
                )
            return
        try:
            QTimer.singleShot(0, self, callback)
            return
        except Exception:  # pragma: no cover - defensive guard
            logger.warning(
                "Failed to schedule diagnostics callback on Diagnostics QObject; retrying with application",
                exc_info=True,
            )
            try:
                if app is not None:
                    QTimer.singleShot(0, app, callback)
                    return
            except Exception:
                logger.warning(
                    "Failed to schedule diagnostics callback on application instance",
                    exc_info=True,
                )
        try:
            callback()
        except Exception:
            logger.warning(
                "Diagnostics callback failed after scheduling fallbacks",
                exc_info=True,
            )

    def _priority_for(self, domain: str, tier: str) -> int:
        """Return the priority slot for a domain/tier pair."""
        return _DOMAIN_PRIORITIES.get(
            (domain, tier), _DEFAULT_PRIORITIES.get(tier, 100)
        )

    def _build_core_provider(self) -> DiagnosticsProvider:
        """Return a provider that reports renderer, viewport, tile, and pyramid diagnostics."""

        def _provider(_qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
            """Collect renderer, viewport, tile, and pyramid diagnostics."""
            accessor = self._stack_accessor
            if accessor is None:
                if not self._presenter_missing_logged:
                    logger.warning(
                        "Diagnostics core provider skipping because view accessor is unavailable"
                    )
                    self._presenter_missing_logged = True
                return tuple()
            view = accessor()
            presenter = getattr(view, "presenter", None) if view is not None else None
            if presenter is None:
                if not self._presenter_missing_logged:
                    logger.warning(
                        "Diagnostics core provider skipping because presenter is unavailable"
                    )
                    self._presenter_missing_logged = True
                return tuple()
            renderer = getattr(presenter, "renderer", None)
            viewport = getattr(presenter, "viewport", None)
            tile_manager = getattr(presenter, "tile_manager", None)
            cache_snapshot = None
            try:
                coordinator = getattr(self._qpane, "cacheCoordinator", None)
                cache_snapshot = (
                    coordinator.snapshot() if coordinator is not None else None
                )
            except Exception:
                logger.debug(
                    "Cache coordinator snapshot failed during core diagnostics",
                    exc_info=True,
                )
            try:
                catalog = _qpane.catalog()
            except AttributeError:
                catalog = None
            pyramid_manager = None
            if catalog is not None:
                pyramid_accessor = getattr(catalog, "pyramidManager", None)
                if callable(pyramid_accessor):
                    pyramid_manager = pyramid_accessor()
            return build_core_diagnostics(
                renderer=renderer,
                viewport=viewport,
                tile_manager=tile_manager,
                pyramid_manager=pyramid_manager,
                cache_snapshot=cache_snapshot,
            )

        return _provider

    def _build_accessor_provider(
        self,
        accessor_attr: str,
        provider_fn: Callable[[object], Iterable[DiagnosticRecord]],
    ) -> DiagnosticsProvider:
        """Return a provider that fetches a collaborator via an accessor."""

        def _provider(_qpane: "QPane") -> tuple[DiagnosticRecord, ...]:
            """Collect diagnostics for the collaborator returned by the accessor."""
            accessor = getattr(self, accessor_attr)
            if accessor is None:
                return tuple()
            target = accessor()
            if target is None:
                return tuple()
            return tuple(provider_fn(target))

        return _provider

    def _build_executor_provider(self) -> DiagnosticsProvider:
        """Return a provider that emits executor metrics via the accessor."""
        from ..concurrency import executor_diagnostics_provider

        return self._build_accessor_provider(
            "_executor_accessor", executor_diagnostics_provider
        )

    def _build_executor_summary_provider(self) -> DiagnosticsProvider:
        """Return a provider that emits executor summary metrics via the accessor."""
        from ..concurrency import executor_summary_provider

        return self._build_accessor_provider(
            "_executor_accessor", executor_summary_provider
        )
