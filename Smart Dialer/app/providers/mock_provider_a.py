"""
MockProviderA: Fast, reliable, low failure rate.

Simulates an ideal telecom provider:
- Always healthy
- Fast call initiation (10-50ms)
- Events arrive in order
- No duplicate events
- Realistic answer rates
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
)

logger = logging.getLogger(__name__)


class MockProviderA(TelecomProvider):
    """
    High-quality provider simulation.
    
    - 95% success rate for call initiation
    - Events arrive in correct order
    - No duplicates
    - Average answer rate configurable (default 50%)
    """

    def __init__(
        self,
        answer_rate: float = 0.50,
        avg_setup_time_ms: float = 20.0,
        avg_ring_time_ms: float = 3000.0,
        failure_rate: float = 0.05,
    ):
        """
        Args:
            answer_rate: Probability borrower answers (0.0-1.0)
            avg_setup_time_ms: How long to generate RINGING event
            avg_ring_time_ms: How long call rings before ANSWERED
            failure_rate: Probability call fails (0.0-1.0)
        """
        self.answer_rate = answer_rate
        self.avg_setup_time_ms = avg_setup_time_ms
        self.avg_ring_time_ms = avg_ring_time_ms
        self.failure_rate = failure_rate
        
        self._healthy = True
        self._event_callback: Optional[Callable] = None
        self._active_calls: Dict[str, bool] = {}

    async def initiate_call(
        self,
        request: ProviderInitiateCallRequest,
    ) -> str:
        """
        Simulate initiating a call with this provider.
        
        Returns: provider_call_id
        """
        
        if not await self.is_healthy():
            raise ProviderException("Provider is unhealthy")

        # Simulate call initiation delay
        setup_delay = random.gauss(self.avg_setup_time_ms, 5) / 1000.0
        await asyncio.sleep(max(0.001, setup_delay))

        # Generate provider call ID
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
        """Provider is almost always healthy."""
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
        Simulate the lifecycle of a call.
        
        Sequence:
        1. Wait setup_time → generate RINGING event
        2. Wait ring_time → decide if borrower answers
        3. If answers: generate ANSWERED → wait → COMPLETED
           If fails: generate FAILED
        """

        try:
            # Generate RINGING event
            ring_delay = random.gauss(self.avg_setup_time_ms, 5) / 1000.0
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

            # Decide if borrower answers
            if random.random() < self.failure_rate:
                # Call fails
                await asyncio.sleep(random.gauss(self.avg_ring_time_ms, 500) / 1000.0)
                
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
            elif random.random() < self.answer_rate:
                # Borrower answers
                ring_duration = random.gauss(self.avg_ring_time_ms, 500) / 1000.0
                await asyncio.sleep(max(0.001, ring_duration))

                if self._event_callback:
                    self._event_callback(
                        ProviderCallEvent(
                            provider_call_id=provider_call_id,
                            call_id=call_id,
                            event_type="ANSWERED",
                            timestamp=datetime.now(timezone.utc),
                        )
                    )

                # Call completes (agent talks)
                # In real scenario, agent would hang up
                # For simulation, we complete after fixed time
                await asyncio.sleep(0.5)  # Short talk time for test

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
                # Timeout / no answer
                await asyncio.sleep(random.gauss(self.avg_ring_time_ms, 500) / 1000.0)
                
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

        except asyncio.CancelledError:
            logger.info(f"Call {call_id} cancelled")
        finally:
            self._active_calls.pop(provider_call_id, None)
