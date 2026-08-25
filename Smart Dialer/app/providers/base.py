"""
Telecom Provider abstraction layer.

The dialer depends on this interface, not on provider-specific implementations.
This allows us to swap providers and test with different failure modes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Callable
from datetime import datetime


@dataclass
class ProviderInitiateCallRequest:
    """Request to initiate an outbound call."""
    campaign_id: str
    agent_id: str
    borrower_id: str
    borrower_phone: str
    call_id: str


@dataclass
class ProviderCallEvent:
    """Event from the telecom provider about a call."""
    provider_call_id: str
    call_id: str  # our internal call ID
    event_type: str  # "RINGING", "ANSWERED", "COMPLETED", "FAILED"
    timestamp: datetime
    failure_reason: Optional[str] = None


class TelecomProvider(ABC):
    """
    Abstract base class for telecom providers.
    
    Implementations must handle:
    - Initiating outbound calls
    - Generating asynchronous call events
    - Handling failures gracefully
    - Provider health tracking
    """

    @abstractmethod
    async def initiate_call(
        self,
        request: ProviderInitiateCallRequest,
    ) -> str:
        """
        Initiate an outbound call to a borrower.
        
        Args:
            request: Details about the call to initiate
            
        Returns:
            provider_call_id: The provider's unique ID for this call
            
        Raises:
            ProviderException: If the call cannot be initiated
        """
        pass

    @abstractmethod
    async def is_healthy(self) -> bool:
        """
        Check if the provider is currently healthy and accepting calls.
        
        Returns:
            True if provider is healthy, False if degraded/down
        """
        pass

    @abstractmethod
    def on_event(
        self,
        callback: Callable[[ProviderCallEvent], None],
    ) -> None:
        """
        Register a callback to receive provider events.
        
        The callback will be called asynchronously when the provider
        generates events (RINGING, ANSWERED, COMPLETED, FAILED).
        
        Args:
            callback: Async callable that receives ProviderCallEvent
        """
        pass


class ProviderException(Exception):
    """Base exception for provider-related errors."""
    pass


class ProviderTimeoutException(ProviderException):
    """Provider did not respond in time."""
    pass


class ProviderHealthException(ProviderException):
    """Provider is unhealthy and cannot accept calls."""
    pass
