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

"""Cache consumer adapters that surface usage to the coordinator."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from .coordinator import CacheConsumerCallbacks, CacheCoordinator, CachePriority

logger = logging.getLogger(__name__)

_MISSING_HOOK_LOGS: set[tuple[type, str]] = set()


class _BudgetedCacheConsumer:
    """Shared plumbing for cache consumers with soft budgets and batch trims."""

    def __init__(
        self,
        manager: Any,
        coordinator: CacheCoordinator,
        *,
        consumer_id: str,
        priority: CachePriority,
        usage_label: str,
        limit_label: str,
        trim_target_label: str,
        batch_hook: str,
        marker_attr: str,
        missing_batch_label: str,
        warn_message: str,
        pre_trim: Callable[[], None] | None = None,
    ) -> None:
        """Register manager cache signals and budgets with the shared coordinator."""
        self._manager = manager
        self._coordinator = coordinator
        self._consumer_id = consumer_id
        self._usage_label = usage_label
        self._limit_label = limit_label
        self._trim_target_label = trim_target_label
        self._batch_hook = batch_hook
        self._marker_attr = marker_attr
        self._missing_batch_label = missing_batch_label
        self._warn_message = warn_message
        self._pre_trim = pre_trim
        callbacks = CacheConsumerCallbacks(
            get_usage=self._get_usage,
            set_budget=self._set_budget,
            trim_to=self._trim_to,
        )
        coordinator.register_consumer(
            consumer_id,
            priority=priority,
            callbacks=callbacks,
            preferred_bytes=self._manager.cache_limit_bytes,
        )
        self._manager.set_managed_mode(True)
        _install_admission_guard(self._manager, coordinator.should_admit)
        coordinator.set_consumer_preferred(
            consumer_id,
            _safe_int(
                getattr(self._manager, "cache_limit_bytes", 0),
                label=self._limit_label,
            ),
        )
        self._connect_signals()

    def _update_preferred_budget(self, new_limit: int | None = None) -> None:
        """Refresh the preferred budget after config changes apply."""
        self._coordinator.set_consumer_preferred(
            self._consumer_id,
            _safe_int(
                (
                    new_limit
                    if new_limit is not None
                    else getattr(self._manager, "cache_limit_bytes", 0)
                ),
                label=self._limit_label,
            ),
        )

    def _connect_signals(self) -> None:
        """Subscribe to manager signals to track cache usage and budgets."""
        usage_signal = getattr(self._manager, "usageChanged", None)
        limit_signal = getattr(self._manager, "cacheLimitChanged", None)
        if usage_signal is None:
            logger.error(
                "%s missing usageChanged signal; cannot track cache usage",
                type(self._manager).__name__,
            )
            raise RuntimeError("Manager missing usageChanged signal")
        if limit_signal is None:
            logger.error(
                "%s missing cacheLimitChanged signal; cannot track budgets",
                type(self._manager).__name__,
            )
            raise RuntimeError("Manager missing cacheLimitChanged signal")
        try:
            usage_signal.connect(self._notify)
        except Exception:
            logger.exception("Failed to connect usageChanged for %s", self._consumer_id)
            raise
        try:
            limit_signal.connect(self._update_preferred_budget)
        except Exception:
            logger.exception(
                "Failed to connect cacheLimitChanged for %s", self._consumer_id
            )
            raise

    def _get_usage(self) -> int:
        """Return the current cache usage in bytes."""
        usage_getter = getattr(self._manager, "cache_usage_bytes", None)
        if usage_getter is None:
            logger.error(
                "%s manager missing cache_usage_bytes; cannot report cache usage",
                type(self._manager).__name__,
            )
            raise RuntimeError("cache_usage_bytes missing for cache consumer")
        try:
            return _safe_int(
                usage_getter() if callable(usage_getter) else usage_getter,
                label=self._usage_label,
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "%s manager failed to report cache usage",
                type(self._manager).__name__,
            )
            raise

    def _set_budget(self, target_bytes: int) -> None:
        """Apply ``target_bytes`` as the cache limit."""
        self._manager.cache_limit_bytes = _safe_int(
            target_bytes,
            label=self._limit_label,
        )

    def _trim_to(self, target_bytes: int) -> None:
        """Attempt to shrink usage to ``target_bytes`` and warn if it fails."""
        target = _safe_int(target_bytes, label=self._trim_target_label)
        self._manager.cache_limit_bytes = min(self._manager.cache_limit_bytes, target)
        if self._pre_trim is not None:
            try:
                self._pre_trim()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Cache pre-trim hook failed", exc_info=True)
        _run_cache_batch_trim(
            consumer_id=self._consumer_id,
            get_usage=self._get_usage,
            batch=getattr(self._manager, self._batch_hook, None),
            target=target,
            marker=getattr(self._manager, self._marker_attr, None),
            missing_hook_label=self._missing_batch_label,
            warn_message=self._warn_message,
        )
        self._notify()

    def _notify(self) -> None:
        """Publish cache usage to the coordinator."""
        usage = self._get_usage()
        self._coordinator.update_usage(self._consumer_id, usage)


class SamPredictorCacheConsumer:
    """Reports SAM predictor cache usage to the shared coordinator."""

    def __init__(
        self,
        manager: Any,
        coordinator: CacheCoordinator,
        *,
        consumer_id: str = "predictors",
        priority: CachePriority = CachePriority.PREDICTORS,
    ) -> None:
        """Wire the SAM manager into cache coordination.

        Args:
            manager: SAM manager providing predictor lifecycle hooks.
            coordinator: Shared cache coordinator that enforces budgets.
            consumer_id: Diagnostics identifier exposed in trim logs.
            priority: Trim priority relative to other consumers.
        """
        self._manager = manager
        self._coordinator = coordinator
        self._consumer_id = consumer_id
        callbacks = CacheConsumerCallbacks(
            get_usage=self._get_usage,
            set_budget=self._set_budget,
            trim_to=self._trim_to,
        )
        coordinator.register_consumer(
            consumer_id,
            priority=priority,
            callbacks=callbacks,
            preferred_bytes=None,
        )
        _install_admission_guard(self._manager, coordinator.should_admit)
        self._wrap_manager_hooks()

    def _wrap_manager_hooks(self) -> None:
        """Wrap manager hooks so predictor usage stays synchronized."""
        _wrap_manager_hook(
            self._manager,
            "requestPredictor",
            log_message="SAM predictor request hook failed",
            after_success=lambda _result, _args, _kwargs: self._notify(),
        )

        def _cleanup_cancel(
            _result: Any, args: tuple[Any, ...], _kwargs: dict[str, Any]
        ) -> None:
            """Drop predictor bookkeeping when cancellations succeed."""
            if not args:
                return
            self._notify()

        _wrap_manager_hook(
            self._manager,
            "cancelPendingPredictor",
            log_message="SAM predictor cancel hook failed",
            after_success=_cleanup_cancel,
        )
        try:
            self._manager.predictorReady.connect(self._on_predictor_ready)
            self._manager.predictorCacheCleared.connect(self._on_cache_cleared)
            self._manager.predictorRemoved.connect(self._on_predictor_removed)
        except AttributeError:
            logger.error("SAM manager missing required predictor signals")
            raise
        except Exception:  # pragma: no cover - defensive guard
            logger.warning(
                "SAM predictor signal wiring failed; trims and diagnostics may drift",
                exc_info=True,
            )

    def _on_predictor_ready(self, predictor, image_id):  # noqa: ANN001
        """Record predictor estimates once the manager signals readiness."""
        self._notify()

    def _on_cache_cleared(self) -> None:
        """Reset predictor accounting when the manager clears its cache."""
        self._coordinator.update_usage(self._consumer_id, 0)

    def _on_predictor_removed(self, image_id: uuid.UUID) -> None:
        """Drop bookkeeping for predictors removed externally (e.g., limit trims)."""
        self._notify()

    def _get_usage(self) -> int:
        """Return the predictor usage including pending estimates in bytes."""
        usage_getter = getattr(self._manager, "cache_usage_bytes", None)
        if usage_getter is None:
            logger.error(
                "SAM predictor manager missing cache_usage_bytes; cannot report usage"
            )
            raise RuntimeError("cache_usage_bytes missing for SAM cache consumer")
        pending_getter = getattr(self._manager, "pendingUsageBytes", None)
        if pending_getter is None:
            logger.error(
                "SAM predictor manager missing pendingUsageBytes; cannot report usage"
            )
            raise RuntimeError("pendingUsageBytes missing for SAM cache consumer")
        try:
            manager_usage = _safe_int(
                usage_getter() if callable(usage_getter) else usage_getter,
                label="sam_cache_usage_bytes",
            )
            pending_usage = _safe_int(
                pending_getter() if callable(pending_getter) else pending_getter,
                label="sam_pending_usage_bytes",
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("SAM predictor usage hooks failed")
            raise
        return manager_usage + pending_usage

    def _set_budget(self, target_bytes: int) -> None:  # noqa: ARG002
        """SAM predictors lack a budget knob; trims drive enforcement."""
        return

    def _trim_to(self, target_bytes: int) -> None:
        """Best-effort eviction of cached predictors down to ``target_bytes``."""
        target = _safe_int(target_bytes, label="sam_trim_target")
        usage = self._get_usage()
        if usage <= target:
            return
        drop = getattr(self._manager, "removeFromCache", None)
        if not callable(drop):
            logger.error(
                "SAM predictor manager missing removeFromCache; cannot enforce trims"
            )
            raise RuntimeError("removeFromCache missing for SAM cache consumer")
        id_accessor = getattr(self._manager, "predictorImageIds", None)
        if not callable(id_accessor):
            logger.error(
                "SAM predictor manager missing predictorImageIds; cannot enumerate cache keys"
            )
            raise RuntimeError("predictorImageIds missing for SAM cache consumer")
        for image_id in id_accessor():
            if usage <= target:
                break
            if drop(image_id):
                usage = self._get_usage()
        usage = max(usage, 0)
        if usage > target:
            logger.warning(
                "SAM predictor cache failed to trim below target | consumer=%s | "
                "usage=%d | target=%d",
                self._consumer_id,
                usage,
                target,
            )
        self._coordinator.update_usage(self._consumer_id, usage)

    def _notify(self) -> None:
        """Push the current predictor usage to the coordinator."""
        self._coordinator.update_usage(self._consumer_id, self._get_usage())


class MaskOverlayCacheConsumer:
    """Reports mask overlay cache usage through a :class:`MaskController`."""

    def __init__(
        self,
        controller: Any,
        coordinator: CacheCoordinator,
        *,
        consumer_id: str = "mask_overlays",
        priority: CachePriority = CachePriority.MASK_OVERLAYS,
    ) -> None:
        """Register the mask controller with the coordinator.

        Args:
            controller: Mask controller providing cache hooks.
            coordinator: Shared cache coordinator.
            consumer_id: Diagnostics identifier exposed in trim logs.
            priority: Trim priority relative to other consumers.
        """
        self._controller = controller
        self._coordinator = coordinator
        self._consumer_id = consumer_id
        callbacks = CacheConsumerCallbacks(
            get_usage=self._get_usage,
            set_budget=self._set_budget,
            trim_to=self._trim_to,
        )
        coordinator.register_consumer(
            consumer_id,
            priority=priority,
            callbacks=callbacks,
            preferred_bytes=None,
        )
        _install_admission_guard(self._controller, coordinator.should_admit)
        setter = getattr(controller, "set_cache_usage_callback", None)
        if callable(setter):
            try:
                setter(self._notify)
            except Exception:  # pragma: no cover - defensive
                logger.exception("Failed to install mask cache usage callback")
        else:
            logger.warning(
                "Mask controller missing set_cache_usage_callback; cache usage updates "
                "may be delayed"
            )
        self._notify()

    def _get_usage(self) -> int:
        """Estimate the number of bytes consumed by cached mask overlays."""
        usage_attr = getattr(self._controller, "cache_usage_bytes", None)
        if usage_attr is None:
            logger.error(
                "Mask controller missing cache_usage_bytes; cannot report mask usage"
            )
            raise RuntimeError("cache_usage_bytes missing for mask cache consumer")
        try:
            return _safe_int(
                usage_attr() if callable(usage_attr) else usage_attr,
                label="mask_cache_usage_bytes",
            )
        except Exception:  # pragma: no cover - defensive
            logger.exception("Mask controller failed to report cache usage")
            raise

    def _set_budget(self, target_bytes: int) -> None:  # noqa: ARG002
        """Mask overlays lack a budget knob; trims enforce the limit."""
        return

    def _trim_to(self, target_bytes: int) -> None:
        """Best-effort reduction of overlay cache usage to ``target_bytes``."""
        target = _safe_int(target_bytes, label="mask_trim_target")
        usage = self._get_usage()
        if usage <= target:
            return
        drop = getattr(self._controller, "drop_oldest_cached_mask", None)
        if not callable(drop):
            logger.error(
                "Mask controller missing drop_oldest_cached_mask; cannot trim mask cache"
            )
            raise RuntimeError(
                "drop_oldest_cached_mask missing for mask cache consumer"
            )
        active_accessor = getattr(self._controller, "get_active_mask_id", None)
        active_mask_id = None
        if callable(active_accessor):
            try:
                active_mask_id = active_accessor()
            except Exception:  # pragma: no cover - defensive
                active_mask_id = None
        exclude = {active_mask_id} if active_mask_id else set()
        while usage > target:
            freed = _safe_int(
                drop(
                    reason="coordinator",
                    exclude=exclude or None,
                ),
                label="mask_trim_freed",
            )
            if freed <= 0:
                break
            usage = max(0, usage - freed)
        if usage > target:
            logger.warning(
                "Mask cache failed to trim below target | consumer=%s | usage=%d | "
                "target=%d",
                self._consumer_id,
                usage,
                target,
            )
        self._notify()

    def _notify(self) -> None:
        """Publish overlay cache usage to the coordinator."""
        self._coordinator.update_usage(self._consumer_id, self._get_usage())


class TileCacheConsumer(_BudgetedCacheConsumer):
    """Coordinates a :class:`TileManager` with the cache coordinator."""

    def __init__(
        self,
        manager: Any,
        coordinator: CacheCoordinator,
        *,
        consumer_id: str = "tiles",
        priority: CachePriority = CachePriority.TILES,
    ) -> None:
        """Register ``manager`` with ``coordinator`` and wrap cache hooks.

        Args:
            manager: Tile manager exposing cache hooks and metrics.
            coordinator: Shared cache coordinator.
            consumer_id: Diagnostics identifier exposed in trim logs.
            priority: Trim priority relative to other consumers.
        """
        super().__init__(
            manager,
            coordinator,
            consumer_id=consumer_id,
            priority=priority,
            usage_label="tile_cache_usage_bytes",
            limit_label="tile_cache_limit_bytes",
            trim_target_label="tile_trim_target",
            batch_hook="_evict_cache_batch",
            marker_attr="mark_external_trim",
            missing_batch_label="tile _evict_cache_batch",
            warn_message=(
                "Tile cache failed to trim below target | consumer=%s | usage=%d | "
                "target=%d | attempts=%d"
            ),
        )


class PyramidCacheConsumer(_BudgetedCacheConsumer):
    """Coordinates a :class:`PyramidManager` with the cache coordinator."""

    def __init__(
        self,
        manager: Any,
        coordinator: CacheCoordinator,
        *,
        consumer_id: str = "pyramids",
        priority: CachePriority = CachePriority.PYRAMIDS,
    ) -> None:
        """Register ``manager`` with ``coordinator`` and wrap cache hooks.

        Args:
            manager: Pyramid manager exposing cache hooks and metrics.
            coordinator: Shared cache coordinator.
            consumer_id: Diagnostics identifier exposed in trim logs.
            priority: Trim priority relative to other consumers.
        """
        super().__init__(
            manager,
            coordinator,
            consumer_id=consumer_id,
            priority=priority,
            usage_label="pyramid_cache_usage_bytes",
            limit_label="pyramid_cache_limit_bytes",
            trim_target_label="pyramid_trim_target",
            batch_hook="_run_eviction_batch",
            marker_attr="mark_external_trim",
            missing_batch_label="pyramid _run_eviction_batch",
            warn_message=(
                "Pyramid cache failed to trim below target | consumer=%s | "
                "usage=%d | target=%d | attempts=%d"
            ),
        )
        self._manager.set_managed_mode(True)


def _wrap_manager_hook(
    manager: Any,
    attr_name: str,
    *,
    log_message: str,
    after_success: "Callable[[Any, tuple[Any, ...], dict[str, Any]], None] | None" = None,
    after_finally: "Callable[[], None] | None" = None,
) -> None:
    """Wrap a manager method with logging and notification hooks.

    Args:
        manager: Object that owns the target method.
        attr_name: Name of the method to wrap.
        log_message: Message logged if the wrapped call raises.
        after_success: Optional callback invoked with the result/args after a
            successful call.
        after_finally: Optional zero-arg callback executed in a ``finally``
            block regardless of success.

    Side effects:
        Rebinds ``manager.attr_name`` to the generated wrapper.

    Raises:
        RuntimeError: When the requested hook is missing.
    """
    original = getattr(manager, attr_name, None)
    if not callable(original):
        key = (type(manager), attr_name)
        if key not in _MISSING_HOOK_LOGS:
            logger.error(
                "Cannot wrap missing manager hook %s.%s",
                type(manager).__name__,
                attr_name,
            )
            _MISSING_HOOK_LOGS.add(key)
        raise RuntimeError(
            f"Missing required manager hook {type(manager).__name__}.{attr_name}"
        )

    def _wrapper(*args, **kwargs):
        """Forward to the original method while injecting instrumentation."""
        try:
            result = original(*args, **kwargs)
        except Exception:  # pragma: no cover - defensive logging
            logger.exception(log_message)
            raise
        else:
            if after_success is not None:
                after_success(result, args, kwargs)
            return result
        finally:
            if after_finally is not None:
                after_finally()

    setattr(manager, attr_name, _wrapper)


def _install_admission_guard(manager: Any, guard: Callable[[int], bool] | None) -> None:
    """Attach ``guard`` to ``manager`` when it advertises setter support."""
    setter = getattr(manager, "set_admission_guard", None)
    if not callable(setter):
        return
    try:
        setter(guard)
    except Exception:  # pragma: no cover - defensive
        logger.debug("Admission guard install failed for %s", type(manager).__name__)


def _run_cache_batch_trim(
    *,
    consumer_id: str,
    get_usage: Callable[[], int],
    batch: Callable[[], object] | None,
    target: int,
    marker: Callable[[str], None] | None,
    missing_hook_label: str,
    warn_message: str,
    max_attempts: int = 8,
) -> int:
    """Run cache-eviction batches while preserving existing trim semantics.

    Args:
        consumer_id: Registered cache identifier used in logs.
        get_usage: Callable that returns the current cache usage in bytes.
        batch: Hook that evicts a batch of cache entries when callable.
        target: Desired usage floor in bytes.
        marker: Optional hook used to tag externally initiated trims.
        missing_hook_label: Label describing the required batch hook for logs.
        warn_message: Format string logged when trims cannot reach the target.
        max_attempts: Maximum number of batch calls before giving up.

    Returns:
        Final usage reported by ``get_usage`` after batch trims complete.

    Raises:
        RuntimeError: When the required batch hook is missing.
    """
    usage = get_usage()
    if usage <= target:
        return usage
    if marker is not None:
        try:
            marker("coordinator")
        except Exception:  # pragma: no cover - defensive guard
            logger.debug("Cache trim marker failed for %s", consumer_id, exc_info=True)
    if not callable(batch):
        logger.error(
            "Cannot trim cache for consumer %s; missing batch hook %s",
            consumer_id,
            missing_hook_label,
        )
        raise RuntimeError(f"Missing cache trim hook {missing_hook_label}")
    attempts = 0
    while usage > target and attempts < max_attempts:
        batch()
        attempts += 1
        usage = get_usage()
    if usage > target:
        logger.warning(
            warn_message,
            consumer_id,
            usage,
            target,
            attempts,
        )
    return usage


_INVALID_VALUE_LOGGED: set[str] = set()


def _safe_int(value: int | float, *, label: str | None = None) -> int:
    """Clamp the provided value to a non-negative integer."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        key = label or "cache_value"
        if key not in _INVALID_VALUE_LOGGED:
            logger.warning(
                "Invalid cache metric; defaulting to zero | label=%s | value=%r",
                key,
                value,
            )
            _INVALID_VALUE_LOGGED.add(key)
        return 0
