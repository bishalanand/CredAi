"""
MockProviderB: Slow, unreliable, generates problematic events.

Simulates a degraded telecom provider:
- 70% success rate for call initiation
- Occasional timeouts
- Duplicate events (same event twice)
- Out-of-order events (COMPLETED before ANSWERED)
- Slow event generation
"""

import asyncio
import random
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional
import logging

from .base import (
    TelecomProvider,
    ProviderInitiateCallRequest,
    ProviderCallEvent,
    ProviderException,
    ProviderTimeoutException,
)

logger = logging.getLogger(__name__)


class MockProviderB(TelecomProvider):
    """
    Low-quality provider simulation to test robustness.
    
    - 70% success rate for call initiation
    - Slow event generation
    - Duplicate events (20% of calls get duplicate ANSWERED)
    - Out-of-order events (10% get COMPLETED before ANSWERED)
    - Health degradation/recovery
    """

    def __init__(
        self,
        answer_rate: float = 0.50,
        avg_setup_time_ms: float = 100.0,
        avg_ring_time_ms: float = 5000.0,
        failure_rate: float = 0.30,
        duplicate_event_rate: float = 0.20,
        out_of_order_rate: float = 0.10,
    ):
        """
        Args:
            answer_rate: Probability borrower answers
            avg_setup_time_ms: Slower setup time
            avg_ring_time_ms: Longer ring time
            failure_rate: Higher failure rate
            duplicate_event_rate: 20% of calls get duplicate ANSWERED
            out_of_order_rate: 10% of calls get COMPLETED before ANSWERED
        """
        self.answer_rate = answer_rate
        self.avg_setup_time_ms = avg_setup_time_ms
        self.avg_ring_time_ms = avg_ring_time_ms
        self.failure_rate = failure_rate
        self.duplicate_event_rate = duplicate_event_rate
        self.out_of_order_rate = out_of_order_rate
        
        self._healthy = True
        self._event_callback: Optional[Callable] = None
        self._active_calls: Dict[str, bool] = {}
        self._failure_count = 0
        self._health_check_threshold = 5  # After 5 failures, go unhealthy

    async def initiate_call(
        self,
        request: ProviderInitiateCallRequest,
    ) -> str:
        """
        Simulate initiating a call with occasional failures.
        """
        
        # Simulate random provider failures
        if random.random() < 0.30:  # 30% failure rate
            self._failure_count += 1
            if self._failure_count >= self._health_check_threshold:
                self._healthy = False
                logger.warning(f"MockProviderB degraded after {self._failure_count} failures")
                await asyncio.sleep(2)  # Simulate recovery time
                self._healthy = True
                self._failure_count = 0
            raise ProviderTimeoutException("Provider timeout")

        # Simulate slow call initiation
        setup_delay = random.gauss(self.avg_setup_time_ms, 20) / 1000.0
        await asyncio.sleep(max(0.001, setup_delay))

        provider_call_id = str(uuid.uuid4())
        self._active_calls[provider_call_id] = True

        # Spawn async task to simulate call lifecycle
        asyncio.create_task(
            self._simulate_call_lifecycle(
                provider_call_id=provider_call_id,
                call_id=request.call_id,
                borrower_phone=request.borrower_phone,
            )
        )

        return provider_call_id

    async def is_healthy(self) -> bool:
        """Provider health degrades after multiple failures."""
        return self._healthy

    def on_event(self, callback: Callable[[ProviderCallEvent], None]) -> None:
        """Register event callback."""
        self._event_callback = callback

    async def _simulate_call_lifecycle(
        self,
        provider_call_id: str,
        call_id: str,
        borrower_phone: str,
    ) -> None:
        """
        Simulate call lifecycle with problematic event patterns.
        
        Features:
        - Out-of-order events (COMPLETED before ANSWERED)
        - Duplicate events
        - Slow event generation
        """

        try:
            # Slower RINGING event
            ring_delay = random.gauss(self.avg_setup_time_ms, 30) / 1000.0
            await asyncio.sleep(max(0.001, ring_delay))

            if self._event_callback:
                self._event_callback(
                    ProviderCallEvent(
                        provider_call_id=provider_call_id,
                        call_id=call_id,
                        event_type="RINGING",
                        timestamp=datetime.now(timezone.utc),
                    )
                )

            # Decide call outcome
            if random.random() < self.failure_rate:
                # Call fails
                await asyncio.sleep(random.gauss(self.avg_ring_time_ms, 1000) / 1000.0)
                
                if self._event_callback:
                    self._event_callback(
                        ProviderCallEvent(
                            provider_call_id=provider_call_id,
                            call_id=call_id,
                            event_type="FAILED",
                            timestamp=datetime.now(timezone.utc),
                            failure_reason="No answer (timeout)",
                        )
                    )
            elif random.random() < self.answer_rate:
                # Borrower answers - but might send out-of-order events
                ring_duration = random.gauss(self.avg_ring_time_ms, 1000) / 1000.0
                await asyncio.sleep(max(0.001, ring_duration))

                # Check for out-of-order event
                if random.random() < self.out_of_order_rate:
                    # Send COMPLETED before ANSWERED (bad provider!)
                    if self._event_callback:
                        self._event_callback(
                            ProviderCallEvent(
                                provider_call_id=provider_call_id,
                                call_id=call_id,
                                event_type="COMPLETED",
                                timestamp=datetime.now(timezone.utc),
                            )
                        )
                    
                    # Then send ANSWERED (out of order)
                    await asyncio.sleep(0.1)
                    if self._event_callback:
                        self._event_callback(
                            ProviderCallEvent(
                                provider_call_id=provider_call_id,
                                call_id=call_id,
                                event_type="ANSWERED",
                                timestamp=datetime.now(timezone.utc),
                            )
                        )
                else:
                    # Normal sequence: ANSWERED then COMPLETED
                    if self._event_callback:
                        self._event_callback(
                            ProviderCallEvent(
                                provider_call_id=provider_call_id,
                                call_id=call_id,
                                event_type="ANSWERED",
                                timestamp=datetime.now(timezone.utc),
                            )
                        )

                    # Check for duplicate event
                    if random.random() < self.duplicate_event_rate:
                        # Send duplicate ANSWERED
                        await asyncio.sleep(0.05)
                        if self._event_callback:
                            self._event_callback(
                                ProviderCallEvent(
                                    provider_call_id=provider_call_id,
                                    call_id=call_id,
                                    event_type="ANSWERED",
                                    timestamp=datetime.now(timezone.utc),
                                )
                            )

                    # Complete call
                    await asyncio.sleep(0.5)
                    if self._event_callback:
                        self._event_callback(
                            ProviderCallEvent(
                                provider_call_id=provider_call_id,
                                call_id=call_id,
                                event_type="COMPLETED",
                                timestamp=datetime.now(timezone.utc),
                            )
                        )
            else:
                # No answer / timeout
                await asyncio.sleep(random.gauss(self.avg_ring_time_ms, 1000) / 1000.0)
                
                if self._event_callback:
                    self._event_callback(
                        ProviderCallEvent(
                            provider_call_id=provider_call_id,
                            call_id=call_id,
                            event_type="FAILED",
                            timestamp=datetime.now(timezone.utc),
                            failure_reason="No answer",
                        )
                    )

        except asyncio.CancelledError:
            logger.info(f"Call {call_id} cancelled")
        finally:
            self._active_calls.pop(provider_call_id, None)
